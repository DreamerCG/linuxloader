from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from batocera_common.dataclasses import cached_dataclass, cached_property
from batocera_launch.command import Command
from batocera_launch.devices.controller import generate_sdl_game_controller_config
from batocera_launch.emulator import Emulator
from batocera_launch.types import HotkeysContext

_logger = logging.getLogger("ESLog")

PLAYER_MAPPING = [
    {"BTN_MIDDLE": "KEY_1", "BTN_1": "KEY_5"},
    {"BTN_MIDDLE": "KEY_2", "BTN_1": "KEY_6"},
    {"BTN_MIDDLE": "KEY_3", "BTN_1": "KEY_7"},
    {"BTN_MIDDLE": "KEY_4", "BTN_1": "KEY_8"},
]
SHARED_MAPPING = {
    "BTN_2": None,
    "BTN_3": None,
    "BTN_4": None,
    "BTN_5": "KEY_UP",
    "BTN_6": "KEY_DOWN",
    "BTN_7": "KEY_LEFT",
    "BTN_8": "KEY_RIGHT",
}
GUN_CONFIG_PATH = Path("/var/run/wine-guns.json")
GAME_MAPPING_CONFIG_PATH = Path("/userdata/system/dcg/configs/wine-gun-mappings.json")
DEMULSHOOTER_GAMES = {
    "PointBlankX": "pbx",
    "Drakon": "pbx",
    "NerfArcade": "pbx",
    "RabbidsHollywood": "rha",
    "WildWestShootout": "wws",
    "TombRaider": "tra",
    "OperationWolf": "owr",
    "MIB": "mib",
    "MissionImpossible": "mia",
    "NightHunter2": "nha2",
    "MarsSortie": "marss",
    "PVZ": "pvz",
}
_NVIDIA_VARIABLES_TO_REMOVE = (
    "__NV_PRIME_RENDER_OFFLOAD",
    "__VK_LAYER_NV_optimus",
    "__GLX_VENDOR_LIBRARY_NAME",
)


def _detect_demulshooter(rom_path: str | Path) -> str | None:
    rom_name = Path(rom_path).name.replace(" ", "").lower()
    if "demul" not in rom_name:
        return None
    if "mars" in rom_name:
        return "marss"
    if "rabbid" in rom_name:
        return "rha"
    if "pointblank" in rom_name or "nerf" in rom_name:
        return "pbx"
    if "tombraider" in rom_name:
        return "tra"
    if "wolf" in rom_name:
        return "owr"
    if "meninblack" in rom_name:
        return "mib"
    if "nighthunter" in rom_name:
        return "nha2"
    if "wild" in rom_name:
        return "wws"
    if "mission" in rom_name:
        return "mia"
    if "plant" in rom_name:
        return "pvz"
    if "drakon" in rom_name:
        return "drakon"
    if "dcop" in rom_name:
        return "dcop"
    return "demul"


def _load_game_player_mapping() -> dict[str, dict[int, dict[str, str | None]]]:
    try:
        with GAME_MAPPING_CONFIG_PATH.open(encoding="utf-8") as mapping_file:
            raw_mapping = json.load(mapping_file)
    except FileNotFoundError:
        _logger.info("Wine gun: no custom mapping file found at %s", GAME_MAPPING_CONFIG_PATH)
        return {}
    except (OSError, json.JSONDecodeError) as error:
        _logger.error("Wine gun: failed to load custom mappings from %s: %s", GAME_MAPPING_CONFIG_PATH, error)
        raise

    if not isinstance(raw_mapping, dict):
        raise ValueError("Wine gun mapping file must contain a JSON object")

    game_mapping: dict[str, dict[int, dict[str, str | None]]] = {}
    for game_name, button_mappings in raw_mapping.items():
        if not isinstance(game_name, str) or not game_name.strip():
            raise ValueError("Wine gun mapping game names must be non-empty strings")
        if not isinstance(button_mappings, dict):
            raise ValueError(f"Wine gun mapping for {game_name!r} must be a JSON object")

        player_mappings: dict[int, dict[str, str | None]] = {}
        for button_name, players in button_mappings.items():
            if not isinstance(button_name, str) or not isinstance(players, dict):
                raise ValueError(f"Wine gun mapping for {game_name!r} must map button names to player objects")
            for player, key_name in players.items():
                try:
                    player_number = int(player)
                except (TypeError, ValueError) as error:
                    raise ValueError("Wine gun mapping player IDs must be positive numbers") from error
                if player_number < 1:
                    raise ValueError("Wine gun mapping player IDs must be positive numbers")
                if key_name is not None and not isinstance(key_name, str):
                    raise ValueError("Wine gun mapping values must be key names or null")
                player_mappings.setdefault(player_number - 1, {})[button_name] = key_name
        game_mapping[game_name.casefold()] = player_mappings

    return game_mapping


def _build_player_mapping(
    player_index: int,
    metadata: dict[str, str],
    rom_path: Path,
    game_player_mapping: dict[str, dict[int, dict[str, str | None]]],
) -> dict[str, str]:
    mapping: dict[str, str | None] = dict(SHARED_MAPPING)
    mapping.update(PLAYER_MAPPING[player_index] if player_index < len(PLAYER_MAPPING) else PLAYER_MAPPING[0])

    rom_name = rom_path.name.casefold()
    matching_games = [name for name in game_player_mapping if name in rom_name]
    if matching_games:
        game_name = max(matching_games, key=len)
        overrides = game_player_mapping[game_name].get(player_index, {})
        mapping.update(overrides)
        _logger.info("Wine gun P%d: custom mapping for %s: %s", player_index + 1, game_name, overrides)

    for button_name in list(mapping):
        player_key = f"gun_p{player_index + 1}_{button_name.lower()}"
        override = metadata.get(player_key)
        if override is None:
            override = metadata.get(f"gun_{button_name.lower()}")
        if override is not None:
            mapping[button_name] = None if override == "" or override.lower() == "none" else override.upper()
            if mapping[button_name] is not None:
                _logger.info("Wine gun P%d: override %s -> %s", player_index + 1, button_name, override.upper())

    return {key: value for key, value in mapping.items() if value is not None}


@cached_dataclass
class Wine(Emulator):
    @cached_property
    def hotkeygen_context(self) -> HotkeysContext:
        return {
            "name": "wine",
            "keys": {"exit": "/userdata/system/dcg/bin/batocera-wine windows stop"},
        }

    @property
    def needs_mouse(self) -> bool:
        return self.config.get_bool("force_mouse")

    async def configure(self) -> Command:
        rom = self.rom
        system = self.system
        launcher = "/userdata/system/dcg/bin/batocera-wine"
        if system == "windows_installers":
            return Command(args=[launcher, "windows", "install", str(rom)])

        command = [launcher, system, "play", str(rom)]
        environment: dict[str, str] = {}

        try:
            language = subprocess.check_output(
                ["batocera-settings-get", "system.language"],
                text=True,
            ).strip()
        except subprocess.CalledProcessError:
            language = "en_US"
        if language:
            environment.update({"LANG": language + ".UTF-8", "LC_ALL": language + ".UTF-8"})

        if self.config.get_bool("sdl_config", True):
            environment.update({
                "SDL_GAMECONTROLLERCONFIG": generate_sdl_game_controller_config(list(self.controllers)),
                "SDL_JOYSTICK_HIDAPI": "0",
            })

        if Path("/var/tmp/nvidia.prime").exists():
            for variable_name in _NVIDIA_VARIABLES_TO_REMOVE:
                os.environ.pop(variable_name, None)
            environment["VK_ICD_FILENAMES"] = (
                "/usr/share/vulkan/icd.d/nvidia_icd.x86_64.json:"
                "/usr/share/vulkan/icd.d/nvidia_icd.i686.json"
            )

        if self.config.get_bool("use_guns"):
            self._setup_guns(list(self.guns), self.metadata, rom)
            environment.update({
                "WINE_FORCE_RAWINPUT": "1",
                "WINE_RAWMOUSE_COUNT": str(len(self.guns)),
            })

        return Command(args=command, env=environment)

    def _setup_guns(self, guns: list, metadata: dict[str, str], rom: Path) -> None:
        demulshooter_game = _detect_demulshooter(rom)
        print(f" Demul Shooter Detected : {demulshooter_game}", file=sys.stderr)
        if not demulshooter_game:
            return

        _logger.error("Wine: %d light gun(s) detected", len(guns))
        game_player_mapping = _load_game_player_mapping()
        gun_configs = []
        for index in range(len(guns)):
            mapping = _build_player_mapping(index, metadata, rom, game_player_mapping)
            gun_configs.append({"player": index + 1, "mapping": mapping})
            _logger.error(
                "Wine gun P%d: %s",
                index + 1,
                ", ".join(f"{button}->{key}" for button, key in mapping.items()),
            )

        width = self.resolution.width
        height = self.resolution.height
        config = {
            "guns": gun_configs,
            "ds_bridge": {
                "game": demulshooter_game,
                "width": int(width),
                "height": int(height),
            },
        }
        _logger.error("Wine gun: DemulShooter bridge: game=%s %dx%d", demulshooter_game, width, height)
        GUN_CONFIG_PATH.write_text(json.dumps(config, indent=2))
        _logger.error("Wine gun: config written to %s", GUN_CONFIG_PATH)
