from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Final

from evdev import ecodes

from batocera_common.dataclasses import cached_dataclass, cached_property
from batocera_common.paths import CONFIGS
from batocera_launch.command import Command
from batocera_launch.devices.controller import generate_sdl_game_controller_config
from batocera_launch.emulator import Emulator
from batocera_launch.types import HotkeysContext

_logger = logging.getLogger(__name__)
LINUXLOADER_DIR: Final = Path("/userdata/system/dcg/emulators/linuxloader")
LINUXLOADER_CONFIG: Final = CONFIGS / "linuxloader"

_GUN_BUTTONS: Final = {
    "left": ("BUTTON_1", ecodes.BTN_LEFT),
    "right": ("BUTTON_2", ecodes.BTN_RIGHT),
    "1": ("BUTTON_START", ecodes.BTN_1),
    "middle": ("BUTTON_START", ecodes.BTN_MIDDLE),
    "2": ("COIN", ecodes.BTN_2),
}
_GUN_BUTTONS_WITH_START: Final = {**_GUN_BUTTONS, "middle": ("BUTTON_3", ecodes.BTN_MIDDLE)}
_HALO_GUN_BUTTONS: Final = {
    "left": ("BUTTON_1", ecodes.BTN_LEFT),
    "right": ("BUTTON_2", ecodes.BTN_RIGHT),
    "middle": ("BUTTON_3", ecodes.BTN_MIDDLE),
    "1": ("BUTTON_START", ecodes.BTN_1),
    "2": ("COIN", ecodes.BTN_2),
}
_PAD_COMMON: Final = {"start": "BUTTON_START", "select": "COIN", "r3": "TEST_BUTTON", "l3": "BUTTON_SERVICE"}
_PAD_DRIVING: Final = {
    "y": "BUTTON_1", "b": "BUTTON_2", "a": "BUTTON_3", "up": "BUTTON_UP", "down": "BUTTON_DOWN",
}
_WHEEL_DRIVING: Final = {**_PAD_DRIVING, "pageup": "BUTTON_2", "pagedown": "BUTTON_3"}
_PAD_MOTOGP: Final = {"up": "BUTTON_7", "down": "BUTTON_8"}
_PAD_TANK: Final = {"a": "BUTTON_1", "b": "BUTTON_2", "up": "BUTTON_UP", "down": "BUTTON_DOWN"}
_PAD_BIKE: Final = {"b": "BUTTON_1", "a": "BUTTON_2", "y": "BUTTON_3", "up": "BUTTON_UP", "down": "BUTTON_DOWN"}
_BIKE_SHIFT: Final = {"pageup": "PLAYER_2_BUTTON_DOWN", "pagedown": "PLAYER_2_BUTTON_UP"}
_PAD_DEADHEAT: Final = {
    "b": "BUTTON_1", "a": "BUTTON_2", "y": "BUTTON_3", "up": "BUTTON_UP", "down": "BUTTON_DOWN",
}

_PAD_DHRIDERS: Final = {
    "b": "BUTTON_1", "l2": "BUTTON_3", "y": "BUTTON_4", "a": "BUTTON_5",
    "up": "BUTTON_UP", "down": "BUTTON_DOWN",
}
_PAD_JOYSTICK: Final = {"b": "BUTTON_1"}
_PAD_TMNT: Final = {"a": "BUTTON_1", "b": "BUTTON_2", "x": "BUTTON_3"}
_PAD_GHOSTBUSTERS: Final = {
    "b": "BUTTON_1", "a": "BUTTON_2", "y": "BUTTON_3", "up": "BUTTON_UP", "down": "BUTTON_DOWN",
}
_PAD_WOF: Final = {
    "b": "BUTTON_1", "a": "BUTTON_1", "left": "BUTTON_LEFT", "right": "BUTTON_RIGHT",
    "up": "BUTTON_UP", "down": "BUTTON_DOWN",
}
_DIRECTIONS: Final = ("up", "down", "left", "right")
_POINTER_BUTTONS: Final = {
    "BUTTON_1": ecodes.BTN_LEFT, "BUTTON_2": ecodes.BTN_RIGHT, "BUTTON_3": ecodes.BTN_MIDDLE,
}
_EVENT_RE: Final = re.compile(r"^/dev/input/event(\d+)$")


def _detect_batocera_version() -> int | None:
    try:
        result = subprocess.run(
            ["batocera-es-swissknife", "--version"], capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"^\s*(\d+)", result.stdout)
    return int(match.group(1)) if match else None


def _pointers(guns: list, /) -> list[tuple[str, str, set[int]]]:
    import evdev
    import pyudev

    context = pyudev.Context()
    gun_nodes = {gun.node for gun in guns}
    gun_paths = set()
    for node in gun_nodes:
        try:
            gun_paths.add(pyudev.Devices.from_device_file(context, node).properties.get("ID_PATH"))
        except OSError:
            continue
    gun_paths.discard(None)

    found: list[tuple[bool, int, str, str, set[int]]] = []
    for device in context.list_devices(subsystem="input"):
        node = device.device_node
        match = _EVENT_RE.match(node) if node else None
        props = device.properties
        if match is None or node in gun_nodes or props.get("ID_INPUT_GUN") == "1":
            continue
        if props.get("ID_INPUT_MOUSE") != "1" and props.get("ID_INPUT_TOUCHPAD") != "1":
            continue
        if props.get("ID_PATH") in gun_paths:
            continue
        virtual = (props.get("DEVPATH") or "").startswith("/devices/virtual/input/")
        if virtual and any(props.get(prop) == "1" for prop in (
            "ID_INPUT_KEYBOARD", "ID_INPUT_TOUCHSCREEN", "ID_INPUT_TABLET",
        )):
            continue
        try:
            caps = evdev.InputDevice(node).capabilities()
        except OSError:
            continue
        relative = set(caps.get(ecodes.EV_REL, []))
        absolute = {code if isinstance(code, int) else code[0] for code in caps.get(ecodes.EV_ABS, [])}
        if {ecodes.REL_X, ecodes.REL_Y} <= relative:
            axis = "REL"
        elif {ecodes.ABS_X, ecodes.ABS_Y} <= absolute:
            axis = "ABS"
        else:
            continue
        found.append((virtual, int(match.group(1)), node, axis, set(caps.get(ecodes.EV_KEY, []))))
    return [(node, axis, keys) for _, _, node, axis, keys in sorted(found)]


def _fix_pt2_sound_daemon(game_dir: Path, /) -> None:
    link = game_dir / "pt2snd"
    if not (game_dir / "pt2s_g11").is_file() or not link.exists():
        return
    try:
        if not link.is_symlink() and link.stat().st_size < 64:
            target = link.read_text(errors="replace").strip()
            if target and "/" not in target and (game_dir / target).is_file():
                link.unlink()
                link.symlink_to(target)
                _logger.info("linuxloader: %s -> %s restored", link, target)
        daemon = link.resolve()
        if not os.access(daemon, os.X_OK):
            daemon.chmod(daemon.stat().st_mode | 0o111)
            _logger.info("linuxloader: %s made executable", daemon)
    except OSError as error:
        _logger.warning("linuxloader: cannot repair %s (%s), the game will have no sound", link, error)


def _game_kind(rom: Path, /) -> str:
    name = rom.name.lower()
    flat = re.sub(r"[^a-z]", "", name)
    if "halo" in name:
        return "halo"
    if "teenage mutant ninja turtles" in name or "tmnt" in name:
        return "tmnt"
    if "ghostbusters" in flat:
        return "ghostbusters"
    if "spaceinvadersfrenzy" in flat:
        return "gun"
    if "worldslargestpacman" in flat:
        return "joystick"
    if "centipede" in name:
        return "centipede"
    if "cruis" in name or "superbikes" in flat or "wangan" in name or "maximum tune" in name:
        return "driving"
    if "motogp" in flat:
        return "motogp"
    if "nirin" in name:
        return "bike"
    if "deadheatriders" in flat or "dhriders" in flat:
        return "dhriders"
    if "deadheat" in flat or "maximumheat" in flat:
        return "deadheat"
    if "tanktanktank" in flat:
        return "tank"
    if "galaga" in name or "pac" in name or "panther" in name:
        return "joystick"
    if "fortune" in name:
        return "wof"
    return "gun"


def _pad_input(pad, name: str, /, *, digital: bool) -> str | None:
    inp = pad.inputs.get(name)
    if inp is None:
        return None
    device = pad.device_path
    if inp.type == "button" and inp.code is not None:
        return f"{device}:KEY:{inp.code}"
    if inp.type == "hat":
        vertical = inp.value in ("1", "4")
        axis = ecodes.ABS_HAT0X + int(inp.id) * 2 + int(vertical)
        return f"{device}:ABS:{axis}:{'MIN' if inp.value in ('1', '8') else 'MAX'}"
    if inp.type == "axis" and inp.code is not None:
        if digital:
            return f"{device}:ABS:{inp.code}:{'MIN' if int(inp.value) < 0 else 'MAX'}"
        relaxed = pad.get_mapping_axis_relaxed_values().get(name)
        return f"{device}:{'ABS_NEG' if relaxed and relaxed['reversed'] else 'ABS'}:{inp.code}"
    return None


def _stick_direction(pad, direction: str, /) -> str | None:
    source = _pad_input(
        pad, "joystick1up" if direction in ("up", "down") else "joystick1left", digital=True,
    )
    if source is None or ":ABS:" not in source or direction in ("up", "left"):
        return source
    return source[:-3] + ("MAX" if source.endswith("MIN") else "MIN")


def _set(evdev: dict[str, str], key: str, value: str | None, /) -> None:
    if value is not None and key not in evdev:
        evdev[key] = value


def _setup_pad(evdev: dict[str, str], kind: str, nplayer: int, pad, /, *, wheel: bool = False) -> None:
    player = f"PLAYER_{nplayer}_"
    for name, action in _PAD_COMMON.items():
        key = action if action.startswith("TEST") else player + action
        if nplayer == 1 or not action.startswith("TEST"):
            _set(evdev, key, _pad_input(pad, name, digital=True))

    if kind in ("driving", "motogp", "bike", "deadheat", "dhriders", "tank"):
        if nplayer != 1:
            return
        _set(evdev, "ANALOGUE_1", _pad_input(pad, "joystick1left", digital=False))
        _set(evdev, "ANALOGUE_2", _pad_input(pad, "r2", digital=False))
        if kind == "dhriders":
            for name, action in _PAD_DHRIDERS.items():
                _set(evdev, player + action, _pad_input(pad, name, digital=True))
            return
        _set(evdev, "ANALOGUE_3", _pad_input(pad, "l2", digital=False))
        if kind == "deadheat":
            for name, action in _PAD_DEADHEAT.items():
                _set(evdev, player + action, _pad_input(pad, name, digital=True))
            for name, key in _BIKE_SHIFT.items():
                _set(evdev, key, _pad_input(pad, name, digital=True))
            return
        if kind == "motogp":
            for name, action in _PAD_MOTOGP.items():
                _set(evdev, player + action, _pad_input(pad, name, digital=True))
            return
        if kind == "tank":
            for name, action in _PAD_TANK.items():
                _set(evdev, player + action, _pad_input(pad, name, digital=True))
            return
        if kind == "bike":
            for name, action in _PAD_BIKE.items():
                _set(evdev, player + action, _pad_input(pad, name, digital=True))
            for name, key in _BIKE_SHIFT.items():
                _set(evdev, key, _pad_input(pad, name, digital=True))
            return
        mapping = _WHEEL_DRIVING if wheel else _PAD_DRIVING
        for name, action in mapping.items():
            _set(evdev, player + action, _pad_input(pad, name, digital=True))
    elif kind == "ghostbusters":
        _set(evdev, f"ANALOGUE_{nplayer * 2 - 1}", _pad_input(pad, "joystick1left", digital=False))
        _set(evdev, f"ANALOGUE_{nplayer * 2}", _pad_input(pad, "joystick1up", digital=False))
        for name, action in _PAD_GHOSTBUSTERS.items():
            _set(evdev, player + action, _pad_input(pad, name, digital=True))
    elif kind == "wof":
        if nplayer != 1:
            return
        _set(evdev, "ANALOGUE_3", _pad_input(pad, "joystick1left", digital=False))
        for name, action in _PAD_WOF.items():
            _set(evdev, player + action, _pad_input(pad, name, digital=True))
    elif kind in ("joystick", "tmnt"):
        mapping = _PAD_TMNT if kind == "tmnt" else _PAD_JOYSTICK
        for name, action in mapping.items():
            _set(evdev, player + action, _pad_input(pad, name, digital=True))
        for direction in _DIRECTIONS:
            sources = (_pad_input(pad, direction, digital=True), _stick_direction(pad, direction))
            _set(
                evdev, player + "BUTTON_" + direction.upper(),
                ",".join(dict.fromkeys(source for source in sources if source)) or None,
            )
        if kind == "tmnt" and nplayer == 1:
            _set(evdev, "VOLUME_UP", _pad_input(pad, "pageup", digital=True))
            _set(evdev, "VOLUME_DOWN", _pad_input(pad, "pagedown", digital=True))


@cached_dataclass
class LinuxLoader(Emulator):
    @cached_property
    def hotkeygen_context(self) -> HotkeysContext:
        return {"name": "linuxloader", "keys": {"exit": "KEY_ESC", "coin": "KEY_5"}}

    @property
    def handles_bezels(self) -> bool:
        return True

    def needs_overlayfs(self, _rom: Path, /) -> bool:
        return True

    @cached_property
    def in_game_ratio(self) -> float:
        return 16 / 9

    @property
    def execution_path(self) -> Path:
        return LINUXLOADER_DIR

    async def configure(self) -> Command:
        rom = self.rom
        game_dir = rom if rom.is_dir() else rom.parent
        kind = _game_kind(rom)
        LINUXLOADER_CONFIG.mkdir(parents=True, exist_ok=True)
        config_file = LINUXLOADER_CONFIG / "linuxloader.ini"

        conf: dict[str, dict[str, str]] = {
            "Display": {
                "WIDTH": str(self.resolution.width),
                "HEIGHT": str(self.resolution.height),
                "FULLSCREEN": "1" if "nerf" in rom.name.lower()
                or "bigbuckhunterpro" in re.sub(r"[^a-z]", "", rom.name.lower()) else "0",
                "BORDER_ENABLED": "false",
                "ROTATE_VERTICAL": "1" if self.config.get_bool("linuxloader_rotate") else "0",
                "BEZEL_ENABLED": "0" if self.config.get("linuxloader_bezel") == "0" else "-1",
                "KEEP_ASPECT_RATIO": "0" if self.config.get("keep_aspect_ratio") == "off" else "1",
            },
            "Input": {"INPUT_MODE": "2"},
            "CrossHairs": {"ENABLE_CROSSHAIRS": "1" if self.config.get_bool("linuxloader_crosshair") else "0"},
            "EVDEV": {},
        }
        evdev = conf["EVDEV"]
        guns_enabled = self.config.get_bool("use_guns")
        guns = list(self.guns)
        controllers = list(self.controllers)
        wheels = self.wheels

        max_players = 4 if kind in ("halo", "tmnt") else 2
        gun_players = 0
        if kind in ("gun", "halo", "ghostbusters") and guns_enabled and guns:
            if any(gun.needs_borders for gun in guns):
                conf["Display"]["BORDER_ENABLED"] = "true"
            for nplayer, gun in enumerate(guns[:max_players], start=1):
                evdev[f"ANALOGUE_{nplayer * 2 - 1}"] = f"{gun.node}:ABS:0"
                evdev[f"ANALOGUE_{nplayer * 2}"] = f"{gun.node}:ABS:1"
                buttons = _HALO_GUN_BUTTONS if kind == "halo" else (
                    _GUN_BUTTONS_WITH_START if "1" in gun.buttons else _GUN_BUTTONS
                )
                for button, (action, code) in buttons.items():
                    key = f"PLAYER_{nplayer}_{action}"
                    if button in gun.buttons and key not in evdev:
                        evdev[key] = f"{gun.node}:KEY:{code}"
                gun_players = nplayer

        if kind in ("gun", "halo", "ghostbusters", "wof"):
            pointers = _pointers(guns if guns_enabled else [])
            if kind == "wof":
                pointers = [pointer for pointer in pointers if pointer[1] == "REL"][:1]
            for nplayer, (node, axis, keys) in enumerate(
                pointers[:max_players - gun_players], start=gun_players + 1,
            ):
                evdev[f"ANALOGUE_{nplayer * 2 - 1}"] = (
                    f"{node}:{axis}:{ecodes.ABS_X if axis == 'ABS' else ecodes.REL_X}"
                )
                evdev[f"ANALOGUE_{nplayer * 2}"] = (
                    f"{node}:{axis}:{ecodes.ABS_Y if axis == 'ABS' else ecodes.REL_Y}"
                )
                if kind != "wof":
                    for action, code in _POINTER_BUTTONS.items():
                        if code in keys:
                            evdev.setdefault(f"PLAYER_{nplayer}_{action}", f"{node}:KEY:{code}")

        for nplayer, pad in enumerate(controllers[:max_players], start=1):
            if nplayer > gun_players:
                _setup_pad(
                    evdev,
                    kind,
                    nplayer,
                    pad,
                    wheel=self.config.get_bool("use_wheels") and pad.device_path in wheels,
                )

        if not evdev:
            conf["Input"]["INPUT_MODE"] = "1"

        lines: list[str] = []
        for section, values in conf.items():
            lines.append(f"[{section}]")
            lines.extend(f"{key} = {value}" for key, value in values.items())
            lines.append("")
        config_file.write_text("\n".join(lines))
        _logger.debug("linuxloader config (%s) %s:\n%s", kind, config_file, "\n".join(lines))

        if not (game_dir / "game").is_file() and (game_dir / "pm" / "game").is_file():
            game_dir = game_dir / "pm"
        if (game_dir / "System" / "armyops-bin").is_file():
            game_dir = game_dir / "System"
        _fix_pt2_sound_daemon(game_dir)

        command = [str(LINUXLOADER_DIR / "linuxloader"), "-g", str(game_dir), "-c", str(config_file)]
        if self.config.get_bool("linuxloader_test"):
            command.append("-t")

        version = _detect_batocera_version()
        library_dirs = ["/lib32/pulseaudio", "/lib32", "/lib32/extralibs", str(game_dir / "lib")]
        if kind == "ghostbusters":
            library_dirs.append(str(LINUXLOADER_DIR / "ghostbusters"))
        if version == 42:
            library_dirs.append(str(LINUXLOADER_DIR / "lib"))
        library_dirs.extend(["/lib", "/usr/lib", str(LINUXLOADER_DIR)])

        return Command(
            args=command,
            env={
                "LD_LIBRARY_PATH": ":".join(library_dirs),
                "LIBGL_DRIVERS_PATH": "/lib32/dri:/usr/lib/dri",
                "SPA_PLUGIN_DIR": "/lib32/spa-0.2:/usr/lib/spa-0.2",
                "PIPEWIRE_MODULE_DIR": "/lib32/pipewire-0.3:/usr/lib/pipewire-0.3",
                "SDL_GAMECONTROLLERCONFIG": generate_sdl_game_controller_config(controllers),
                "SDL_JOYSTICK_HIDAPI": "0",
            },
        )
