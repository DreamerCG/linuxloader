from __future__ import annotations

import json
import logging
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

_logger = logging.getLogger(__name__)

# Windows arcade games (Taito Type X / X2, NESiCAxLive, ...) through the Windows arcade
# loader: arcade-launcher runs the game in Wine with its system's I/O emulated (JVS board,
# NESiCA card reader...) and maps the pads, keyboards and guns itself (SDL and evdev), as
# the game's profile says (systemprofiles/<system>/<gameid>.yaml).
#
# The rom is a game dump directory holding <gameid>.windowsloader (the executable path
# relative to the dump root): ES lists that file, the game id selects the profile. Or the
# dump packed as a .squashfs image: Batocera Launch mounts it and hands the mounted
# directory; needs_overlayfs() gives it a writable overlay kept in
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

print(f" ===================================", file=sys.stderr)
print(f" Lancement de Windows Arcade Loader Generator :", file=sys.stderr)
print(f" WAL DIR : {WAL_DIR}", file=sys.stderr)
print(f" WAL_CONFIG : {WAL_CONFIG}", file=sys.stderr)
print(f" ===================================", file=sys.stderr)


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


@cached_dataclass
class WindowsArcadeLoader(Emulator):
    @cached_property
    def hotkeygen_context(self) -> HotkeysContext:
        return {
            "name": "windows-arcade-loader",
            "keys": {"exit": "/usr/bin/pkill -TERM -x arcade-launcher", "coin": "KEY_5"},
        }

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
                },
            )

        WAL_CONFIG.mkdir(parents=True, exist_ok=True)

        top: dict[str, str] = {}
        input_keys: dict[str, str] = {}

        reshade = self.config.get("wal_reshade")
        if reshade in ("0", "1"):
            top["reshade"] = _yaml_scalar(reshade == "1")

        renderer = self.config.get("wal_renderer")
        if renderer in ("dxvk", "wined3d"):
            top["dxvk"] = _yaml_scalar(renderer == "dxvk")

        mouse_guns = self.config.get("wal_mouse_guns")
        if mouse_guns in ("0", "1"):
            input_keys["guns_mouse"] = _yaml_scalar(mouse_guns == "1")

        keyboard = self.config.get("wal_keyboard")
        if keyboard in ("0", "1"):
            input_keys["keyboard_enabled"] = _yaml_scalar(keyboard == "1")

        gamescope = self.config.get("gamescope")
        gamescope_options_set = any(
            str(self.config.get(key) or "") != ""
            and not (key in _GAMESCOPE_SWITCHES and self.config.get(key) == "0")
            for key in self.config.keys()
            if key.startswith("gamescope_")
        )
        if gamescope != "0" and gamescope_options_set:
            gamescope = "1"

        gamescope_keys: dict[str, str] = {}
        if gamescope in ("0", "1"):
            gamescope_keys["enabled"] = _yaml_scalar(gamescope == "1")
        if gamescope == "1":
            nested = self.config.get("gamescope_nested_resolution")
            if nested:
                width, separator, height = str(nested).partition("x")
                if separator:
                    gamescope_keys["width"] = str(int(width))
                    gamescope_keys["height"] = str(int(height))

        lines = [f"{key}: {value}" for key, value in top.items()]
        if input_keys:
            lines.append("input:")
            lines.extend(f"  {key}: {value}" for key, value in input_keys.items())
        if gamescope_keys:
            lines.append("gamescope:")
            lines.extend(f"  {key}: {value}" for key, value in gamescope_keys.items())

        layer = WAL_CONFIG / "es-options.yaml"
        layer.write_text("\n".join(lines) + "\n")
        _logger.debug("windows-arcade-loader options %s:\n%s", layer, "\n".join(lines))

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
            },
        )
