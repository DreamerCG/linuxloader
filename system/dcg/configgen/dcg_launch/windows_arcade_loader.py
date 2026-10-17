from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Final

from batocera_common.dataclasses import cached_dataclass, cached_property
from batocera_common.paths import CONFIGS
from batocera_launch.command import Command
from batocera_launch.devices.controller import generate_sdl_game_controller_config
from batocera_launch.emulator import Emulator
from batocera_launch.types import HotkeysContext

_logger = logging.getLogger(__name__)

WAL_DIR: Final = Path("/userdata/system/dcg/emulators/windows-arcade-loader")
WAL_CONFIG: Final = CONFIGS / "windows-arcade-loader"
_GAMESCOPE_SWITCHES: Final = {"gamescope_hdr", "gamescope_hdr_itm_enabled"}


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

    async def configure(self) -> Command:
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
