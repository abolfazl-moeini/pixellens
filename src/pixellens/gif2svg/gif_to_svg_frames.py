#!/usr/bin/env python3
"""
Convert an animated GIF/APNG icon into a pure-CSS frame-by-frame Animated SVG.
Requires ImageMagick (`magick`) on PATH or /opt/homebrew/bin/magick; zero Python dependencies.

Features:
  - Automatic de-dithering & 2-layer separation (solid black + 50% dithered gray)
  - Pure-Python Marching Squares -> Laplacian smoothing -> RDP -> Catmull-Rom cubic Beziers
  - Automatic deduplication of consecutive near-identical frames (preserves exact timing)
  - Pure CSS @keyframes output (no JavaScript; works inside <img> tags)

Usage:
    python3 gif_to_svg_frames.py input.gif output.svg [--step 1] [--no-dedither]
"""

import argparse
import glob
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
        magic = f.readline().strip()
        assert magic == b"P5", f"Expected P5 PGM, got {magic}"
        line = f.readline()
        while line.startswith(b"#"):
            line = f.readline()
        w, h = map(int, line.split())
        f.readline()
        raw = f.read(w * h)
    return w, h, raw


def extract_contours(w, h, raw, thresh=128):
    pw, ph = w + 2, h + 2
    grid = bytearray(pw * ph)
    for y in range(h):
        row_off = y * w
        dst_off = (y + 1) * pw + 1
        for x in range(w):
            if raw[row_off + x] >= thresh:
                grid[dst_off + x] = 1

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
        if len(pts) >= 6:
            fpts = [((px / 2.0) - 1.0, (py / 2.0) - 1.0) for (px, py) in pts]
            area = 0.0
            n = len(fpts)
            for i in range(n):
                x1, y1 = fpts[i]
                x2, y2 = fpts[(i + 1) % n]
                area += x1 * y2 - x2 * y1
            if abs(area) * 0.5 >= 35.0:
                loops.append(fpts)
    return loops


def smooth_loop(pts, iters=3):
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


def rdp_closed(pts, eps=1.25):
    n = len(pts)
    if n <= 4:
        return pts
    p0 = pts[0]
    far_i = max(range(1, n), key=lambda i: (pts[i][0] - p0[0]) ** 2 + (pts[i][1] - p0[1]) ** 2)
    a = rdp_open(pts[:far_i + 1], eps)
    b = rdp_open(pts[far_i:] + [pts[0]], eps)
    res = a[:-1] + b[:-1]
    return res if len(res) >= 3 else pts[:3]


def loop_to_bezier_d(pts):
    pts = smooth_loop(pts, iters=3)
    pts = rdp_closed(pts, eps=1.25)
    n = len(pts)
    if n < 3:
        return ""
    def fmt(v):
        return f"{v:.1f}".rstrip("0").rstrip(".")
    parts = [f"M{fmt(pts[0][0])},{fmt(pts[0][1])}"]
    tension = 6.0
    for i in range(n):
        p0 = pts[(i - 1) % n]
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n]
        c1x = p1[0] + (p2[0] - p0[0]) / tension
        c1y = p1[1] + (p2[1] - p0[1]) / tension
        c2x = p2[0] - (p3[0] - p1[0]) / tension
        c2y = p2[1] - (p3[1] - p1[1]) / tension
        parts.append(f"C{fmt(c1x)},{fmt(c1y)} {fmt(c2x)},{fmt(c2y)} {fmt(p2[0])},{fmt(p2[1])}")
    parts.append("Z")
    return "".join(parts)


def mask_to_d(pgm_path):
    w, h, raw = read_pgm(pgm_path)
    loops = extract_contours(w, h, raw)
    return "".join(loop_to_bezier_d(lp) for lp in loops)


def get_frame_delays(magick, input_path):
    out = subprocess.check_output(
        [magick, "identify", "-format", "%w %h %T\n", input_path],
        text=True,
    ).strip().splitlines()
    w, h = map(int, out[0].split()[:2])
    # Delay in centiseconds (1/100s); default 4cs if 0
    delays = [max(int(line.split()[2]), 2) / 100.0 for line in out if line.strip()]
    return w, h, delays


def convert_gif_to_svg(input_path, output_path, step=1, dedither=True):
    magick = find_magick()
    w, h, delays = get_frame_delays(magick, input_path)
    total_dur = sum(delays)

    with tempfile.TemporaryDirectory() as tmp:
        frames_dir = os.path.join(tmp, "frames")
        masks_dir = os.path.join(tmp, "masks")
        os.makedirs(frames_dir)
        os.makedirs(masks_dir)

        subprocess.run(
            [magick, input_path, "-coalesce", os.path.join(frames_dir, "f_%04d.png")],
            check=True,
        )
        frame_files = sorted(glob.glob(os.path.join(frames_dir, "f_*.png")))

        # Accumulate time windows according to step
        sampled = []
        t_cursor = 0.0
        for i, fpath in enumerate(frame_files):
            d = delays[i] if i < len(delays) else 0.02
            if i % step == 0:
                sampled.append([fpath, t_cursor, t_cursor + d])
            else:
                sampled[-1][2] += d
            t_cursor += d

        # Trace each sampled frame and deduplicate identical consecutive SVG paths
        unique_frames = []
        for idx, (src, t0, t1) in enumerate(sampled):
            b_pgm = os.path.join(masks_dir, f"f_{idx:04d}_b.pgm")
            if dedither:
                g_pgm = os.path.join(masks_dir, f"f_{idx:04d}_g.pgm")
                subprocess.run([
                    magick, src, "-alpha", "extract",
                    "-blur", "0x2.5", "-threshold", "82%",
                    "-morphology", "Dilate", "Disk:1.5",
                    "-blur", "0x0.8", "-threshold", "50%",
                    f"PGM:{b_pgm}",
                ], check=True)
                subprocess.run([
                    magick, src, "-alpha", "extract",
                    "-blur", "0x2.5", "-threshold", "22%",
                    "(", b_pgm, "-morphology", "Dilate", "Disk:6", ")",
                    "-compose", "MinusSrc", "-composite",
                    "-morphology", "Open", "Disk:2.5",
                    "-blur", "0x0.8", "-threshold", "50%",
                    f"PGM:{g_pgm}",
                ], check=True)
                d_gray = mask_to_d(g_pgm)
                d_black = mask_to_d(b_pgm)
                elems = []
                if d_gray:
                    elems.append(f'<path fill="#000" fill-opacity=".5" fill-rule="evenodd" d="{d_gray}"/>')
                if d_black:
                    elems.append(f'<path fill="#000" fill-rule="evenodd" d="{d_black}"/>')
            else:
                subprocess.run([
                    magick, src, "-alpha", "extract",
                    "-blur", "0x0.8", "-threshold", "50%",
                    f"PGM:{b_pgm}",
                ], check=True)
                d_black = mask_to_d(b_pgm)
                elems = [f'<path fill="#000" fill-rule="evenodd" d="{d_black}"/>'] if d_black else []

            content = "".join(elems)
            if unique_frames and unique_frames[-1][0] == content:
                unique_frames[-1][2] = t1
            else:
                unique_frames.append([content, t0, t1])

        css_rules = [
            f".f{{visibility:hidden;animation-duration:{total_dur:.3f}s;"
            "animation-iteration-count:infinite;animation-timing-function:step-end}"
        ]
        frame_groups = []
        for idx, (content, t0, t1) in enumerate(unique_frames):
            fid = f"f{idx}"
            vis_attr = ' visibility="visible"' if idx == 0 else ' visibility="hidden"'
            frame_groups.append(f'<g id="{fid}" class="f"{vis_attr}>{content}</g>')
            p0 = (t0 / total_dur) * 100.0
            p1 = (t1 / total_dur) * 100.0
            if idx == 0:
                css_rules.append(f"#{fid}{{animation-name:k{idx}}}@keyframes k{idx}{{0%{{visibility:visible}}{p1:.3f}%{{visibility:hidden}}}}")
            elif idx == len(unique_frames) - 1:
                css_rules.append(f"#{fid}{{animation-name:k{idx}}}@keyframes k{idx}{{0%{{visibility:hidden}}{p0:.3f}%{{visibility:visible}}}}")
            else:
                css_rules.append(f"#{fid}{{animation-name:k{idx}}}@keyframes k{idx}{{0%{{visibility:hidden}}{p0:.3f}%{{visibility:visible}}{p1:.3f}%{{visibility:hidden}}}}")

        css_rules.append("@media (prefers-reduced-motion:reduce){.f{animation:none!important}#f0{visibility:visible!important}}")

        svg_out = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n'
            f'<style>{"".join(css_rules)}</style>\n'
            + "\n".join(frame_groups) + "\n"
            '</svg>\n'
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(svg_out)
        print(f"Generated {output_path}: {len(svg_out):,} bytes ({len(unique_frames)} unique frames, {total_dur:.2f}s)")


def main():
    parser = argparse.ArgumentParser(description="Convert GIF/APNG icon to pure-CSS frame-by-frame Animated SVG.")
    parser.add_argument("input", help="Path to input GIF/APNG file")
    parser.add_argument("output", help="Path to output .svg file")
    parser.add_argument("--step", type=int, default=1, help="Frame sampling step (1=full FPS, 2=half FPS)")
    parser.add_argument("--no-dedither", action="store_true", help="Disable 2-layer de-dithering separation")
    args = parser.parse_args()
    convert_gif_to_svg(args.input, args.output, step=args.step, dedither=not args.no_dedither)


if __name__ == "__main__":
    main()
