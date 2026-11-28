"""PixelLens — local-first media converter and pixel/layout/SSIM diff engine."""

from __future__ import annotations

__version__ = "0.2.0"

from pixellens.cli import inspect_file, main
from pixellens.compare.pixels import compare_images, load_rgba
from pixellens.audit.assets import (
    audit_inline_svgs,
    audit_raster_images,
    audit_svg_icons,
    run_full_asset_audit,
)

__all__ = [
    "__version__",
    "audit_inline_svgs",
    "audit_raster_images",
    "audit_svg_icons",
    "compare_images",
    "inspect_file",
    "load_rgba",
    "main",
    "run_full_asset_audit",
]
