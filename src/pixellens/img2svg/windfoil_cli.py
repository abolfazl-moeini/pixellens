#!/usr/bin/env python3
"""
Wrapper CLI for Windfoil (WebGPU differentiable SVG optimizer) with automatic
alpha-channel <clipPath> preservation so transparent 3D icons stay transparent
on any background (including Dark Mode).

Requires:
  - Node.js >= 22 + a local clone of https://github.com/texel-org/windfoil-paper
    (auto-detected in img2svg/windfoil-paper, /tmp/windfoil-paper, or $WINDFOIL_DIR)
  - ImageMagick (`magick`) for white-background flattening & alpha silhouette extraction

Usage:
    python3 windfoil_cli.py input.png output.svg [--shapes 512] [--steps 600] [--no-alpha-clip]
"""

import argparse
import glob
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def find_bin(name):
    for cand in (shutil.which(name), f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if cand and os.path.exists(cand):
            return cand
    return None


def find_windfoil_dir(override=None):
    cands = [
        override,
        os.environ.get("WINDFOIL_DIR"),
        os.path.join(SCRIPT_DIR, "windfoil-paper"),
        "/tmp/windfoil-paper",
    ]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "demos", "cli.js")):
            return c
    return None


def read_pgm(path):
    with open(path, "rb") as f:
        assert f.readline().strip() == b"P5"
        line = f.readline()
        while line.startswith(b"#"):
            line = f.readline()
        w, h = map(int, line.split())
        f.readline()
        return w, h, f.read(w * h)


def extract_alpha_clip_d(magick, input_path, view_w, view_h):
    """Extract clean cubic-bezier silhouette path from input alpha channel for <clipPath>."""
    with tempfile.TemporaryDirectory() as tmp:
        pgm = os.path.join(tmp, "alpha.pgm")
        subprocess.run([
            magick, input_path,
            "-resize", f"{view_w}x{view_h}!",
            "-alpha", "extract",
            "-blur", "0x0.6", "-threshold", "40%",
            f"PGM:{pgm}",
        ], check=True)
        w, h, raw = read_pgm(pgm)
        # If image is completely opaque (>99% filled), no clip needed
        filled = sum(1 for b in raw if b >= 128)
        if filled >= w * h * 0.99:
            return None

        # Marching squares contour extraction
        pw, ph = w + 2, h + 2
        grid = bytearray(pw * ph)
        for y in range(h):
            ro, do = y * w, (y + 1) * pw + 1
            for x in range(w):
                if raw[ro + x] >= 128:
                    grid[do + x] = 1

        adj = {}
        def add_edge(p1, p2):
            adj.setdefault(p1, []).append(p2)

        for y in range(ph - 1):
            y0, y1 = y * pw, (y + 1) * pw
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
                area = sum(fpts[i][0] * fpts[(i + 1) % len(fpts)][1] - fpts[(i + 1) % len(fpts)][0] * fpts[i][1] for i in range(len(fpts)))
                if abs(area) * 0.5 >= 25.0:
                    loops.append(fpts)

        if not loops:
            return None

        def rdp_open(pts, eps=1.2):
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

        def fmt(v):
            return f"{v:.1f}".rstrip("0").rstrip(".")

        parts = []
        for lp in loops:
            p0 = lp[0]
            far_i = max(range(1, len(lp)), key=lambda i: (lp[i][0] - p0[0]) ** 2 + (lp[i][1] - p0[1]) ** 2)
            simp = rdp_open(lp[:far_i + 1], 1.2)[:-1] + rdp_open(lp[far_i:] + [lp[0]], 1.2)[:-1]
            if len(simp) < 3:
                continue
            parts.append(f"M{fmt(simp[0][0])},{fmt(simp[0][1])}")
            for x, y in simp[1:]:
                parts.append(f"L{fmt(x)},{fmt(y)}")
            parts.append("Z")
        return "".join(parts) if parts else None


def apply_alpha_clip(svg_text, clip_d):
    # Remove any solid white background rect Windfoil emits and wrap content in <g clip-path="url(#wfClip)">
    m = re.search(r"(<svg\b[^>]*>)", svg_text)
    if not m:
        return svg_text
    head = svg_text[:m.end()]
    body = svg_text[m.end():]
    end_idx = body.rfind("</svg>")
    if end_idx == -1:
        return svg_text
    inner = body[:end_idx]
    tail = body[end_idx:]
    # Strip solid white full-canvas <rect .../> if present
    inner = re.sub(r'<rect\b[^>]*fill="(?:#fff|#ffffff|white|rgb\(255,\s*255,\s*255\))"[^>]*/?>', "", inner, count=1, flags=re.I)
    defs = f'<defs><clipPath id="wfClip"><path fill-rule="evenodd" d="{clip_d}"/></clipPath></defs>\n'
    return f'{head}\n{defs}<g clip-path="url(#wfClip)">\n{inner}\n</g>\n{tail}'


def run_windfoil(input_path, output_path, shapes=512, steps=600, opt_size=512, windfoil_dir=None, alpha_clip=True):
    node = find_bin("node")
    magick = find_bin("magick")
    if not node:
        raise RuntimeError("Node.js (>= 22) not found.")
    wf_dir = find_windfoil_dir(windfoil_dir)
    if not wf_dir:
        raise RuntimeError(
            "windfoil-paper repo not found. Install it once with:\n"
            "  git clone --depth 1 https://github.com/texel-org/windfoil-paper.git /tmp/windfoil-paper && cd /tmp/windfoil-paper && npm install"
        )

    with tempfile.TemporaryDirectory() as tmp:
        flat_png = os.path.join(tmp, "target_white.png")
        out_dir = os.path.join(tmp, "wf_out")
        if magick:
            subprocess.run([magick, input_path, "-background", "white", "-alpha", "remove", "-alpha", "off", flat_png], check=True)
        else:
            shutil.copyfile(input_path, flat_png)

        cmd = [
            node, os.path.join(wf_dir, "demos", "cli.js"), "l2",
            f"--target={flat_png}",
            f"--n={shapes}",
            f"--steps={steps}",
            f"--opt-size={opt_size}",
            "--bg=white",
            f"--out={out_dir}",
        ]
        subprocess.run(cmd, cwd=wf_dir, check=True)

        svgs = sorted(glob.glob(os.path.join(out_dir, "**", "*.svg"), recursive=True))
        if not svgs:
            raise RuntimeError(f"Windfoil finished but no .svg output was found in {out_dir}")
        svg_text = open(svgs[-1], "r", encoding="utf-8").read()

        if alpha_clip and magick:
            vb = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg_text)
            vw = int(round(float(vb.group(1)))) if vb else opt_size
            vh = int(round(float(vb.group(2)))) if vb else opt_size
            clip_d = extract_alpha_clip_d(magick, input_path, vw, vh)
            if clip_d:
                svg_text = apply_alpha_clip(svg_text, clip_d)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(svg_text)
        print(f"[Windfoil:n={shapes},steps={steps}] {input_path} -> {output_path} ({len(svg_text):,} bytes)")


def main():
    parser = argparse.ArgumentParser(description="Run Windfoil WebGPU differentiable SVG optimizer with alpha preservation.")
    parser.add_argument("input", help="Input image path")
    parser.add_argument("output", help="Output .svg path")
    parser.add_argument("--shapes", type=int, default=512, help="Number of quadratic loops (default: 512)")
    parser.add_argument("--steps", type=int, default=600, help="Optimization steps (default: 600)")
    parser.add_argument("--opt-size", type=int, default=512, help="Optimization resolution (default: 512)")
    parser.add_argument("--windfoil-dir", help="Path to local windfoil-paper repo")
    parser.add_argument("--no-alpha-clip", action="store_true", help="Do not apply alpha <clipPath> to remove white background")
    args = parser.parse_args()
    run_windfoil(
        args.input,
        args.output,
        shapes=args.shapes,
        steps=args.steps,
        opt_size=args.opt_size,
        windfoil_dir=args.windfoil_dir,
        alpha_clip=not args.no_alpha_clip,
    )


if __name__ == "__main__":
    main()
