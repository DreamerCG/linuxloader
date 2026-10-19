from __future__ import annotations

from collections.abc import Mapping


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

    return args
