"""Dependency-free raster comparison helpers (ImageMagick supplies decoding)."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from typing import Any


def find_magick() -> str | None:
    for candidate in (shutil.which("magick"), "/opt/homebrew/bin/magick", "/usr/local/bin/magick"):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def load_rgba(path: str, magick: str | None = None) -> tuple[int, int, bytes]:
    magick = magick or find_magick()
    if not magick:
        raise RuntimeError("ImageMagick 'magick' is required for image comparison")
    target = path if path.endswith("]") else f"{path}[0]"
    identify = subprocess.run(
        [magick, "identify", "-format", "%w %h\n", target],
        check=True,
        capture_output=True,
        text=True,
    )
    parts = identify.stdout.strip().split()
    if len(parts) < 2:
        raise RuntimeError(f"Could not determine dimensions for {path!r}")
    width, height = int(parts[0]), int(parts[1])
    expected = width * height * 4
    proc = subprocess.run(
        [magick, "-background", "none", target, "-alpha", "on", "-depth", "8", "RGBA:-"],
        check=True,
        capture_output=True,
    )
    raw = proc.stdout
    if len(raw) < expected:
        raise RuntimeError(f"Could not decode {path!r} as an RGBA image")
    return width, height, raw[:expected]


def _crop(data: bytes, width: int, x: int, y: int, w: int, h: int) -> bytes:
    return b"".join(data[((y + row) * width + x) * 4:((y + row) * width + x + w) * 4] for row in range(h))


def _gray(px: bytes, i: int) -> float:
    return 0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]


def _downscale_for_perceptual(
    b: bytes, a: bytes, mask: bytes | None, w: int, h: int, magick: str | None = None
) -> tuple[bytes, bytes, bytes | None, int, int]:
    """Downscale proportionally so max(w, h) <= 512 for fast & macro-level SSIM."""
    max_dim = max(w, h)
    if max_dim <= 512:
        return b, a, mask, w, h
    scale = 512.0 / max_dim
    pw = max(1, round(w * scale))
    ph = max(1, round(h * scale))
    magick = magick or find_magick()
    if magick:
        try:
            cmd = [magick, "-size", f"{w}x{h}", "-depth", "8", "RGBA:-", "-resize", f"{pw}x{ph}!", "-depth", "8", "RGBA:-"]
            b_scaled = subprocess.run(cmd, input=b, capture_output=True, check=True).stdout
            a_scaled = subprocess.run(cmd, input=a, capture_output=True, check=True).stdout
            if mask:
                mask_cmd = [magick, "-size", f"{w}x{h}", "-depth", "8", "RGBA:-", "-filter", "Point", "-resize", f"{pw}x{ph}!", "-depth", "8", "RGBA:-"]
                mask_scaled = subprocess.run(mask_cmd, input=mask, capture_output=True, check=True).stdout
            else:
                mask_scaled = None
            if len(b_scaled) >= pw * ph * 4 and len(a_scaled) >= pw * ph * 4:
                return (
                    b_scaled[:pw * ph * 4],
                    a_scaled[:pw * ph * 4],
                    mask_scaled[:pw * ph * 4] if mask_scaled else None,
                    pw,
                    ph,
                )
        except Exception:
            pass

    # Pure Python nearest-neighbor fallback (for tests or environments without magick access)
    scale_x = w / pw
    scale_y = h / ph
    b_out = bytearray(pw * ph * 4)
    a_out = bytearray(pw * ph * 4)
    m_out = bytearray(pw * ph * 4) if mask else None
    for py in range(ph):
        sy = min(int(py * scale_y), h - 1)
        for px in range(pw):
            sx = min(int(px * scale_x), w - 1)
            src_idx = (sy * w + sx) * 4
            dst_idx = (py * pw + px) * 4
            b_out[dst_idx:dst_idx + 4] = b[src_idx:src_idx + 4]
            a_out[dst_idx:dst_idx + 4] = a[src_idx:src_idx + 4]
            if mask and m_out:
                m_out[dst_idx:dst_idx + 4] = mask[src_idx:src_idx + 4]
    return bytes(b_out), bytes(a_out), bytes(m_out) if m_out else None, pw, ph


def compare_images(
    before: str,
    after: str,
    *,
    common_area: bool = False,
    tolerance: int = 0,
    ignore: str | None = None,
    diff_out: str | None = None,
    min_similarity: float = 0.90,
    tile_size: int = 16,
) -> dict[str, Any]:
    if not 0 <= tolerance <= 255:
        raise ValueError("tolerance must be between 0 and 255")
    if not 0.0 <= min_similarity <= 1.0:
        raise ValueError("min_similarity must be between 0.0 and 1.0")

    bw, bh, b = load_rgba(before)
    aw, ah, a = load_rgba(after)
    w, h = min(bw, aw), min(bh, ah)

    if not common_area and (bw, bh) != (aw, ah):
        return {
            "comparable": False,
            "identical": False,
            "sizes": {"before": {"width": bw, "height": bh}, "after": {"width": aw, "height": ah}},
            "shared_area": None,
            "total_pixels": 0,
            "differing_pixels": 0,
            "difference_fraction": None,
            "max_channel_delta": 0,
            "changed_bounding_box": None,
            "layout_similarity": 0.0,
            "perceptual_similarity": 0.0,
            "ignored_tiles": 0,
            "passed": False,
        }

    b, a = _crop(b, bw, 0, 0, w, h), _crop(a, aw, 0, 0, w, h)
    mask = None
    if ignore:
        mw, mh, mask_raw = load_rgba(ignore)
        if (mw, mh) == (w, h):
            mask = mask_raw
        elif common_area and mw >= w and mh >= h:
            mask = _crop(mask_raw, mw, 0, 0, w, h)
        else:
            raise ValueError(f"ignore mask must be at least {w}x{h}; got {mw}x{mh}")

    differing = 0
    max_delta = 0
    masked_pixels = 0
    minx, miny, maxx, maxy = w, h, -1, -1

    for p in range(w * h):
        i = p * 4
        if mask and min(mask[i:i + 4]) >= 250:
            masked_pixels += 1
            continue
        delta = max(abs(b[i + c] - a[i + c]) for c in range(4))
        max_delta = max(max_delta, delta)
        if delta > tolerance:
            x, y = p % w, p // w
            minx, miny, maxx, maxy = min(minx, x), min(miny, y), max(maxx, x), max(maxy, y)
            differing += 1

    # Layout: fraction of 16px tiles whose mean RGB color distance is <= threshold (24)
    layout_ok = 0
    layout_total = 0
    ignored = 0
    tile = max(1, tile_size)
    for y0 in range(0, h, tile):
        for x0 in range(0, w, tile):
            vals_b = [0.0, 0.0, 0.0]
            vals_a = [0.0, 0.0, 0.0]
            n = 0
            for y in range(y0, min(y0 + tile, h)):
                for x in range(x0, min(x0 + tile, w)):
                    i = (y * w + x) * 4
                    if mask and min(mask[i:i + 4]) >= 250:
                        continue
                    for c in range(3):
                        vals_b[c] += b[i + c]
                        vals_a[c] += a[i + c]
                    n += 1
            if not n:
                ignored += 1
                continue
            layout_total += 1
            distance = math.sqrt(sum(((vals_b[c] - vals_a[c]) / n) ** 2 for c in range(3)))
            layout_ok += int(distance <= 24)
    layout = layout_ok / layout_total if layout_total else 1.0

    # Perceptual: downscale so max dimension <= 512px, then compute SSIM over 8x8 windows
    pb, pa, pmask, pw, ph = _downscale_for_perceptual(b, a, mask, w, h)
    ssim_values = []
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2

    for y0 in range(0, ph, 8):
        for x0 in range(0, pw, 8):
            xs = []
            ys = []
            for y in range(y0, min(y0 + 8, ph)):
                for x in range(x0, min(x0 + 8, pw)):
                    i = (y * pw + x) * 4
                    if not pmask or min(pmask[i:i + 4]) < 250:
                        xs.append(_gray(pb, i))
                        ys.append(_gray(pa, i))
            if not xs:
                continue
            mx = sum(xs) / len(xs)
            my = sum(ys) / len(ys)
            vx = sum((v - mx) ** 2 for v in xs) / len(xs)
            vy = sum((v - my) ** 2 for v in ys) / len(ys)
            cov = sum((x_val - mx) * (y_val - my) for x_val, y_val in zip(xs, ys)) / len(xs)
            num = (2 * mx * my + c1) * (2 * cov + c2)
            den = (mx * mx + my * my + c1) * (vx + vy + c2)
            ssim_values.append(num / den)

    perceptual = max(0.0, min(1.0, sum(ssim_values) / len(ssim_values))) if ssim_values else 1.0

    total = w * h - masked_pixels

    result = {
        "comparable": True,
        "identical": differing == 0,
        "sizes": {"before": {"width": bw, "height": bh}, "after": {"width": aw, "height": ah}},
        "shared_area": {"width": w, "height": h},
        "total_pixels": total,
        "differing_pixels": differing,
        "difference_fraction": (differing / total) if total else 0.0,
        "max_channel_delta": max_delta,
        "changed_bounding_box": (
            {"x": minx, "y": miny, "width": maxx - minx + 1, "height": maxy - miny + 1}
            if differing > 0
            else None
        ),
        "layout_similarity": round(layout, 4),
        "perceptual_similarity": round(perceptual, 4),
        "ignored_tiles": ignored,
        "passed": bool(layout >= min_similarity and perceptual >= min_similarity),
    }

    if diff_out:
        _write_diff(diff_out, b, a, w, h, tolerance, mask)

    return result


def _write_diff(
    path: str,
    before: bytes,
    after: bytes,
    width: int,
    height: int,
    tolerance: int,
    mask: bytes | None,
) -> None:
    out = bytearray(width * height * 4)
    for p in range(width * height):
        i = p * 4
        if mask and min(mask[i:i + 4]) >= 250:
            rgb = (35, 45, 55)  # Soft slate for masked/ignored pixels
        elif max(abs(before[i + c] - after[i + c]) for c in range(4)) > tolerance:
            rgb = (255, 32, 32)  # Highlight differing pixels in vibrant red
        else:
            rgb = (45, 45, 45)  # Dark gray background for identical pixels
        out[i:i + 4] = bytes((*rgb, 255))

    magick = find_magick()
    if not magick:
        raise RuntimeError("ImageMagick 'magick' is required to write --diff-out")
    dir_name = os.path.dirname(os.path.abspath(path))
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    subprocess.run(
        [magick, "-size", f"{width}x{height}", "-depth", "8", "RGBA:-", path],
        input=bytes(out),
        check=True,
    )


def compare_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pixellens compare", description="Compare two raster images")
    parser.add_argument("before", help="Reference / before image path")
    parser.add_argument("after", help="Comparison / after image path")
    parser.add_argument("--common-area", action="store_true", help="Crop to common area if dimensions differ")
    parser.add_argument("--tolerance", type=int, default=0, help="Per-channel color delta tolerance (0..255)")
    parser.add_argument("--ignore", help="Path to noise mask PNG where white tiles/pixels are ignored")
    parser.add_argument("--diff-out", help="Path to write visual delta PNG")
    parser.add_argument("--min-similarity", type=float, default=0.90, help="Passing similarity threshold (default: 0.90)")
    parser.add_argument("--json", action="store_true", help="Format JSON output with 2-space indentation")
    args = parser.parse_args(argv)
    try:
        result = compare_images(
            args.before,
            args.after,
            common_area=args.common_area,
            tolerance=args.tolerance,
            ignore=args.ignore,
            diff_out=args.diff_out,
            min_similarity=args.min_similarity,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2 if args.json else None))
    return 0 if result["passed"] else 1
