"""PixelLens — local-first media converter and pixel/layout/SSIM diff engine."""

from __future__ import annotations

__version__ = "0.1.0"

from pixellens.cli import inspect_file, main
from pixellens.compare.pixels import compare_images, load_rgba

__all__ = ["__version__", "compare_images", "inspect_file", "load_rgba", "main"]
