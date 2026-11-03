from __future__ import annotations

import logging
import re
import shlex
from collections.abc import Mapping
from pathlib import Path

_logger = logging.getLogger(__name__)

GAMESCOPE_CONFIG_KEYS = (
    "gamescope",
    "gamescope_backend",
    "gamescope_output_resolution",
    "gamescope_nested_resolution",
    "gamescope_nested_refresh",
    "gamescope_scaler",
    "gamescope_filter",
    "gamescope_sharpness",
    "gamescope_hdr",
    "gamescope_sdr_gamut_wideness",
    "gamescope_hdr_sdr_content_nits",
    "gamescope_hdr_itm_enabled",
    "gamescope_hdr_itm_sdr_nits",
    "gamescope_hdr_itm_target_nits",
    "gamescope_windows_mode",
    "gamescope_reshade_effect",
)


def _get_str(config: object, key: str) -> str:
    getter = getattr(config, "get_str", None)
    value = getter(key) if callable(getter) else _get_value(config, key)
    return str(value) if value else ""


def _get_bool(config: object, key: str) -> bool:
    getter = getattr(config, "get_bool", None)
    if callable(getter):
        return bool(getter(key))
    value = _get_value(config, key)
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def _get_value(config: object, key: str) -> object | None:
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key)
    if isinstance(config, Mapping):
        return config.get(key)
    raise TypeError(f"Unsupported Gamescope configuration type: {type(config).__name__}")


def gamescope_config_values(config: object) -> dict[str, object | None]:
    """Return all Gamescope settings consulted by the Windows Arcade Loader generator."""
    return {key: _get_value(config, key) for key in GAMESCOPE_CONFIG_KEYS}


def gamescope_profile(
    wal_dir: Path,
    system: str,
    game_id: str,
) -> tuple[bool, list[str]] | None:
    """Return Gamescope section presence and args, resolving profile IDs or display names."""
    system_profiles_dir = wal_dir / "systemprofiles"
    system_profile_dir = system_profiles_dir / system
    profile_id = game_id
    if not (system_profile_dir / f"{profile_id}.yaml").is_file():
        rom_name_tokens = set(re.findall(r"[a-z0-9]+", game_id.lower()))
        candidates: list[tuple[int, str, str]] = []
        for candidate in system_profiles_dir.glob("*/*.yaml"):
            for line in candidate.read_text(encoding="utf-8").splitlines():
                match = re.match(r"^\s*name:\s*(.*?)\s*(?:#.*)?$", line)
                if not match:
                    continue
                name = match.group(1).strip().strip("\"'")
                name_tokens = set(re.findall(r"[a-z0-9]+", name.lower()))
                compact_name = "".join(re.findall(r"[a-z0-9]+", name.lower()))
                compact_rom_name = "".join(re.findall(r"[a-z0-9]+", game_id.lower()))
                if name_tokens and (
                    name_tokens.issubset(rom_name_tokens)
                    or compact_name == compact_rom_name
                ):
                    candidates.append((len(name_tokens), candidate.parent.name, candidate.stem))
                break

        if candidates:
            best_score = max(score for score, _, _ in candidates)
            best_matches = [
                (candidate_system, candidate_id)
                for score, candidate_system, candidate_id in candidates
                if score == best_score
            ]
            if len(best_matches) != 1:
                raise ValueError(
                    f"Ambiguous WAL profile name for {system}/{game_id}: {best_matches}"
                )
            profile_system, profile_id = best_matches[0]
            system_profile_dir = system_profiles_dir / profile_system
        else:
            return None

    has_gamescope = False
    args: list[str] = []
    for profile in (
        system_profile_dir / f"{profile_id}.yaml",
        wal_dir / "userprofiles" / system_profile_dir.name / f"{profile_id}.yaml",
    ):
        try:
            lines = profile.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue

        for index, line in enumerate(lines):
            if line.strip() != "gamescope:":
                continue
            has_gamescope = True
            section_indent = len(line) - len(line.lstrip())
            for option_index in range(index + 1, len(lines)):
                option_line = lines[option_index]
                stripped = option_line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                indent = len(option_line) - len(option_line.lstrip())
                if indent <= section_indent:
                    break
                if indent != section_indent + 2 or not stripped.startswith("args:"):
                    continue

                value = stripped[len("args:"):].strip()
                if value.startswith("["):
                    closing_bracket = value.rfind("]")
                    if closing_bracket < 0:
                        raise ValueError(f"Unclosed Gamescope args list in {profile}")
                    lexer = shlex.shlex(value[1:closing_bracket], posix=True)
                    lexer.whitespace += ","
                    lexer.whitespace_split = True
                    lexer.commenters = "#"
                    args = list(lexer)
                elif not value:
                    args = []
                    for item_line in lines[option_index + 1:]:
                        item = item_line.strip()
                        if not item or item.startswith("#"):
                            continue
                        item_indent = len(item_line) - len(item_line.lstrip())
                        if item_indent <= indent:
                            break
                        if item.startswith("- "):
                            lexer = shlex.shlex(item[2:], posix=True)
                            lexer.whitespace_split = True
                            lexer.commenters = "#"
                            args.extend(lexer)
                else:
                    raise ValueError(f"Unsupported Gamescope args value in {profile}: {value}")
                break
            else:
                continue
            break
    return has_gamescope, args


def gamescope_args(
    config: object,
    current_resolution: Mapping[str, str | int],
    *,
    nested: bool = True,
) -> list[str]:
    """Build Gamescope arguments; nested=False leaves game sizing to the loader profile."""
    args: list[str] = []

    output_resolution = _get_str(config, "gamescope_output_resolution")
    if output_resolution:
        output_width, _, output_height = output_resolution.partition("x")
    else:
        output_width = str(current_resolution["width"])
        output_height = str(current_resolution["height"])
    args.extend(["-W", output_width.strip(), "-H", output_height.strip()])

    nested_resolution = _get_str(config, "gamescope_nested_resolution")
    if nested_resolution and nested:
        nested_width, _, nested_height = nested_resolution.partition("x")
        args.extend(["-w", nested_width.strip(), "-h", nested_height.strip()])

    nested_refresh = _get_str(config, "gamescope_nested_refresh")
    if nested_refresh:
        args.extend(["-r", nested_refresh])

    scaler = _get_str(config, "gamescope_scaler")
    if scaler:
        args.extend(["-S", scaler])

    filter_name = _get_str(config, "gamescope_filter")
    if filter_name:
        args.extend(["-F", filter_name])

    sharpness = _get_str(config, "gamescope_sharpness")
    if sharpness:
        args.extend(["--sharpness", sharpness])

    if _get_bool(config, "gamescope_hdr"):
        sdr_gamut = _get_str(config, "gamescope_sdr_gamut_wideness")
        if sdr_gamut:
            args.extend(["--sdr-gamut-wideness", sdr_gamut])

        hdr_sdr_nits = _get_str(config, "gamescope_hdr_sdr_content_nits")
        if hdr_sdr_nits:
            args.extend(["--hdr-sdr-content-nits", hdr_sdr_nits])

        if _get_bool(config, "gamescope_hdr_itm_enabled"):
            args.append("--hdr-itm-enabled")

        hdr_itm_sdr_nits = _get_str(config, "gamescope_hdr_itm_sdr_nits")
        if hdr_itm_sdr_nits:
            args.extend(["--hdr-itm-sdr-nits", hdr_itm_sdr_nits])

        hdr_itm_target = _get_str(config, "gamescope_hdr_itm_target_nits")
        if hdr_itm_target:
            args.extend(["--hdr-itm-target-nits", hdr_itm_target])

    windows_mode = _get_str(config, "gamescope_windows_mode")
    if windows_mode in ("borderless", "bordeless"):
        args.append("-b")
    elif windows_mode == "fullscreen":
        args.append("-f")

    reshade_effect = _get_str(config, "gamescope_reshade_effect")
    if reshade_effect:
        args.extend(["--reshade-effect", reshade_effect])

    _logger.debug("GameScope arguments: %s", args)
    return args
