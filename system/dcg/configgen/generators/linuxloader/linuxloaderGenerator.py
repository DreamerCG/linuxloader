from __future__ import annotations

import logging
import os
import re
import sys
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Final

from evdev import ecodes

from configgen import Command
from configgen.batoceraPaths import CONFIGS, mkdir_if_not_exists
from configgen.controller import generate_sdl_game_controller_config
from configgen.exceptions import BatoceraException
from configgen.generators.Generator import Generator
from configgen.utils import bezels as bezelsUtil

if TYPE_CHECKING:
    from configgen.controller import Controller
    from configgen.types import HotkeysContext

_logger = logging.getLogger(__name__)

# Raw Thrills Linux games (Jurassic Park Arcade, Cruis'n Blast, Galaga
# Assault) run natively through linuxloader (lindbergh-loader fork, branch
# "rawthrills").
#
# The rom is the game directory (the one holding the "game" dump), either as
# a plain directory or packed as a .squashfs image. squashfs images are
# read-only: writesToRom() makes configgen mount them with a writable overlay
# kept in /userdata/saves/<system>/<rom name> (logs, settings, calibration).
#
# Controls are set per game kind, from the rom name: gun games (default),
# driving (Cruis'n Blast) and joystick (Galaga Assault, Pac-Man Chomp
# Mania, Pink Panther Jewel Heist). On every pad, Start starts, Select
# inserts a coin, R3 is the test switch and L3 service.
#
# Driving: a wheel or a pad steers the same way (left stick or wheel, R2 gas,
# L2 brake); the device steering also gets the game's force feedback, a
# wheel's motor force or, on a pad, a rumble (linuxloader picks it from
# ANALOGUE_1).
#
# The Namco ES1 games run through linuxloader too. Each rom is the cabinet's
# /opt/arcade/exec (a.elf at its top, with data/ and the save directories),
# and lib/ for what Batocera does not carry: sdl12-compat (libSDL-1.2.so.0)
# for Nirin and Dead Heat, a 32-bit libusb-1.0.so.0 for Dead Heat Riders.
#
# Nirin (a motorbike game) drives like the driving games (left stick or
# wheel, R2 gas, L2 brake); its buttons and gears are its own (_PAD_BIKE).
# Dead Heat and Dead Heat Riders steer with the left stick or the wheel and
# accelerate with R2; Dead Heat brakes with L2 as a pedal, Dead Heat Riders
# as its brake switch. Their buttons are their own (_PAD_DEADHEAT,
# _PAD_DHRIDERS). Maximum Heat 3D (Dead Heat's later build) drives as Dead
# Heat.
#
# Halo: Fireteam Raven is the exception. It is a 64-bit g7 title, so the
# 32-bit loader cannot host it: it runs under halo_rt.so instead, preloaded
# into the game by the dynamic linker. halo_rt.so reads the same
# linuxloader.ini (named by LINUXLOADER_CONFIG): resolution, gun border and
# the [EVDEV] map below.

LINUXLOADER_DIR: Final = Path('/userdata/system/dcg/emulators/linuxloader')
LINUXLOADER_CONFIG: Final = CONFIGS / 'linuxloader'

# Batocera gun button -> linuxloader evdev input, first match wins. Trigger
# fires; on a Sinden, button 1 (rear right) starts and button 2 (front right)
# inserts a coin; guns without them start with the middle button.
_GUN_BUTTONS: Final = {
    'left': ('BUTTON_1', ecodes.BTN_LEFT),
    'right': ('BUTTON_2', ecodes.BTN_RIGHT),
    '1': ('BUTTON_START', ecodes.BTN_1),
    'middle': ('BUTTON_START', ecodes.BTN_MIDDLE),
    '2': ('COIN', ecodes.BTN_2),
}

# Guns with a button 1 to start with: the middle button is BUTTON_3, the
# gun's third button (Terminator Salvation's grenade, Big Buck World's
# reload by firing off the screen).
_GUN_BUTTONS_WITH_START: Final = {
    **_GUN_BUTTONS,
    'middle': ('BUTTON_3', ecodes.BTN_MIDDLE),
}

# Halo's guns have three buttons: trigger, reload and action. The middle
# button is the action, so only button 1 starts.
_HALO_GUN_BUTTONS: Final = {
    'left': ('BUTTON_1', ecodes.BTN_LEFT),
    'right': ('BUTTON_2', ecodes.BTN_RIGHT),
    'middle': ('BUTTON_3', ecodes.BTN_MIDDLE),
    '1': ('BUTTON_START', ecodes.BTN_1),
    '2': ('COIN', ecodes.BTN_2),
}

# Pad buttons common to all games (es input name -> linuxloader input,
# PLAYER_n_ prefixed unless it starts with TEST).
_PAD_COMMON: Final = {
    'start': 'BUTTON_START',
    'select': 'COIN',
    'r3': 'TEST_BUTTON',
    'l3': 'BUTTON_SERVICE',
}

# Driving: y (left face button) brakes, b (bottom) changes the view, a
# (right) the music, up/down the volume (the menus' up and down).
_PAD_DRIVING: Final = {
    'y': 'BUTTON_1',
    'b': 'BUTTON_2',
    'a': 'BUTTON_3',
    'up': 'BUTTON_UP',
    'down': 'BUTTON_DOWN',
}

# Wheels also change the view and the music with their paddles.
_WHEEL_DRIVING: Final = {**_PAD_DRIVING, 'pageup': 'BUTTON_2', 'pagedown': 'BUTTON_3'}

# Tank! Tank! Tank!: the cabinet's wheel on the left stick, its two pedals
# on R2/L2 (as a driving game's), a (right face button) fires and b (bottom)
# is the safety button; up/down move in the menus.
_PAD_TANK: Final = {
    'a': 'BUTTON_1',
    'b': 'BUTTON_2',
    'up': 'BUTTON_UP',
    'down': 'BUTTON_DOWN',
}

# Nirin: b (bottom face button) selects (its Enter), a (right) changes the
# view, y (left) the transmission; L1/R1 (a wheel's paddles too) shift down
# and up, which are player 2's down and up on the cabinet; up/down move in
# the menus.
_PAD_BIKE: Final = {
    'b': 'BUTTON_1',
    'a': 'BUTTON_2',
    'y': 'BUTTON_3',
    'up': 'BUTTON_UP',
    'down': 'BUTTON_DOWN',
}
_BIKE_SHIFT: Final = {'pageup': 'PLAYER_2_BUTTON_DOWN', 'pagedown': 'PLAYER_2_BUTTON_UP'}

# Dead Heat: b (bottom face button) is Enter, a (right) the view, y (left)
# the nitrous; L1/R1 (a wheel's paddles too) shift down and up, player 2's
# down and up on the cabinet; up/down move in the menus.
_PAD_DEADHEAT: Final = {
    'b': 'BUTTON_1',
    'a': 'BUTTON_2',
    'y': 'BUTTON_3',
    'up': 'BUTTON_UP',
    'down': 'BUTTON_DOWN',
}

# Dead Heat Riders: b is Enter, y the nitrous, a the view; L2 (a switch on
# this cabinet) brakes.
_PAD_DHRIDERS: Final = {
    'b': 'BUTTON_1',
    'l2': 'BUTTON_3',
    'y': 'BUTTON_4',
    'a': 'BUTTON_5',
    'up': 'BUTTON_UP',
    'down': 'BUTTON_DOWN',
}

# Joystick: b (bottom face button) starts and fires.
_PAD_JOYSTICK: Final = {'b': 'BUTTON_1'}

_DIRECTIONS: Final = ('up', 'down', 'left', 'right')

# Mice and touchpads (a gamepad's too) aim like guns for the players left
# without one: the left button (a touchpad's click) fires, the right one
# reloads, the middle one is the third button.
_POINTER_BUTTONS: Final = {
    'BUTTON_1': ecodes.BTN_LEFT,
    'BUTTON_2': ecodes.BTN_RIGHT,
    'BUTTON_3': ecodes.BTN_MIDDLE,
}

_EVENT_RE: Final = re.compile(r'^/dev/input/event(\d+)$')

def _detect_batocera_version() -> int | None:

    try:
        result = subprocess.run(
            ["batocera-es-swissknife", "--version"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    m = re.search(r"^\s*(\d+)", result.stdout)
    if not m:
        return None
    return int(m.group(1))

def _pointers(guns, /) -> list[tuple[str, str, set[int]]]:
    """Mice and touchpads, by event number: (node, axis type (REL for a
    mouse, ABS for a touchpad), button codes); not the guns, nor anything on
    a gun's device, nor virtual devices (a streaming server's, Sunshine's...:
    Bluetooth pads are under /devices/virtual/misc/uhid instead)."""
    import evdev
    import pyudev

    context = pyudev.Context()
    gun_nodes = {gun.node for gun in guns}
    gun_paths = set()
    for node in gun_nodes:
        try:
            gun_paths.add(pyudev.Devices.from_device_file(context, node).properties.get('ID_PATH'))
        except Exception:
            pass
    gun_paths.discard(None)

    found: list[tuple[int, str, str, set[int]]] = []
    for dev in context.list_devices(subsystem='input'):
        node = dev.device_node
        match = _EVENT_RE.match(node) if node else None
        props = dev.properties
        if match is None or node in gun_nodes or props.get('ID_INPUT_GUN') == '1':
            continue
        if props.get('ID_INPUT_MOUSE') != '1' and props.get('ID_INPUT_TOUCHPAD') != '1':
            continue
        if props.get('ID_PATH') in gun_paths or (props.get('DEVPATH') or '').startswith('/devices/virtual/input/'):
            continue
        try:
            caps = evdev.InputDevice(node).capabilities()
        except OSError:
            continue
        rel = set(caps.get(ecodes.EV_REL, []))
        abs_codes = {code if isinstance(code, int) else code[0] for code in caps.get(ecodes.EV_ABS, [])}
        if {ecodes.REL_X, ecodes.REL_Y} <= rel:
            axis = 'REL'
        elif {ecodes.ABS_X, ecodes.ABS_Y} <= abs_codes:
            axis = 'ABS'
        else:
            continue
        found.append((int(match.group(1)), node, axis, set(caps.get(ecodes.EV_KEY, []))))
    return [(node, axis, keys) for _, node, axis, keys in sorted(found)]


def _game_kind(rom: Path, /) -> str:
    name = rom.name.lower()
    if 'halo' in name:
        return 'halo'
    if 'cruis' in name:
        return 'driving'
    # Wangan Midnight Maximum Tune 3 (Namco N2): wheel, pedals, view button.
    if 'wangan' in name or 'maximum tune' in name:
        return 'driving'
    if 'nirin' in name:
        return 'bike'
    flat = re.sub(r'[^a-z]', '', name)
    if 'deadheatriders' in flat or 'dhriders' in flat:
        return 'dhriders'
    # Maximum Heat 3D is Dead Heat's later build: the same cabinet and controls.
    if 'deadheat' in flat or 'maximumheat' in flat:
        return 'deadheat'
    if 'tanktanktank' in flat:
        return 'tank'
    if 'galaga' in name or 'pac' in name or 'panther' in name:
        return 'joystick'
    return 'gun'


def _pad_input(pad: Controller, name: str, /, *, digital: bool) -> str | None:
    """linuxloader evdev source for an es input: a key, or an axis (hats are
    ABS_HAT0X/Y...), analog or, if digital, pressed at one end."""
    inp = pad.inputs.get(name)
    if inp is None:
        return None
    dev = pad.device_path
    if inp.type == 'button' and inp.code is not None:
        return f'{dev}:KEY:{inp.code}'
    if inp.type == 'hat':
        vertical = inp.value in ('1', '4')
        axis = ecodes.ABS_HAT0X + int(inp.id) * 2 + (1 if vertical else 0)
        return f'{dev}:ABS:{axis}:{"MIN" if inp.value in ("1", "8") else "MAX"}'
    if inp.type == 'axis' and inp.code is not None:
        if digital:
            return f'{dev}:ABS:{inp.code}:{"MIN" if int(inp.value) < 0 else "MAX"}'
        relaxed = pad.get_mapping_axis_relaxed_values().get(name)
        return f'{dev}:{"ABS_NEG" if relaxed and relaxed["reversed"] else "ABS"}:{inp.code}'
    return None


def _stick_direction(pad: Controller, direction: str, /) -> str | None:
    """The left stick pushed in a direction: es records its up and left
    ends (joystick1up, joystick1left), down and right are the other ends."""
    source = _pad_input(pad, 'joystick1up' if direction in ('up', 'down') else 'joystick1left', digital=True)
    if source is None or ':ABS:' not in source or direction in ('up', 'left'):
        return source
    return source[:-3] + ('MAX' if source.endswith('MIN') else 'MIN')


def _set(evdev: dict[str, str], key: str, value: str | None, /) -> None:
    if value is not None and key not in evdev:
        evdev[key] = value


def _setup_pad(evdev: dict[str, str], kind: str, nplayer: int, pad: Controller, /, *, wheel: bool = False) -> None:
    player = f'PLAYER_{nplayer}_'
    for name, action in _PAD_COMMON.items():
        key = action if action.startswith('TEST') else player + action
        if nplayer == 1 or not action.startswith('TEST'):
            _set(evdev, key, _pad_input(pad, name, digital=True))

    if kind in ('driving', 'bike', 'deadheat', 'dhriders', 'tank'):
        if nplayer != 1:
            return
        _set(evdev, 'ANALOGUE_1', _pad_input(pad, 'joystick1left', digital=False))
        _set(evdev, 'ANALOGUE_2', _pad_input(pad, 'r2', digital=False))
        if kind == 'dhriders':
            for name, action in _PAD_DHRIDERS.items():
                _set(evdev, player + action, _pad_input(pad, name, digital=True))
            return
        _set(evdev, 'ANALOGUE_3', _pad_input(pad, 'l2', digital=False))
        if kind == 'deadheat':
            for name, action in _PAD_DEADHEAT.items():
                _set(evdev, player + action, _pad_input(pad, name, digital=True))
            for name, key in _BIKE_SHIFT.items():
                _set(evdev, key, _pad_input(pad, name, digital=True))
            return
        if kind == 'tank':
            for name, action in _PAD_TANK.items():
                _set(evdev, player + action, _pad_input(pad, name, digital=True))
            return
        if kind == 'bike':
            for name, action in _PAD_BIKE.items():
                _set(evdev, player + action, _pad_input(pad, name, digital=True))
            for name, key in _BIKE_SHIFT.items():
                _set(evdev, key, _pad_input(pad, name, digital=True))
            return
        for name, action in (_WHEEL_DRIVING if wheel else _PAD_DRIVING).items():
            _set(evdev, player + action, _pad_input(pad, name, digital=True))
    elif kind == 'joystick':
        for name, action in _PAD_JOYSTICK.items():
            _set(evdev, player + action, _pad_input(pad, name, digital=True))
        # The d-pad and the left stick together (linuxloader takes a list).
        for direction in _DIRECTIONS:
            sources = (_pad_input(pad, direction, digital=True), _stick_direction(pad, direction))
            _set(evdev, player + 'BUTTON_' + direction.upper(), ','.join(dict.fromkeys(s for s in sources if s)) or None)


class LinuxloaderGenerator(Generator):

    def getHotkeysContext(self) -> HotkeysContext:
        # Exit (hotkey + start, a gun's three buttons held for 2s: see
        # rawthrills.keys): Esc, which linuxloader quits on in every game.
        return {
            'name': 'linuxloader',
            'keys': {'exit': 'KEY_ESC', 'coin': 'KEY_5'},
        }

    def writesToRom(self, config) -> bool:
        return True

    # linuxloader draws the gun border inside the game frame
    # ([Display] BORDER_ENABLED): configgen must not add its own on top.
    def supportsInternalBezels(self) -> bool:
        return True

    def getInGameRatio(self, config, gameResolution, rom):
        return 16 / 9

    def generate(self, system, rom, playersControllers, metadata, guns, wheels, gameResolution):
        game_dir = rom if rom.is_dir() else rom.parent
        # A .squashfs rom arrives as its overlay mount, named after the image.
        kind = _game_kind(rom)
        mkdir_if_not_exists(LINUXLOADER_CONFIG)
        config_file = LINUXLOADER_CONFIG / 'linuxloader.ini'

        conf: dict[str, dict[str, str]] = {
            'Display': {
                'WIDTH': str(gameResolution['width']),
                'HEIGHT': str(gameResolution['height']),
                'FULLSCREEN': '0',
                'BORDER_ENABLED': 'false',
                'ROTATE_VERTICAL': '1' if system.config.get_bool('linuxloader_rotate') else '0',
                # The game directory's bezel.png, when there is one.
                'BEZEL_ENABLED': '0' if system.config.get('linuxloader_bezel') == '0' else '-1',
                # Off: the games made for a 4:3 monitor (Big Buck World)
                # stretched to the screen instead of black bars.
                'KEEP_ASPECT_RATIO': '0' if system.config.get('keep_aspect_ratio') == 'off' else '1',
            },
            'Input': {'INPUT_MODE': '2'},
            'EVDEV': {},
        }
        evdev = conf['EVDEV']
        # DEBUG: état de départ
        print("linuxloader kind :", kind, file=sys.stderr)
        print("linuxloader rom :", rom, file=sys.stderr)
        print("linuxloader use_guns :", system.config.use_guns, file=sys.stderr)
        print("linuxloader guns count :", len(guns), file=sys.stderr)
        for g in guns:
            print("linuxloader gun :", g.node, "buttons:", g.buttons, "needs_borders:", g.needs_borders, file=sys.stderr)
        print("linuxloader pads :", [(p.index, p.device_path, p.real_name) for p in playersControllers], file=sys.stderr)
        
        # Guns (gun games): P1 on ANALOGUE_1/2, P2 on ANALOGUE_3/4, and so on;
        # then mice and touchpads for the players left, in their order.
        # Halo runs as its 4 player cabinet; the other games have 2 players.
        max_players = 4 if kind == 'halo' else 2
        gun_players = 0
        if kind in ('gun', 'halo') and system.config.use_guns and guns:
            if any(gun.needs_borders for gun in guns):
                inner, outer = bezelsUtil.gunBordersSize(system.guns_borders_size_name(guns))
                conf['Display']['BORDER_ENABLED'] = 'true'
                conf['Display']['WHITE_BORDER_PERCENTAGE'] = str(inner)
                conf['Display']['BLACK_BORDER_PERCENTAGE'] = str(outer)
            for nplayer, gun in enumerate(guns[:max_players], start=1):
                evdev[f'ANALOGUE_{nplayer * 2 - 1}'] = f'{gun.node}:ABS:0'
                evdev[f'ANALOGUE_{nplayer * 2}'] = f'{gun.node}:ABS:1'
                print("evdev after guns :", evdev, file=sys.stderr)
                print("gun_players :", gun_players, file=sys.stderr)                
                if kind == 'halo':
                    pointers = _pointers(guns if system.config.use_guns and guns else [])
                    print("linuxloader pointers :", pointers, file=sys.stderr)
                    buttons = _HALO_GUN_BUTTONS
                elif '1' in gun.buttons:
                    buttons = _GUN_BUTTONS_WITH_START
                else:
                    buttons = _GUN_BUTTONS
                for button, (action, code) in buttons.items():
                    key = f'PLAYER_{nplayer}_{action}'
                    if button in gun.buttons and key not in evdev:
                        evdev[key] = f'{gun.node}:KEY:{code}'
                gun_players = nplayer

        if kind in ('gun', 'halo'):
            pointers = _pointers(guns if system.config.use_guns and guns else [])
            for nplayer, (node, axis, keys) in enumerate(pointers[: max_players - gun_players], start=gun_players + 1):
                evdev[f'ANALOGUE_{nplayer * 2 - 1}'] = f'{node}:{axis}:{ecodes.ABS_X if axis == "ABS" else ecodes.REL_X}'
                evdev[f'ANALOGUE_{nplayer * 2}'] = f'{node}:{axis}:{ecodes.ABS_Y if axis == "ABS" else ecodes.REL_Y}'
                for action, code in _POINTER_BUTTONS.items():
                    if code in keys:
                        evdev.setdefault(f'PLAYER_{nplayer}_{action}', f'{node}:KEY:{code}')
                _logger.debug('linuxloader: player %s aims with %s (%s)', nplayer, node, axis)

        # Pads (and wheels, driving the same way): the players without a gun.
        for nplayer, pad in enumerate(playersControllers[:max_players], start=1):
            if nplayer > gun_players:
                wheel = system.config.use_wheels and pad.device_path in wheels
                _setup_pad(evdev, kind, nplayer, pad, wheel=wheel)

        # No controller, gun or pointer mapped: linuxloader's own input, the
        # keyboard (evdev mode reads nothing but the [EVDEV] devices).
        if not evdev:
            conf['Input']['INPUT_MODE'] = '1'

        lines: list[str] = []
        for section, values in conf.items():
            lines.append(f'[{section}]')
            lines.extend(f'{key} = {value}' for key, value in values.items())
            lines.append('')
            print("evdev final :", evdev, file=sys.stderr)
            print("conf Display :", conf['Display'], file=sys.stderr)            
        config_file.write_text('\n'.join(lines))
        _logger.debug('linuxloader config (%s) %s:\n%s', kind, config_file, '\n'.join(lines))

        if kind == 'halo':
            return self._halo(game_dir, config_file)

        # Games kept in the cabinet's layout (Pink Panther Jewel Heist) have
        # the binary, its hasp/ and bezel.png in pm/, the cabinet's /pm.
        if not (game_dir / 'game').is_file() and (game_dir / 'pm' / 'game').is_file():
            game_dir = game_dir / 'pm'
        command = [str(LINUXLOADER_DIR / 'linuxloader'), '-g', str(game_dir), '-c', str(config_file)]
        if system.config.get_bool('linuxloader_test'):
            command.append('-t')

        os.chdir(LINUXLOADER_DIR)
        version = _detect_batocera_version()
        print("Batocera Version :", version, file=sys.stderr)
        print("Debug command :", command, file=sys.stderr)        

        # Chemins de base communs à toutes les versions
        ld_library_path = (
            f'/lib32/pulseaudio:/lib32:/lib32/extralibs:/lib:/usr/lib:{LINUXLOADER_DIR}'
        )

        # Libs supplémentaires uniquement pour Batocera 42
        if version == 42:
            ld_library_path += f':{LINUXLOADER_DIR}/lib'
            print("Chargement des libs supplementaires pour Batocera 42", ld_library_path, file=sys.stderr)

        return Command.Command(
            array=command,
            env={
                # /lib32/libpulsecommon-17.0.so links to the 64-bit library:
                # the 32-bit one is in /lib32/pulseaudio (The Walking Dead
                # links libpulse).
                'LD_LIBRARY_PATH': ld_library_path,
                'LIBGL_DRIVERS_PATH': '/lib32/dri:/usr/lib/dri',
                'SPA_PLUGIN_DIR': '/lib32/spa-0.2:/usr/lib/spa-0.2',
                'PIPEWIRE_MODULE_DIR': '/lib32/pipewire-0.3:/usr/lib/pipewire-0.3',
                'SDL_GAMECONTROLLERCONFIG': generate_sdl_game_controller_config(playersControllers),
                'SDL_JOYSTICK_HIDAPI': '0',
            },
        )

    @staticmethod
    def _halo(game_dir: Path, config_file: Path, /) -> Command.Command:
        """Halo: Fireteam Raven, the 64-bit g7 title.

        halo_rt.so rebuilds the dump's import table, answers the dongle and
        maps the cabinet's /pm onto the install, so it has to be in the game
        before anything else runs: the dynamic linker is invoked by hand to
        preload it.  The game is either in the cabinet's pm/g7/halo, /pm then
        being two levels above it, or at the root of the game's folder, which
        then stands for /pm as a whole.  Either way it is started from its own
        directory, and lib/ there holds the fmod libraries Batocera does not
        carry.  It takes the resolution, the gun border and the evdev input
        from config_file, like the loader.
        """
        halo_dir = game_dir / 'pm' / 'g7' / 'halo'
        if not (halo_dir / 'game').is_file():
            halo_dir = game_dir
        if not (halo_dir / 'game').is_file():
            raise BatoceraException(f'No Halo executable at {game_dir / "game"} or {game_dir / "pm/g7/halo/game"}')

        os.chdir(halo_dir)
        return Command.Command(
            array=[
                '/lib64/ld-linux-x86-64.so.2',
                '--preload', str(LINUXLOADER_DIR / 'halo_rt.so'),
                './game',
            ],
            env={
                'LD_LIBRARY_PATH': 'lib',
                # The import table is rebuilt up front, so nothing may be
                # left for a lazy resolution that would run against it.
                'LD_BIND_NOW': '1',
                'LINUXLOADER_CONFIG': str(config_file),
            },
        )
