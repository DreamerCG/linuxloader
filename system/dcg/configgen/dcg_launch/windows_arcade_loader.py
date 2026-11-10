from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Final

from batocera_common.dataclasses import cached_dataclass, cached_property
from batocera_common.paths import CONFIGS
from batocera_launch.command import Command
from batocera_launch.devices.controller import Controller, generate_sdl_game_controller_config
from batocera_launch.devices.input import Input
from batocera_launch.emulator import Emulator
from batocera_launch.paths import SYSTEM_ES_DIR, USER_ES_DIR, configure_emulator
from batocera_launch.types import HotkeysContext
from dcg_launch.gamescope_links import ensure_gamescope_links
from dcg_launch.gamescope_options import (
    gamescope_args,
    gamescope_profile_details,
    wal_game_id,
)

# Windows arcade games (Taito Type X / X2, NESiCAxLive, ...) through the Windows arcade
# loader: arcade-launcher runs the game in Wine with its system's I/O emulated (JVS board,
# NESiCA card reader...) and maps the pads, keyboards and guns itself (SDL and evdev), as
# the game's profile says (systemprofiles/<system>/<gameid>.yaml).
#
# The rom is a game dump directory holding <gameid>.windowsloader (the executable path
# relative to the dump root): ES lists that file, the game id selects the profile. Or the
# dump packed as a .squashfs image: Batocera Launch mounts it and hands the mounted
# directory, whose .windowsloader marker gives the game ID; needs_overlayfs() gives it a
# writable overlay kept in
# /userdata/saves/<system>/<image name> (the game's save data in the dump's WindowsLoader folder).
#
# The ES options are written as an extra profile layer (--profile), merged over the game's
# profile: only the options set in ES, so a game's own setting (e.g. KOF Maximum Impact
# Regulation A's dxvk: false) stays unless the user picks another.
#
# GAMESCOPE options: the launcher starts gamescope around wine (its inputs and force feedback
# stay outside), so configgen does not wrap the command (gamescope.py SELF_GAMESCOPE); they
# go in the layer's gamescope: settings, over the game profile's (Gundam Spirits of Zeon 2
# players: 1280x480). The game resolution stays the profile's unless set in ES. Any GAMESCOPE
# option set enables it, unless GAMESCOPE ENABLE is Off.
#
# Layout of the emulator directory:
#   arcade-launcher, payloads/ (wal_*.dll, wal-loader.exe), systemprofiles/,
#   launcher.yaml (machine settings: runners_dir, payloads_dir), wine-prefix/ (created)
# The Wine runner (GE-Proton with the loader's hotfixes) is in /userdata/system/wine/custom.
#
# The GUI (applications menu, arcade-launcher-gui) comes through here too, with the rom
# "config": SDL then gets the pads' ES mappings as in games (without them, pads SDL3 does not
# know, e.g. Xbox ones without HIDAPI, are plain joysticks).

WAL_DIR: Final = Path("/userdata/system/dcg/emulators/windows-arcade-loader")
WAL_CONFIG: Final = CONFIGS / "windows-arcade-loader"

_GAMESCOPE_SWITCHES: Final = {"gamescope_hdr", "gamescope_hdr_itm_enabled"}


def _es_pads() -> list[Controller]:
    # outside a game ES passes no -pX controllers: every pad configured in ES (user file first)
    import xml.etree.ElementTree as ET

    pads: dict[str, Controller] = {}
    for conffile in (USER_ES_DIR / "es_input.cfg", SYSTEM_ES_DIR / "es_input.cfg"):
        if not conffile.exists():
            continue
        for element in ET.parse(conffile).getroot().iterfind('./inputConfig[@type="joystick"]'):
            guid = element.get('deviceGUID') or ''
            name = element.get('deviceName') or ''
            if not guid or guid in pads:
                continue
            pads[guid] = Controller(
                name=name, type='joystick', guid=guid, player_number=len(pads) + 1, index=len(pads),
                real_name=name, device_path='', button_count=0, hat_count=0, axis_count=0,
                inputs_=Input.from_parent_element(element),
            )
    return list(pads.values())


def _yaml_scalar(value: str | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value)

def _player_pads(controllers) -> str:
    # ES's player order (P1 first) by evdev node: the launcher gives the players in it, under
    # the GUI's priority list (userprofiles/players.yaml)
    return ';'.join(c.device_path for c in controllers if c.device_path)

@cached_dataclass
class WindowsArcadeLoader(Emulator):
    @cached_property
    def hotkeygen_context(self) -> HotkeysContext:
        return {
            "name": "windows-arcade-loader",
            'keys': {'exit': '/bin/pkill -TERM -x arcade-launcher', 'coin': 'KEY_5'},
        }

    @property
    def handles_bezels(self) -> bool:
        return self.config.get_bool("use_guns")

    @property
    def execution_path(self) -> Path:
        return WAL_DIR

    def needs_overlayfs(self, _rom: Path, /) -> bool:
        return True


    async def configure(self) -> Command:
        if configure_emulator(self.rom):
            controllers = list(self.controllers) or _es_pads()
            return Command(
                args=[str(WAL_DIR / "arcade-launcher"), "gui", "--root", str(WAL_DIR)],
                env={
                    "SDL_GAMECONTROLLERCONFIG": generate_sdl_game_controller_config(controllers),
                    "SDL_JOYSTICK_HIDAPI": "0",
                    'WAL_PLAYER_PADS': _player_pads(controllers),
                },
            )

        ensure_gamescope_links()
        WAL_CONFIG.mkdir(parents=True, exist_ok=True)

        guns = list(self.guns)
        top: dict[str, str] = {}
        input_keys: dict[str, str] = {"exit_combo": "[]"}
        #input_keys: dict[str, str] = {}
        
        reshade = self.config.get("wal_reshade")
        if reshade in ("0", "1"):
            top["reshade"] = _yaml_scalar(reshade == "1")

        renderer = self.config.get("wal_renderer")
        if renderer in ("dxvk", "wined3d"):
            top["dxvk"] = _yaml_scalar(renderer == "dxvk")

        mouse_guns = self.config.get("wal_mouse_guns")
        if mouse_guns in ("0", "1"):
            input_keys["guns_mouse"] = _yaml_scalar(mouse_guns == "1")

        env_keys: dict[str, str] = {}
        # Native Sinden frame: Batocera's system/game border settings, with an explicit
        # loader override when chosen. Auto still gates activation on the game's gun profile.
        border_mode = self.config.get("bordersmode") or "auto"
        if border_mode == "auto":
            border_mode = self.config.get("controllers.guns.bordersmode") or "auto"
        border = {"hidden": "0", "force": "1"}.get(border_mode, "auto")
        override = self.config.get("wal_sinden_border")
        if override in ("auto", "0", "1"):
            border = override
        env_keys["WAL_SINDEN_BORDER"] = _yaml_scalar(border)
        env_keys["WAL_SINDEN_BORDER_SIZE"] = _yaml_scalar(
            str(self.config.get("wal_sinden_border_size")
                or self.config.get("controllers.guns.borderssize")
                or "medium")
        )
        env_keys["WAL_SINDEN_BORDER_COLOR"] = _yaml_scalar(
            str(self.config.get("wal_sinden_border_color")
                or self.config.get("controllers.guns.borderscolor")
                or "white")
        )
        # The frontend knows virtual Sinden devices too; sysfs detection remains the fallback.
        env_keys["WAL_SINDEN_DETECTED"] = _yaml_scalar(
            "1" if any(gun.needs_borders for gun in guns) else "0"
        )

        keep_aspect = self.config.get("keep_aspect_ratio")
        if keep_aspect in ("on", "off"):
            env_keys["WAL_KEEP_ASPECT_RATIO"] = _yaml_scalar("1" if keep_aspect == "on" else "0")

        crosshair = self.config.get('wal_crosshair')
        if crosshair in ('0', '1'):
            # the common crosshair option of the games able to show crosshairs
            env_keys['WAL_CROSSHAIR'] = _yaml_scalar(crosshair)

        freeplay = self.config.get('wal_freeplay')
        if freeplay in ('0', '1'):
            # the common free play option (the loader's default: on)
            env_keys['WAL_FREEPLAY'] = _yaml_scalar(freeplay)

        keyboard = self.config.get("wal_keyboard")
        if keyboard in ("0", "1"):
            input_keys["keyboard_enabled"] = _yaml_scalar(keyboard == "1")

        configured_gamescope_backend = str(
            self.config.get("gamescope_backend") or ""
        ).lower()
        wayland_session = (
            bool(os.environ.get("WAYLAND_DISPLAY"))
            or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        )
        game_id = wal_game_id(self.rom)
        profile_name, profile_gamescope = gamescope_profile_details(
            WAL_DIR,
            str(self.system),
            game_id,
        )
        profile_has_gamescope = bool(profile_gamescope and profile_gamescope[0])
        profile_args = profile_gamescope[1] if profile_gamescope else []
        force_wayland_sdl = (
            configured_gamescope_backend in ("", "auto")
            and wayland_session
            and profile_has_gamescope
        )
        gamescope = self.config.get("gamescope")
        gamescope_options_set = force_wayland_sdl or any(
            str(self.config.get(key) or "") != ""
            and not (key in _GAMESCOPE_SWITCHES and self.config.get(key) == "0")
            and not (key == "gamescope_backend" and self.config.get(key) == "auto")
            for key in self.config.keys()
            if key.startswith("gamescope_")
        )
        if gamescope != "0" and gamescope_options_set:
            gamescope = "1"

        gamescope_keys: dict[str, str] = {}
        if gamescope in ("0", "1"):
            gamescope_keys["enabled"] = _yaml_scalar(gamescope == "1")
        generated_gamescope_args: list[str] = []
        needs_args_override = False
        if gamescope == "1":
            effective_backend = (
                "sdl" if force_wayland_sdl else configured_gamescope_backend
            ) if profile_has_gamescope else ""
            nested = self.config.get("gamescope_nested_resolution")
            if nested:
                width, separator, height = str(nested).partition("x")
                if separator:
                    gamescope_keys["width"] = str(int(width))
                    gamescope_keys["height"] = str(int(height))
            args_options_set = any(
                str(self.config.get(key) or "") != ""
                and key != "gamescope_backend"
                and not (key in _GAMESCOPE_SWITCHES and self.config.get(key) == "0")
                for key in self.config.keys()
                if key.startswith("gamescope_")
            ) and profile_has_gamescope
            needs_args_override = profile_has_gamescope and (
                effective_backend in ("sdl", "drm") or args_options_set
            )
            if needs_args_override:
                if effective_backend in ("sdl", "drm"):
                    generated_gamescope_args.extend(["--backend", effective_backend])
                generated_gamescope_args.extend(profile_args or [])
            if args_options_set:
                generated_gamescope_args.extend(gamescope_args(
                    self.config,
                    {"width": self.resolution.width, "height": self.resolution.height},
                    nested=False,
                ))
            if needs_args_override:
                gamescope_keys["args"] = json.dumps(generated_gamescope_args)
        controllers = [
            f"P{controller.player_number}: "
            f"{getattr(controller, 'real_name', '') or controller.name}"
            + (f" ({controller.device_path})" if controller.device_path else "")
            for controller in self.controllers
        ]
        gamescope_state = (
            "not configured in profile"
            if not profile_has_gamescope
            else "disabled"
            if gamescope == "0"
            else "enabled"
            if gamescope == "1"
            else "profile defaults"
        )
        backend = (
            "sdl" if force_wayland_sdl else configured_gamescope_backend or "auto"
        ) if profile_has_gamescope else "profile default"
        print(
            "\n".join([
                "=== Windows Arcade Loader ===",
                f"  Jeu                 : {self.rom.name} (ID: {game_id})",
                f"  Profil WAL          : {profile_name or 'introuvable'}",
                f"  Gamescope           : {gamescope_state} (backend: {backend})",
                f"  Arguments du profil : {profile_args}",
                f"  Arguments ES        : "
                f"{generated_gamescope_args if needs_args_override else []}",
                f"  Contrôleurs reconnus ({len(controllers)}):",
                *(
                    [f"    {controller}" for controller in controllers]
                    if controllers else ["    none"]
                ),
                f"=== Windows Arcade Loader ===",
            ]),
            file=sys.stderr,
        )

        lines = [f"{key}: {value}" for key, value in top.items()]
        if input_keys:
            lines.append("input:")
            lines.extend(f"  {key}: {value}" for key, value in input_keys.items())
        if env_keys:
            lines.append("env:")
            lines.extend(f"  {key}: {value}" for key, value in env_keys.items())
        if gamescope_keys:
            lines.append("gamescope:")
            lines.extend(f"  {key}: {value}" for key, value in gamescope_keys.items())

        layer = WAL_CONFIG / "es-options.yaml"
        layer.write_text("\n".join(lines) + "\n")

        command = [
            str(WAL_DIR / "arcade-launcher"),
            "run",
            str(self.rom),
            "--root",
            str(WAL_DIR),
            "--profile",
            str(layer),
        ]
        return Command(
            args=command,
            env={
                "SDL_GAMECONTROLLERCONFIG": generate_sdl_game_controller_config(list(self.controllers)),
                "SDL_JOYSTICK_HIDAPI": "0",
                'WAL_PLAYER_PADS': _player_pads(self.controllers),
            },
        )
