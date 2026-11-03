from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

from configgen import Command
from configgen.batoceraPaths import BATOCERA_ES_DIR, CONFIGS, USER_ES_DIR, configure_emulator, mkdir_if_not_exists
from configgen.controller import Controller, generate_sdl_game_controller_config
from configgen.input import Input
from configgen.generators.Generator import Generator
from dcg_launch.gamescope_links import ensure_gamescope_links
from dcg_launch.gamescope_options import gamescope_args, gamescope_config_values, gamescope_profile

if TYPE_CHECKING:
    from configgen.types import HotkeysContext

_logger = logging.getLogger(__name__)

# Windows arcade games (Taito Type X / X2, NESiCAxLive, ...) through the Windows arcade
# loader: arcade-launcher runs the game in Wine with its system's I/O emulated (JVS board,
# NESiCA card reader...) and maps the pads, keyboards and guns itself (SDL and evdev), as
# the game's profile says (systemprofiles/<system>/<gameid>.yaml).
#
# The rom is a game dump directory holding <gameid>.windowsloader (the executable path
# relative to the dump root): ES lists that file, the game id selects the profile. Or the
# dump packed as a .squashfs image: configgen mounts it and hands the mounted directory;
# writesToRom() gives it a writable overlay kept in /userdata/saves/<system>/<image name>
# (the game's save data in the dump's WindowsLoader folder).
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

WAL_DIR: Final = Path('/userdata/system/dcg/emulators/windows-arcade-loader')
WAL_CONFIG: Final = CONFIGS / 'windows-arcade-loader'

print(f" ===================================", file=sys.stderr)
print(f" Lancement de Windows Arcade Loader Generator :", file=sys.stderr)
print(f" WAL DIR : {WAL_DIR}", file=sys.stderr)
print(f" WAL_CONFIG : {WAL_CONFIG}", file=sys.stderr)
print(f" ===================================", file=sys.stderr)


_GAMESCOPE_SWITCHES: Final = {'gamescope_hdr', 'gamescope_hdr_itm_enabled'}


def _es_pads() -> list[Controller]:
    # outside a game ES passes no -pX controllers: every pad configured in ES (user file first)
    import xml.etree.ElementTree as ET

    pads: dict[str, Controller] = {}
    for conffile in (USER_ES_DIR / 'es_input.cfg', BATOCERA_ES_DIR / 'es_input.cfg'):
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


def _player_pads(controllers) -> str:
    # ES's player order (P1 first) by evdev node: the launcher gives the players in it, under
    # the GUI's priority list (userprofiles/players.yaml)
    return ';'.join(c.device_path for c in controllers if c.device_path)


def _yaml_scalar(value: str | bool) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return f'"{value}"'


class WindowsArcadeLoaderGenerator(Generator):

    def getHotkeysContext(self) -> HotkeysContext:
        # Exit: the launcher stops the game and quits on SIGTERM. Coin: P1 coin (key 5).
        return {
            'name': 'windows-arcade-loader',
            'keys': {'exit': '/usr/bin/pkill -TERM -x arcade-launcher', 'coin': 'KEY_5'},
        }

    def writesToRom(self, config) -> bool:
        return True

    def generate(self, system, rom, playersControllers, metadata, guns, wheels, gameResolution):
        if configure_emulator(rom):
            os.chdir(WAL_DIR)
            return Command.Command(
                array=[str(WAL_DIR / 'arcade-launcher'), 'gui', '--root', str(WAL_DIR)],
                env={
                    'SDL_GAMECONTROLLERCONFIG': generate_sdl_game_controller_config(playersControllers or _es_pads()),
                    'SDL_JOYSTICK_HIDAPI': '0',
                    'WAL_PLAYER_PADS': _player_pads(playersControllers or []),
                },
            )

        ensure_gamescope_links()
        mkdir_if_not_exists(WAL_CONFIG)

        # ES options -> profile keys (top level or under input:), set ones only.
        top: dict[str, str] = {}
        input_keys: dict[str, str] = {}
        reshade = system.config.get('wal_reshade')
        if reshade in ('0', '1'):
            # rotation / bezel of the dumps that ship a ReShade setup (vertical games)
            top['reshade'] = _yaml_scalar(reshade == '1')
        renderer = system.config.get('wal_renderer')
        if renderer in ('dxvk', 'wined3d'):
            top['dxvk'] = _yaml_scalar(renderer == 'dxvk')
        mouse_guns = system.config.get('wal_mouse_guns')
        if mouse_guns in ('0', '1'):
            input_keys['guns_mouse'] = _yaml_scalar(mouse_guns == '1')
        env_keys: dict[str, str] = {}
        keep_aspect = system.config.get('keep_aspect_ratio')
        if keep_aspect in ('on', 'off'):
            # games whose picture the payload scales to the screen (Gashaaaan)
            env_keys['WAL_KEEP_ASPECT_RATIO'] = _yaml_scalar('1' if keep_aspect == 'on' else '0')
        crosshair = system.config.get('wal_crosshair')
        if crosshair in ('0', '1'):
            # the common crosshair option of the games able to show crosshairs
            env_keys['WAL_CROSSHAIR'] = _yaml_scalar(crosshair)
        keyboard = system.config.get('wal_keyboard')
        if keyboard in ('0', '1'):
            input_keys['keyboard_enabled'] = _yaml_scalar(keyboard == '1')

        gamescope_config = gamescope_config_values(system.config)
        configured_gamescope_backend = str(gamescope_config['gamescope_backend'] or '').lower()
        wayland_session = (
            bool(os.environ.get('WAYLAND_DISPLAY'))
            or os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
        )
        profile_gamescope = gamescope_profile(
            WAL_DIR,
            system.name,
            Path(rom).stem,
        )
        profile_has_gamescope = bool(profile_gamescope and profile_gamescope[0])
        profile_args = profile_gamescope[1] if profile_gamescope else []
        force_wayland_sdl = (
            configured_gamescope_backend in ('', 'auto')
            and wayland_session
            and profile_has_gamescope
        )
        reported_backend = (
            'ignored (profile has no gamescope section)'
            if not profile_has_gamescope
            else 'sdl (automatic)'
            if force_wayland_sdl
            else configured_gamescope_backend or 'auto'
        )

        print(
            f"windows-arcade-loader: Gamescope configuration for {Path(rom).name}:\n"
            + "\n".join(f"  {key} = {value!r}" for key, value in gamescope_config.items())
            + f"\n  wayland_session = {wayland_session}"
            + f"\n  effective_backend = {reported_backend}",
            file=sys.stderr,
        )

        gamescope_keys: dict[str, str] = {}
        gamescope = system.config.get('gamescope')
        # any GAMESCOPE option set turns it on (unless ENABLE is set to Off)
        def gamescope_option_set(key: str) -> bool:
            value = str(system.config.get(key) or '')
            # switches (HDR...) set to Off do not count
            return (
                value != ''
                and not (key in _GAMESCOPE_SWITCHES and value == '0')
                and not (key == 'gamescope_backend' and value == 'auto')
            )

        if gamescope != '0' and (
            force_wayland_sdl
            or any(k.startswith('gamescope_') and gamescope_option_set(k) for k in system.config.keys())
        ):
            gamescope = '1'
        if gamescope in ('0', '1'):
            gamescope_keys['enabled'] = _yaml_scalar(gamescope == '1')
        if gamescope == '1':
            effective_backend = (
                'sdl' if force_wayland_sdl else configured_gamescope_backend
            ) if profile_has_gamescope else ''
            nested = system.config.get('gamescope_nested_resolution')
            if nested:
                width, _, height = nested.partition('x')
                gamescope_keys['width'] = str(int(width))
                gamescope_keys['height'] = str(int(height))
            args_options_set = any(
                str(system.config.get(key) or '') != ''
                and key != 'gamescope_backend'
                and not (key in _GAMESCOPE_SWITCHES and system.config.get(key) == '0')
                for key in system.config.keys()
                if key.startswith('gamescope_')
            ) and profile_has_gamescope
            needs_args_override = profile_has_gamescope and (
                effective_backend in ('sdl', 'drm') or args_options_set
            )
            generated_gamescope_args: list[str] = []
            if needs_args_override:
                if effective_backend in ('sdl', 'drm'):
                    generated_gamescope_args.extend(['--backend', effective_backend])
                generated_gamescope_args.extend(profile_args or [])
            if args_options_set:
                # a JSON list is a YAML flow sequence
                generated_gamescope_args.extend(gamescope_args(
                    system.config,
                    gameResolution,
                    nested=False,
                ))
            if needs_args_override:
                gamescope_keys['args'] = json.dumps(generated_gamescope_args)
                print(
                    f"windows-arcade-loader: Gamescope arguments written for "
                    f"{Path(rom).name}: {generated_gamescope_args}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"windows-arcade-loader: no Gamescope section in the WAL profile for "
                    f"{Path(rom).name}; backend and argument overrides are ignored"
                    if not profile_args
                    else f"windows-arcade-loader: Gamescope profile arguments preserved "
                    f"for {Path(rom).name}",
                    file=sys.stderr,
                )
        elif force_wayland_sdl and gamescope == '0':
            print(
                f'windows-arcade-loader: Gamescope disabled by EmulationStation '
                f'for {Path(rom).name}; Wayland SDL backend will not override it',
                file=sys.stderr,
            )
        elif gamescope == '0':
            print(
                f'windows-arcade-loader: Gamescope disabled by EmulationStation '
                f'for {Path(rom).name}',
                file=sys.stderr,
            )
        else:
            print(
                f'windows-arcade-loader: no Gamescope arguments generated by '
                f'EmulationStation for {Path(rom).name}; game profile settings remain in effect',
                file=sys.stderr,
            )

        lines = [f'{key}: {value}' for key, value in top.items()]
        if input_keys:
            lines.append('input:')
            lines.extend(f'  {key}: {value}' for key, value in input_keys.items())
        if env_keys:
            lines.append('env:')
            lines.extend(f'  {key}: {value}' for key, value in env_keys.items())
        if gamescope_keys:
            lines.append('gamescope:')
            lines.extend(f'  {key}: {value}' for key, value in gamescope_keys.items())
        layer = WAL_CONFIG / 'es-options.yaml'
        layer.write_text('\n'.join(lines) + '\n')
        _logger.debug('windows-arcade-loader options %s:\n%s', layer, '\n'.join(lines))

        command = [str(WAL_DIR / 'arcade-launcher'), 'run', str(rom), '--root', str(WAL_DIR), '--profile', str(layer)]
        os.chdir(WAL_DIR)
        return Command.Command(
            array=command,
            env={
                'SDL_GAMECONTROLLERCONFIG': generate_sdl_game_controller_config(playersControllers),
                'SDL_JOYSTICK_HIDAPI': '0',
                'WAL_PLAYER_PADS': _player_pads(playersControllers),
            },
        )
