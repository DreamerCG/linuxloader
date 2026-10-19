from __future__ import annotations

import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

GAMESCOPE_DIR = Path("/userdata/system/dcg/gamescope")

_LINKS = (
    ("lib64/libsystemd.so.0.42.0", "/usr/lib64/libsystemd.so.0", False),
    ("lib64/libdecor-0.so.0.200.4", "/usr/lib64/libdecor-0.so.0", False),
    ("lib64/libdisplay-info.so.0.3.0", "/usr/lib64/libdisplay-info.so.3", False),
    ("lib64/libeis.so.1.5.0", "/usr/lib64/libeis.so.1", False),
    ("lib64/libliftoff.so.0.5.0", "/usr/lib64/libliftoff.so.0", False),
    ("lib64/libseat.so.1", "/usr/lib64/libseat.so.1", False),
    ("lib64/libwlroots-0.18.so", "/usr/lib64/libwlroots-0.18.so", False),
    ("lib64/libxcb-errors.so.0.0.0", "/usr/lib64/libxcb-errors.so.0", False),
    ("lib64/libwayland-client.so.0", "/usr/lib64/libwayland-client.so.0", False),
    ("lib64/libwayland-server.so.0", "/usr/lib64/libwayland-server.so.0", False),
    ("lib64/libXRes.so.1", "/usr/lib64/libXRes.so.1", False),
    ("lib64/libdisplay-info.so.2", "/usr/lib64/libdisplay-info.so.2", False),
    ("lib64/gamescope", "/usr/lib64/gamescope", True),
    # The bundled Vulkan layer manifest embeds this library directory.
    ("lib64/gamescope", "/usr/lib/x86_64-linux-gnu/gamescope", True),
    ("share/gamescope", "/usr/share/gamescope", True),
    ("gamescope", "/usr/bin/gamescope", False),
    ("gamescopectl", "/usr/bin/gamescopectl", False),
    ("gamescopereaper", "/usr/bin/gamescopereaper", False),
    ("gamescopestream", "/usr/bin/gamescopestream", False),
    ("Xwayland", "/usr/bin/Xwayland", False),
)


_WSI_MANIFEST = Path("/usr/share/vulkan/implicit_layer.d/VkLayer_FROG_gamescope_wsi.x86_64.json")
_BUNDLED_WSI_MANIFEST = GAMESCOPE_DIR / "share/vulkan/implicit_layer.d/VkLayer_FROG_gamescope_wsi.x86_64.json"


def ensure_gamescope_links() -> None:
    if not GAMESCOPE_DIR.is_dir():
        _logger.warning(
            "Gamescope bundle not found at %s; system links were not created",
            GAMESCOPE_DIR,
        )
        return

    missing_sources = [
        str(GAMESCOPE_DIR / source)
        for source, _, _ in _LINKS
        if not (GAMESCOPE_DIR / source).exists()
    ]
    if missing_sources:
        raise FileNotFoundError(
            "Incomplete Gamescope bundle; missing: " + ", ".join(missing_sources)
        )

    if _WSI_MANIFEST.is_symlink() and _WSI_MANIFEST.resolve() == _BUNDLED_WSI_MANIFEST.resolve():
        _WSI_MANIFEST.unlink()
        _logger.info("Removed Gamescope WSI manifest link %s", _WSI_MANIFEST)

    for source, destination, is_directory in _LINKS:
        source_path = GAMESCOPE_DIR / source
        destination_path = Path(destination)
        if destination_path.exists() or destination_path.is_symlink():
            continue
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination_path.symlink_to(source_path, target_is_directory=is_directory)
            _logger.info("Created Gamescope link %s -> %s", destination_path, source_path)
        except FileExistsError:
            # Another launcher may have created the same link concurrently.
            continue
