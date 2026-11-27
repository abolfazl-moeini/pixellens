#!/usr/bin/env python3
"""
Centerline (stroke-based) extractor and pure-CSS Animated SVG builder for stroke-based animated GIF icons.
Requires ImageMagick (`magick`) on PATH or /opt/homebrew/bin/magick; zero Python dependencies.

Pipeline:
  1. De-dither and separate solid-black vs 50%-opacity gray layers via ImageMagick.
  2. Analyze frame-by-frame pixel area & bounding boxes to detect scale bounce & trim timing.
  3. Extract 1-pixel topological centerlines using Zhang-Suen thinning + BFS diameter tracing.
  4. Fit smooth Catmull-Rom cubic Bezier strokes and animate with pure CSS `stroke-dashoffset` & `scale`.

Usage:
    # Analyze any GIF icon's motion timeline (prints frame stats):
    python3 gif_to_stroke_svg.py input.gif --analyze-only

    # Build the ultra-compact (~7 KB), 60/120fps pure-CSS stroke Animated SVG:
    python3 gif_to_stroke_svg.py input.gif output.svg [--stroke-width 31] [--closed-frame 12]
"""

import argparse
import glob
import math
import os
import shutil
import subprocess
import tempfile
from collections import deque


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
        raw = f.read(w * h)
    return w, h, raw


def bbox_and_count(w, h, raw):
    cnt = 0
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        off = y * w
        for x in range(w):
            if raw[off + x] >= 128:
                cnt += 1
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
    return cnt, (minx, miny, maxx, maxy) if cnt else None


def zhang_suen_thin(w, h, raw):
    img = [[1 if raw[y * w + x] >= 128 else 0 for x in range(w)] for y in range(h)]
    for x in range(w): img[0][x] = img[h - 1][x] = 0
    for y in range(h): img[y][0] = img[y][w - 1] = 0
    active = {(x, y) for y in range(1, h - 1) for x in range(1, w - 1) if img[y][x] == 1}
    while True:
        changed = False
        for step in (0, 1):
            to_remove = []
            for x, y in active:
                p2 = img[y - 1][x]; p3 = img[y - 1][x + 1]; p4 = img[y][x + 1]
                p5 = img[y + 1][x + 1]; p6 = img[y + 1][x]; p7 = img[y + 1][x - 1]
                p8 = img[y][x - 1]; p9 = img[y - 1][x - 1]
                B = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
                if B < 2 or B > 6:
                    continue
                A = (
                    (p2 == 0 and p3 == 1) + (p3 == 0 and p4 == 1) +
                    (p4 == 0 and p5 == 1) + (p5 == 0 and p6 == 1) +
                    (p6 == 0 and p7 == 1) + (p7 == 0 and p8 == 1) +
                    (p8 == 0 and p9 == 1) + (p9 == 0 and p2 == 1)
                )
                if A != 1:
                    continue
                if step == 0:
                    if p2 * p4 * p6 != 0 or p4 * p6 * p8 != 0:
                        continue
                else:
                    if p2 * p4 * p8 != 0 or p2 * p6 * p8 != 0:
                        continue
                to_remove.append((x, y))
            if to_remove:
                changed = True
                for x, y in to_remove:
                    img[y][x] = 0
                    active.remove((x, y))
        if not changed:
            break
    return active


def connected_components(pts):
    pts = set(pts)
    comps = []
    while pts:
        start = next(iter(pts))
        comp = set()
        q = deque([start])
        pts.remove(start)
        while q:
            x, y = q.popleft()
            comp.add((x, y))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nb = (x + dx, y + dy)
                    if nb in pts:
                        pts.remove(nb)
                        q.append(nb)
        if len(comp) >= 8:
            comps.append(comp)
    return comps


def bfs_farthest(comp, start):
    dist = {start: 0}
    parent = {start: None}
    q = deque([start])
    far = start
    while q:
        x, y = q.popleft()
        d = dist[(x, y)]
        if d > dist[far]:
            far = (x, y)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nb = (x + dx, y + dy)
                if nb in comp and nb not in dist:
                    dist[nb] = d + 1
                    parent[nb] = (x, y)
                    q.append(nb)
    return far, parent


def diameter_path(comp):
    a, _ = bfs_farthest(comp, next(iter(comp)))
    b, parent = bfs_farthest(comp, a)
    path = []
    cur = b
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.reverse()
    return path


def polyline_len(pts):
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))


def smooth_open(pts, iters=6):
    cur = [(float(x), float(y)) for x, y in pts]
    n = len(cur)
    if n < 3:
        return cur
    for _ in range(iters):
        nxt = [cur[0]]
        for i in range(1, n - 1):
            nxt.append((
                0.25 * cur[i - 1][0] + 0.5 * cur[i][0] + 0.25 * cur[i + 1][0],
                0.25 * cur[i - 1][1] + 0.5 * cur[i][1] + 0.25 * cur[i + 1][1],
            ))
        nxt.append(cur[-1])
        cur = nxt
    return cur


def rdp_open(pts, eps=1.5):
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


def cubic_len(p1, c1, c2, p2, steps=16):
    total = 0.0
    px, py = p1
    for s in range(1, steps + 1):
        t = s / steps
        mt = 1 - t
        x = mt**3 * p1[0] + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * p2[0]
        y = mt**3 * p1[1] + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * p2[1]
        total += math.hypot(x - px, y - py)
        px, py = x, y
    return total


def path_to_bezier(pts, eps=1.8, smooth_iters=6):
    pts = smooth_open(pts, iters=smooth_iters)
    pts = rdp_open(pts, eps=eps)
    n = len(pts)
    if n < 2:
        return "", 0.0
    def fmt(v):
        return f"{v:.1f}".rstrip("0").rstrip(".")
    parts = [f"M{fmt(pts[0][0])},{fmt(pts[0][1])}"]
    if n == 2:
        parts.append(f"L{fmt(pts[1][0])},{fmt(pts[1][1])}")
        return "".join(parts), math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
    tension = 6.0
    total_len = 0.0
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
        c1 = (p1[0] + (p2[0] - p0[0]) / tension, p1[1] + (p2[1] - p0[1]) / tension)
        c2 = (p2[0] - (p3[0] - p1[0]) / tension, p2[1] - (p3[1] - p1[1]) / tension)
        total_len += cubic_len(p1, c1, c2, p2)
        parts.append(f"C{fmt(c1[0])},{fmt(c1[1])} {fmt(c2[0])},{fmt(c2[1])} {fmt(p2[0])},{fmt(p2[1])}")
    return "".join(parts), total_len


def extract_masks(magick, src_png, b_pgm, g_pgm):
    subprocess.run([
        magick, src_png, "-alpha", "extract",
        "-blur", "0x2.5", "-threshold", "82%",
        "-morphology", "Dilate", "Disk:1.5",
        f"PGM:{b_pgm}",
    ], check=True)
    subprocess.run([
        magick, src_png, "-alpha", "extract",
        "-blur", "0x2.5", "-threshold", "22%",
        "(", b_pgm, "-morphology", "Dilate", "Disk:6", ")",
        "-compose", "MinusSrc", "-composite",
        "-morphology", "Open", "Disk:2.5",
        f"PGM:{g_pgm}",
    ], check=True)


def run_pipeline(input_path, output_path=None, stroke_width=31, closed_frame=12, analyze_only=False):
    magick = find_magick()
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
        if analyze_only:
            for idx in range(0, len(frame_files), 2):
                bp = os.path.join(masks_dir, f"b_{idx:04d}.pgm")
                gp = os.path.join(masks_dir, f"g_{idx:04d}.pgm")
                extract_masks(magick, frame_files[idx], bp, gp)
                bc, bb = bbox_and_count(*read_pgm(bp))
                gc, gb = bbox_and_count(*read_pgm(gp))
                print(f"f={idx:03d} | black={bc:5d} {bb} | gray={gc:5d} {gb}")
            return

        # Extract masks for rest frame (0) and fully closed gray frame (closed_frame)
        b0 = os.path.join(masks_dir, "b0.pgm")
        g0 = os.path.join(masks_dir, "g0.pgm")
        bc_pgm = os.path.join(masks_dir, "bc.pgm")
        gc_pgm = os.path.join(masks_dir, "gc.pgm")
        extract_masks(magick, frame_files[0], b0, g0)
        extract_masks(magick, frame_files[min(closed_frame, len(frame_files) - 1)], bc_pgm, gc_pgm)

        w, h, raw_g0 = read_pgm(g0)
        _, _, raw_gc = read_pgm(gc_pgm)
        _, gb0 = bbox_and_count(w, h, raw_g0)
        _, gbc = bbox_and_count(w, h, raw_gc)

        # Measure scale ratio between closed_frame and rest frame
        h0 = (gb0[3] - gb0[1]) if gb0 else h
        hc = (gbc[3] - gbc[1]) if gbc else h
        scale_c_to_0 = h0 / float(hc)
        peak_scale = hc / float(h0)
        cx, cy = w / 2.0, h / 2.0

        # Trace closed gray outline skeleton
        head_skel = zhang_suen_thin(w, h, raw_gc)
        head_comp = max(connected_components(head_skel), key=len)
        main_head = diameter_path(head_comp)
        if main_head[0][0] > main_head[-1][0]:
            main_head.reverse()

        main_set = {(x + dx, y + dy) for (x, y) in main_head for dx in range(-2, 3) for dy in range(-2, 3)}
        rem_comps = connected_components(head_comp - main_set)
        chin_tick = diameter_path(max(rem_comps, key=len)) if rem_comps else []
        if chin_tick:
            d0 = min((math.hypot(chin_tick[0][0] - x, chin_tick[0][1] - y), (x, y)) for (x, y) in main_head)
            d1 = min((math.hypot(chin_tick[-1][0] - x, chin_tick[-1][1] - y), (x, y)) for (x, y) in main_head)
            chin_tick = ([d0[1]] + chin_tick) if d0[0] < d1[0] else (chin_tick + [d1[1]])

        def scale_pt(p):
            return (cx + (p[0] - cx) * scale_c_to_0, cy + (p[1] - cy) * scale_c_to_0)

        main_head_rest = [scale_pt(p) for p in main_head]
        chin_tick_rest = [scale_pt(p) for p in chin_tick]

        # Locate rest gap on main_head_rest against frame 0 gray mask
        present = [raw_g0[int(round(y)) * w + int(round(x))] >= 128 for (x, y) in main_head_rest]
        best_gap = (0, 0)
        i = 0
        while i < len(present):
            if not present[i]:
                j = i
                while j < len(present) and not present[j]:
                    j += 1
                if (j - i) > (best_gap[1] - best_gap[0]):
                    best_gap = (i, j)
                i = j
            else:
                i += 1
        gap_start, gap_end = best_gap
        gap_mid = (gap_start + gap_end) // 2

        head_left_pts = main_head_rest[:gap_mid + 1]
        head_right_pts = list(reversed(main_head_rest[gap_mid:]))

        d_head_left, len_hl = path_to_bezier(head_left_pts, eps=1.8, smooth_iters=8)
        d_head_right, len_hr = path_to_bezier(head_right_pts, eps=1.8, smooth_iters=8)
        d_chin, _ = path_to_bezier(chin_tick_rest, eps=1.5, smooth_iters=4)

        left_rest_frac = polyline_len(main_head_rest[:gap_start + 1]) / max(polyline_len(head_left_pts), 1.0)
        right_rest_frac = polyline_len(list(reversed(main_head_rest[gap_end:]))) / max(polyline_len(head_right_pts), 1.0)
        hl_off_rest = max(0.0, len_hl * (1.0 - left_rest_frac) + 14.0)
        hr_off_rest = max(0.0, len_hr * (1.0 - right_rest_frac) + 14.0)
        hl_off_wide = min(len_hl * 0.85, hl_off_rest + 55.0)
        hr_off_wide = min(len_hr * 0.45, hr_off_rest + 55.0)

        # Trace black foreground strokes from frame 0
        _, _, raw_b0 = read_pgm(b0)
        fp_skel = zhang_suen_thin(w, h, raw_b0)
        fp_paths = [diameter_path(c) for c in connected_components(fp_skel)]
        fp_paths.sort(key=lambda p: (sum(y for _, y in p) / len(p), sum(x for x, _ in p) / len(p)), reverse=True)
        fp_data = []
        for p in fp_paths:
            if p[0][1] < p[-1][1]:
                p.reverse()
            d, L = path_to_bezier(p, eps=1.4, smooth_iters=6)
            fp_data.append((d, max(L, 1.0)))

        css_parts = [f"""
.scene {{
  transform-origin: {cx:.0f}px {cy:.0f}px;
  animation: sceneScale 1.5s infinite;
}}
@keyframes sceneScale {{
  0%, 3% {{ transform: scale(1); animation-timing-function: cubic-bezier(0.35, 0, 0.25, 1); }}
  16% {{ transform: scale({peak_scale:.3f}); animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1); }}
  29% {{ transform: scale({1.0 + (peak_scale - 1.0)*0.7:.3f}); animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1); }}
  43% {{ transform: scale(0.95); animation-timing-function: cubic-bezier(0.3, 0, 0.3, 1); }}
  59% {{ transform: scale(1.015); animation-timing-function: ease-out; }}
  72%, 100% {{ transform: scale(1); }}
}}
.head-l {{
  stroke-dasharray: {len_hl:.1f} {len_hl+20:.1f};
  stroke-dashoffset: {hl_off_rest:.1f};
  animation: headLeft 1.5s infinite;
}}
@keyframes headLeft {{
  0%, 3% {{ stroke-dashoffset: {hl_off_rest:.1f}; animation-timing-function: cubic-bezier(0.35, 0, 0.25, 1); }}
  15%, 29% {{ stroke-dashoffset: 0; animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1); }}
  43% {{ stroke-dashoffset: {hl_off_wide:.1f}; animation-timing-function: cubic-bezier(0.3, 0, 0.3, 1); }}
  58%, 100% {{ stroke-dashoffset: {hl_off_rest:.1f}; }}
}}
.head-r {{
  stroke-dasharray: {len_hr:.1f} {len_hr+20:.1f};
  stroke-dashoffset: {hr_off_rest:.1f};
  animation: headRight 1.5s infinite;
}}
@keyframes headRight {{
  0%, 3% {{ stroke-dashoffset: {hr_off_rest:.1f}; animation-timing-function: cubic-bezier(0.35, 0, 0.25, 1); }}
  15%, 29% {{ stroke-dashoffset: 0; animation-timing-function: cubic-bezier(0.4, 0, 0.2, 1); }}
  43% {{ stroke-dashoffset: {hr_off_wide:.1f}; animation-timing-function: cubic-bezier(0.3, 0, 0.3, 1); }}
  58%, 100% {{ stroke-dashoffset: {hr_off_rest:.1f}; }}
}}
"""]

        fp_tags = []
        fp_classes = []
        stroke_attrs = f'fill="none" stroke="#000" stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round"'
        for idx, (d, L) in enumerate(fp_data, start=1):
            r0 = 3.0 + (idx - 1) * 0.5
            r1 = 14.0 + (idx - 1) * 0.5
            d0 = 31.0 + (idx - 1) * 2.0
            d1 = 48.0 + (idx - 1) * 2.0
            cls = f"fp{idx}"
            fp_classes.append(f".{cls}")
            css_parts.append(f"""
.{cls} {{
  stroke-dasharray: {L:.1f} {L+20:.1f};
  stroke-dashoffset: 0;
  animation: {cls} 1.5s infinite;
}}
@keyframes {cls} {{
  0%, {r0:.1f}% {{ stroke-dashoffset: 0; opacity: 1; animation-timing-function: cubic-bezier(0.4, 0, 0.6, 1); }}
  {r1 - 0.5:.1f}% {{ stroke-dashoffset: {L:.1f}; opacity: 1; }}
  {r1:.1f}%, {d0 - 0.5:.1f}% {{ stroke-dashoffset: {L+2:.1f}; opacity: 0; }}
  {d0:.1f}% {{ stroke-dashoffset: {L:.1f}; opacity: 1; animation-timing-function: cubic-bezier(0.2, 0, 0.2, 1); }}
  {d1:.1f}%, 100% {{ stroke-dashoffset: 0; opacity: 1; }}
}}""")
            fp_tags.append(f'    <path {stroke_attrs} class="{cls}" d="{d}"/>')

        reduced_sel = ", ".join([".scene", ".head-l", ".head-r"] + fp_classes)
        css_parts.append(f"\n@media (prefers-reduced-motion: reduce) {{\n  {reduced_sel} {{ animation: none !important; }}\n}}\n")

        chin_tag = f'\n    <path {stroke_attrs} d="{d_chin}"/>' if d_chin else ""
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n'
            f'<style>{"".join(css_parts).strip()}</style>\n'
            '<g class="scene">\n'
            '  <g opacity="0.5">\n'
            f'    <path {stroke_attrs} class="head-l" stroke-dasharray="{len_hl:.1f} {len_hl+20:.1f}" stroke-dashoffset="{hl_off_rest:.1f}" d="{d_head_left}"/>\n'
            f'    <path {stroke_attrs} class="head-r" stroke-dasharray="{len_hr:.1f} {len_hr+20:.1f}" stroke-dashoffset="{hr_off_rest:.1f}" d="{d_head_right}"/>'
            f'{chin_tag}\n'
            '  </g>\n'
            '  <g>\n'
            + "\n".join(fp_tags) + "\n"
            '  </g>\n'
            '</g>\n'
            '</svg>\n'
        )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(svg)
        print(f"Generated {output_path}: {len(svg):,} bytes ({len(fp_data) + (3 if d_chin else 2)} paths)")


def main():
    parser = argparse.ArgumentParser(description="Build ultra-compact stroke-based pure-CSS Animated SVG from a GIF icon.")
    parser.add_argument("input", help="Path to input GIF/APNG icon")
    parser.add_argument("output", nargs="?", help="Path to output .svg file (required unless --analyze-only)")
    parser.add_argument("--stroke-width", type=int, default=31, help="Stroke width in viewBox px (default: 31)")
    parser.add_argument("--closed-frame", type=int, default=12, help="Frame index where outer gray outline is fully closed")
    parser.add_argument("--analyze-only", action="store_true", help="Only print frame-by-frame motion metrics")
    args = parser.parse_args()
    if not args.analyze_only and not args.output:
        parser.error("output path is required unless --analyze-only is set")
    run_pipeline(
        args.input,
        args.output,
        stroke_width=args.stroke_width,
        closed_frame=args.closed_frame,
        analyze_only=args.analyze_only,
    )


if __name__ == "__main__":
    main()
