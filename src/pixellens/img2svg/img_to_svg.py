#!/usr/bin/env python3
"""
Convert a static raster image (PNG, JPG, WebP, etc.) into a true vector SVG.
Requires ImageMagick (`magick`) on PATH or /opt/homebrew/bin/magick; zero Python dependencies.

Modes:
  - mono:  Best for 2D monochrome/flat icons (upscales to 512px for crisp Bezier curves)
  - color: Stacked multi-color vectorization with clamped sharp-corner cubic Beziers
  - auto:  Detects whether the image is monochrome or multi-color automatically

Usage:
    python3 img_to_svg.py input.png output.svg [--mode auto|mono|color] [--colors 24] [--size 360]
"""

import argparse
import math
import os
import shutil
import subprocess
import tempfile


def find_magick():
    for cand in (shutil.which("magick"), "/opt/homebrew/bin/magick", "/usr/local/bin/magick"):
        if cand and os.path.exists(cand):
            return cand
    raise RuntimeError("ImageMagick (`magick`) not found. Install via: brew install imagemagick")


def read_pgm(path):
    with open(path, "rb") as f:
        assert f.readline().strip() == b"P5"
        line = f.readline()
        while line.startswith(b"#"):
            line = f.readline()
        w, h = map(int, line.split())
        f.readline()
        return w, h, f.read(w * h)


def read_ppm(path):
    with open(path, "rb") as f:
        assert f.readline().strip() == b"P6"
        line = f.readline()
        while line.startswith(b"#"):
            line = f.readline()
        w, h = map(int, line.split())
        f.readline()
        return w, h, f.read(w * h * 3)


def extract_contours(w, h, grid_bytes, min_area=12.0):
    pw, ph = w + 2, h + 2
    grid = bytearray(pw * ph)
    for y in range(h):
        ro = y * w
        do = (y + 1) * pw + 1
        for x in range(w):
            if grid_bytes[ro + x]:
                grid[do + x] = 1

    adj = {}
    def add_edge(p1, p2):
        adj.setdefault(p1, []).append(p2)

    for y in range(ph - 1):
        y0 = y * pw
        y1 = (y + 1) * pw
        for x in range(pw - 1):
            c = (grid[y0 + x] << 3) | (grid[y0 + x + 1] << 2) | (grid[y1 + x + 1] << 1) | grid[y1 + x]
            if c == 0 or c == 15:
                continue
            T = (2 * x + 1, 2 * y)
            R = (2 * x + 2, 2 * y + 1)
            B = (2 * x + 1, 2 * y + 2)
            L = (2 * x, 2 * y + 1)
            if c == 1: add_edge(L, B)
            elif c == 2: add_edge(B, R)
            elif c == 3: add_edge(L, R)
            elif c == 4: add_edge(R, T)
            elif c == 5: add_edge(L, T); add_edge(R, B)
            elif c == 6: add_edge(B, T)
            elif c == 7: add_edge(L, T)
            elif c == 8: add_edge(T, L)
            elif c == 9: add_edge(T, B)
            elif c == 10: add_edge(T, R); add_edge(B, L)
            elif c == 11: add_edge(T, R)
            elif c == 12: add_edge(R, L)
            elif c == 13: add_edge(R, B)
            elif c == 14: add_edge(B, L)

    loops = []
    while adj:
        start = next(iter(adj))
        pts = [start]
        cur = start
        while True:
            nxts = adj.get(cur)
            if not nxts:
                break
            nxt = nxts.pop()
            if not nxts:
                del adj[cur]
            if nxt == start:
                break
            pts.append(nxt)
            cur = nxt
        if len(pts) >= 4:
            fpts = [((px / 2.0) - 1.0, (py / 2.0) - 1.0) for (px, py) in pts]
            area = 0.0
            n = len(fpts)
            for i in range(n):
                x1, y1 = fpts[i]
                x2, y2 = fpts[(i + 1) % n]
                area += x1 * y2 - x2 * y1
            if abs(area) * 0.5 >= min_area:
                loops.append(fpts)
    return loops


def smooth_loop(pts, iters=2):
    n = len(pts)
    cur = pts
    for _ in range(iters):
        cur = [
            (
                0.25 * cur[(i - 1) % n][0] + 0.5 * cur[i][0] + 0.25 * cur[(i + 1) % n][0],
                0.25 * cur[(i - 1) % n][1] + 0.5 * cur[i][1] + 0.25 * cur[(i + 1) % n][1],
            )
            for i in range(n)
        ]
    return cur


def rdp_open(pts, eps):
    if len(pts) <= 2:
        return pts
    x1, y1 = pts[0]
    x2, y2 = pts[-1]
    dx, dy = x2 - x1, y2 - y1
    denom = math.hypot(dx, dy)
    max_d, idx = -1.0, 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        d = math.hypot(px - x1, py - y1) if denom == 0 else abs(dy * px - dx * py + x2 * y1 - y2 * x1) / denom
        if d > max_d:
            max_d, idx = d, i
    if max_d > eps:
        return rdp_open(pts[:idx + 1], eps)[:-1] + rdp_open(pts[idx:], eps)
    return [pts[0], pts[-1]]


def rdp_closed(pts, eps=1.2):
    n = len(pts)
    if n <= 4:
        return pts
    p0 = pts[0]
    far_i = max(range(1, n), key=lambda i: (pts[i][0] - p0[0]) ** 2 + (pts[i][1] - p0[1]) ** 2)
    a = rdp_open(pts[:far_i + 1], eps)
    b = rdp_open(pts[far_i:] + [pts[0]], eps)
    res = a[:-1] + b[:-1]
    return res if len(res) >= 3 else pts[:3]


def loop_to_bezier_d(pts, scale=1.0, eps=1.2, smooth_iters=2):
    pts = smooth_loop(pts, iters=smooth_iters)
    pts = rdp_closed(pts, eps=eps)
    n = len(pts)
    if n < 3:
        return ""
    def fmt(v):
        return f"{v * scale:.1f}".rstrip("0").rstrip(".")
    parts = [f"M{fmt(pts[0][0])},{fmt(pts[0][1])}"]
    tension = 6.0
    for i in range(n):
        p0 = pts[(i - 1) % n]
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n]
        seg = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        d1x, d1y = (p2[0] - p0[0]) / tension, (p2[1] - p0[1]) / tension
        d2x, d2y = (p3[0] - p1[0]) / tension, (p3[1] - p1[1]) / tension
        l1 = math.hypot(d1x, d1y)
        l2 = math.hypot(d2x, d2y)
        max_h = seg * 0.35
        if l1 > max_h and l1 > 0:
            d1x *= max_h / l1
            d1y *= max_h / l1
        if l2 > max_h and l2 > 0:
            d2x *= max_h / l2
            d2y *= max_h / l2
        c1x, c1y = p1[0] + d1x, p1[1] + d1y
        c2x, c2y = p2[0] - d2x, p2[1] - d2y
        parts.append(f"C{fmt(c1x)},{fmt(c1y)} {fmt(c2x)},{fmt(c2y)} {fmt(p2[0])},{fmt(p2[1])}")
    parts.append("Z")
    return "".join(parts)


def detect_mode(magick, in_path):
    # Check unique RGB colors after ignoring transparency
    out = subprocess.check_output(
        [magick, in_path, "-alpha", "off", "-resize", "64x64", "-colorspace", "RGB", "-format", "%k", "info:"],
        text=True,
    ).strip()
    try:
        uniq = int(out)
    except ValueError:
        uniq = 100
    return "mono" if uniq <= 8 else "color"


def vectorize_mono(magick, in_path, out_path, eps=1.2):
    with tempfile.TemporaryDirectory() as tmp:
        pgm = os.path.join(tmp, "m.pgm")
        subprocess.run([
            magick, in_path,
            "-alpha", "extract",
            "-resize", "512x512",
            "-blur", "0x1.2", "-threshold", "50%",
            f"PGM:{pgm}",
        ], check=True)
        w, h, raw = read_pgm(pgm)
        mask = bytes(1 if b >= 128 else 0 for b in raw)
        loops = extract_contours(w, h, mask, min_area=20.0)
        d = "".join(loop_to_bezier_d(lp, scale=1.0, eps=eps, smooth_iters=3) for lp in loops)
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n'
            f'  <path fill="#000" fill-rule="evenodd" d="{d}"/>\n'
            '</svg>\n'
        )
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(svg)
        print(f"[MONO] {in_path} -> {out_path} ({len(svg):,} bytes, {len(loops)} contours)")


def vectorize_color(magick, in_path, out_path, n_colors=24, work_size=360, eps=1.35):
    with tempfile.TemporaryDirectory() as tmp:
        rgb_ppm = os.path.join(tmp, "q.ppm")
        alpha_pgm = os.path.join(tmp, "a.pgm")

        subprocess.run([
            magick, in_path,
            "-resize", f"{work_size}x{work_size}",
            "-alpha", "extract",
            "-blur", "0x0.7", "-threshold", "45%",
            f"PGM:{alpha_pgm}",
        ], check=True)

        subprocess.run([
            magick, in_path,
            "-resize", f"{work_size}x{work_size}",
            "-background", "#808080", "-alpha", "remove", "-alpha", "off",
            "-statistic", "Median", "3x3",
            "+dither", "-colors", str(n_colors),
            f"PPM:{rgb_ppm}",
        ], check=True)

        w, h, alpha = read_pgm(alpha_pgm)
        w2, h2, rgb = read_ppm(rgb_ppm)
        assert (w, h) == (w2, h2)

        color_counts = {}
        pix_colors = [None] * (w * h)
        for idx in range(w * h):
            if alpha[idx] >= 128:
                c = (rgb[idx * 3], rgb[idx * 3 + 1], rgb[idx * 3 + 2])
                pix_colors[idx] = c
                color_counts[c] = color_counts.get(c, 0) + 1

        sorted_colors = sorted(color_counts.items(), key=lambda kv: kv[1], reverse=True)
        scale_x = 512.0 / max(w, h)
        vw, vh = round(w * scale_x), round(h * scale_x)
        paths = []

        if sorted_colors:
            dom_c = sorted_colors[0][0]
            sil_mask = bytes(1 if alpha[i] >= 128 else 0 for i in range(w * h))
            sil_loops = extract_contours(w, h, sil_mask, min_area=15.0)
            sil_d = "".join(loop_to_bezier_d(lp, scale=scale_x, eps=eps, smooth_iters=2) for lp in sil_loops)
            if sil_d:
                paths.append(f'  <path fill="#{dom_c[0]:02x}{dom_c[1]:02x}{dom_c[2]:02x}" fill-rule="evenodd" d="{sil_d}"/>')

        for color, cnt in sorted_colors:
            if cnt < 18:
                continue
            mask = bytes(1 if pix_colors[i] == color else 0 for i in range(w * h))
            loops = extract_contours(w, h, mask, min_area=10.0)
            if not loops:
                continue
            d = "".join(loop_to_bezier_d(lp, scale=scale_x, eps=eps, smooth_iters=2) for lp in loops)
            if d:
                hex_c = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
                paths.append(f'  <path fill="{hex_c}" fill-rule="evenodd" d="{d}"/>')

        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vw} {vh}" width="{vw}" height="{vh}">\n'
            + "\n".join(paths) + "\n"
            '</svg>\n'
        )
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(svg)
        print(f"[COLOR] {in_path} -> {out_path} ({len(svg):,} bytes, {len(paths)} layers)")


def main():
    parser = argparse.ArgumentParser(description="Convert static raster image (PNG/JPG/WebP) to vector SVG.")
    parser.add_argument("input", help="Path to input image file")
    parser.add_argument("output", help="Path to output .svg file")
    parser.add_argument("--mode", choices=["auto", "mono", "color"], default="auto", help="Tracing mode (default: auto)")
    parser.add_argument("--colors", type=int, default=24, help="Max quantized colors in color mode (default: 24)")
    parser.add_argument("--size", type=int, default=360, help="Working resolution for color mode (default: 360)")
    parser.add_argument("--eps", type=float, default=1.35, help="RDP curve simplification tolerance (default: 1.35)")
    args = parser.parse_args()

    magick = find_magick()
    mode = detect_mode(magick, args.input) if args.mode == "auto" else args.mode
    if mode == "mono":
        vectorize_mono(magick, args.input, args.output, eps=min(args.eps, 1.2))
    else:
        vectorize_color(magick, args.input, args.output, n_colors=args.colors, work_size=args.size, eps=args.eps)


if __name__ == "__main__":
    main()
