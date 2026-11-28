#!/usr/bin/env python3
"""
iconkat — Unified CLI to inspect any icon/image/animation file and convert it to optimal web formats or SVG.

Automatically detects (from binary headers & pixel analysis, ignoring file extension):
  1. lottie-json        -> Animated SVG via json2svg/lottie_to_svg.py
  2. animated-raster    -> Animated SVG via gif2svg/gif_to_svg_frames.py (or --gif-mode stroke)
  3. static-2d-icon     -> SVG via 2D method (VTracer binary / fallback Python mono)
  4. static-3d-or-photo -> Default: full-res AVIF (q=88) + WebP Lossless (optional --size for 2x retina)
                           With --svg: ALSO builds SVG using either:
                             - windfoil (WebGPU differentiable optimizer + alpha clipPath — best for 3D)
                             - vtracer  (fast color tracing — CPU-only)
                             - python   (zero-dependency fallback)

Usage:
    python3 iconkat.py <input> [output] [--svg] [--svg-only] [--svg-engine auto|windfoil|vtracer|python]
"""

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))


def find_bin(name):
    for cand in (shutil.which(name), f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if cand and os.path.exists(cand):
            return cand
    return None


def find_windfoil_dir(override=None):
    for cand in (
        override,
        os.environ.get("WINDFOIL_DIR"),
        os.path.join(ROOT, "img2svg", "windfoil-paper"),
        "/tmp/windfoil-paper",
    ):
        if cand and os.path.isfile(os.path.join(cand, "demos", "cli.js")):
            return cand
    return None


def is_lottie_json(path):
    try:
        with open(path, "rb") as f:
            head = f.read(64).lstrip()
        if not head.startswith(b"{"):
            return False
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return isinstance(data, dict) and "layers" in data and "fr" in data and "w" in data and "h" in data
    except Exception:
        return False


def detect_container(path):
    with open(path, "rb") as f:
        sig = f.read(16)
    if sig.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if sig.startswith(b"\x89PNG\r\n\x1a\n"):
        with open(path, "rb") as f:
            f.read(8)
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                length, ctype = struct.unpack(">I4s", hdr)
                if ctype == b"acTL":
                    return "apng"
                if ctype == b"IDAT":
                    break
                f.seek(length + 4, os.SEEK_CUR)
        return "png"
    if sig.startswith(b"RIFF") and sig[8:12] == b"WEBP":
        with open(path, "rb") as f:
            blob = f.read(256)
        return "awebp" if b"ANIM" in blob else "webp"
    if sig.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return "unknown"


def count_frames(magick, path, container):
    if container in ("png", "jpg", "webp"):
        return 1
    if not magick:
        return 2 if container in ("apng", "awebp") else 1
    try:
        out = subprocess.check_output(
            [magick, "identify", "-format", "%n\n", path],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip().splitlines()
        return int(out[0]) if out else 1
    except Exception:
        return 2 if container in ("apng", "awebp") else 1


def analyze_static_style(magick, path):
    """Distinguish 2D flat/mono icon vs 3D/shaded multi-tone image using posterized colors & midtone ratio."""
    if not magick:
        return "static-3d-or-photo", -1
    try:
        uniq_colors = int(
            subprocess.check_output(
                [
                    magick, path,
                    "-resize", "64x64",
                    "-background", "white", "-alpha", "remove", "-alpha", "off",
                    "-depth", "8", "-posterize", "8",
                    "-format", "%k", "info:",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
        gray = subprocess.check_output(
            [magick, path, "-resize", "64x64", "-colorspace", "Gray", "-depth", "8", "PGM:-"],
            stderr=subprocess.DEVNULL,
        )
        alpha = subprocess.check_output(
            [magick, path, "-resize", "64x64", "-alpha", "extract", "-depth", "8", "PGM:-"],
            stderr=subprocess.DEVNULL,
        )

        def pgm_body(buf):
            idx = 0
            for _ in range(3):
                idx = buf.index(10, idx) + 1
            return buf[idx:]

        gb, ab = pgm_body(gray), pgm_body(alpha)
        fg = [gb[i] for i in range(min(len(gb), len(ab))) if ab[i] >= 200]
        mid_ratio = sum(1 for v in fg if 30 <= v <= 225) / max(len(fg), 1)
    except Exception:
        return "static-3d-or-photo", -1

    if uniq_colors <= 10 and mid_ratio < 0.25:
        return "static-2d-icon", uniq_colors
    return "static-3d-or-photo", uniq_colors


def inspect_file(path):
    if is_lottie_json(path):
        return {"kind": "lottie-json", "container": "json", "frames": None, "colors": None}

    magick = find_bin("magick")
    container = detect_container(path)
    frames = count_frames(magick, path, container)
    if frames > 1 or container in ("apng", "awebp"):
        return {"kind": "animated-raster", "container": container, "frames": frames, "colors": None}

    kind, colors = analyze_static_style(magick, path)
    return {"kind": kind, "container": container, "frames": 1, "colors": colors}


def default_output_base(input_path, output_arg=None):
    if output_arg:
        base, _ = os.path.splitext(output_arg)
        return base
    base, _ = os.path.splitext(input_path)
    return base


def export_3d_raster_formats(input_path, out_base, resize_px=None):
    magick = find_bin("magick")
    if not magick:
        print("Warning: ImageMagick not found; skipping AVIF and WebP Lossless export.", file=sys.stderr)
        return
    suffix = f"-{resize_px}" if resize_px else ""
    avif_path = f"{out_base}{suffix}.avif"
    webp_path = f"{out_base}{suffix}-lossless.webp"
    resize_args = ["-resize", f"{resize_px}x{resize_px}"] if resize_px else []

    subprocess.run([magick, input_path, *resize_args, "-quality", "88", avif_path], check=True)
    subprocess.run(
        [magick, input_path, *resize_args, "-define", "webp:lossless=true", "-define", "webp:method=6", webp_path],
        check=True,
    )
    res_label = f"{resize_px}px" if resize_px else "full-res"
    print(f"AVIF:     {avif_path} ({os.path.getsize(avif_path):,} bytes, {res_label} q=88)")
    print(f"WebP:     {webp_path} ({os.path.getsize(webp_path):,} bytes, {res_label} lossless)")


def generate_reference_grid(input_path, grid_out_path):
    magick = find_bin("magick")
    if not magick:
        print("Warning: ImageMagick not found; skipping --grid generation.", file=sys.stderr)
        return
    draws = []
    for v in range(64, 512, 64):
        col = "rgba(0,100,255,0.55)" if v == 256 else "rgba(255,0,0,0.35)"
        draws += ["-stroke", col, "-draw", f"line {v},0 {v},512", "-draw", f"line 0,{v} 512,{v}"]
    subprocess.run(
        [magick, input_path, "-resize", "512x512", "-background", "#e5e5e5", "-flatten", *draws, grid_out_path],
        check=True,
    )
    print(f"Grid:     {grid_out_path} (512x512, 64px reference grid for Vector 2.5D redraw)")


def run_svg_conversion(input_path, output_svg, info, args):
    kind = info["kind"]
    node = find_bin("node")
    vtracer_cli = os.path.join(ROOT, "img2svg", "vtracer_cli.js")
    vtracer_ready = (
        node is not None
        and os.path.exists(vtracer_cli)
        and os.path.exists(os.path.join(ROOT, "img2svg", "node_modules", "@neplex", "vectorizer"))
    )
    windfoil_cli = os.path.join(ROOT, "img2svg", "windfoil_cli.py")
    wf_dir = find_windfoil_dir(args.windfoil_dir)

    if kind == "lottie-json":
        cmd = [sys.executable, os.path.join(ROOT, "json2svg", "lottie_to_svg.py"), input_path, output_svg]
    elif kind == "animated-raster":
        if args.gif_mode == "stroke":
            cmd = [sys.executable, os.path.join(ROOT, "gif2svg", "gif_to_stroke_svg.py"), input_path, output_svg]
        else:
            cmd = [sys.executable, os.path.join(ROOT, "gif2svg", "gif_to_svg_frames.py"), input_path, output_svg, "--step", str(args.step)]
    elif kind == "static-2d-icon":
        # Method 1 (2D icons): VTracer binary (or Windfoil/Python if explicitly requested)
        eng = args.svg_engine
        if eng == "windfoil":
            cmd = [sys.executable, windfoil_cli, input_path, output_svg, "--shapes", str(args.wf_shapes), "--steps", str(args.wf_steps)]
            if wf_dir:
                cmd += ["--windfoil-dir", wf_dir]
        elif eng != "python" and vtracer_ready:
            cmd = [node, vtracer_cli, input_path, output_svg, "--mode", "binary"]
        else:
            cmd = [sys.executable, os.path.join(ROOT, "img2svg", "img_to_svg.py"), input_path, output_svg, "--mode", "mono"]
    else:
        # Method 2 (3D / multi-tone images): supports BOTH Windfoil (WebGPU) AND VTracer/Python
        eng = args.svg_engine
        if eng == "auto":
            eng = "windfoil" if wf_dir else ("vtracer" if vtracer_ready else "python")
        if eng == "windfoil":
            cmd = [sys.executable, windfoil_cli, input_path, output_svg, "--shapes", str(args.wf_shapes), "--steps", str(args.wf_steps)]
            if wf_dir:
                cmd += ["--windfoil-dir", wf_dir]
        elif eng == "vtracer" and vtracer_ready:
            cmd = [node, vtracer_cli, input_path, output_svg, "--mode", "color"]
        else:
            cmd = [sys.executable, os.path.join(ROOT, "img2svg", "img_to_svg.py"), input_path, output_svg, "--mode", "color"]

    subprocess.run(cmd, check=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "compare":
        from pixellens.compare.pixels import compare_main
        sys.exit(compare_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "audit":
        from pixellens.audit.assets import audit_main
        sys.exit(audit_main(sys.argv[2:]))
    parser = argparse.ArgumentParser(
        description="iconkat / PixelLens unified CLI: auto-detect Lottie, Animated GIF/APNG, 2D icon, or 3D image and build optimal outputs, or run 'compare'."
    )
    parser.add_argument("input", help="Input file path (or 'compare' subcommand)")
    parser.add_argument("output", nargs="?", help="Optional output base or .svg path (default: next to input)")
    parser.add_argument("--inspect-only", action="store_true", help="Only detect and print file type without converting")
    parser.add_argument("--svg", action="store_true", help="For 3D images, ALSO build an SVG output (in addition to AVIF + WebP Lossless)")
    parser.add_argument("--svg-only", action="store_true", help="Build ONLY SVG output (skip AVIF + WebP Lossless even for 3D images)")
    parser.add_argument(
        "--svg-engine",
        choices=["auto", "windfoil", "vtracer", "python"],
        default="auto",
        help="SVG engine: auto (2D->vtracer, 3D->windfoil if installed else vtracer), windfoil (WebGPU), vtracer, or python",
    )
    parser.add_argument("--wf-shapes", type=int, default=512, help="Windfoil shape count (default: 512)")
    parser.add_argument("--wf-steps", type=int, default=600, help="Windfoil optimization steps (default: 600)")
    parser.add_argument("--windfoil-dir", help="Path to local windfoil-paper repo")
    parser.add_argument("--size", type=int, default=None, help="Optional resize (px) for 3D AVIF/WebP outputs (e.g., 128 or 256 for 2x retina icons)")
    parser.add_argument("--grid", action="store_true", help="Also export a 512x512 64px reference grid PNG for Vector 2.5D redraw")
    parser.add_argument("--gif-mode", choices=["frames", "stroke"], default="frames", help="Converter for animated GIFs (default: frames)")
    parser.add_argument("--step", type=int, default=1, help="Frame sampling step for animated GIF frames mode (default: 1)")
    parser.add_argument("--verify", action="store_true", help="Rasterize the generated SVG and report similarity to the input")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    info = inspect_file(args.input)
    labels = {
        "lottie-json": "Animation (Lottie JSON)",
        "animated-raster": f"Animation ({info['container'].upper()}, {info['frames']} frames)",
        "static-2d-icon": f"2D Flat/Mono Icon ({info['container'].upper()}, ~{info['colors']} tones)",
        "static-3d-or-photo": f"3D / Multi-tone Image ({info['container'].upper()}, ~{info['colors']} tones)",
    }

    print(f"Input:    {args.input}")
    print(f"Detected: {labels[info['kind']]}")

    out_base = default_output_base(args.input, args.output)
    out_svg = args.output if (args.output and args.output.endswith(".svg")) else f"{out_base}.svg"

    if args.grid and info["kind"] in ("static-2d-icon", "static-3d-or-photo"):
        generate_reference_grid(args.input, f"{out_base}-grid.png")

    if args.inspect_only:
        return

    if info["kind"] == "static-3d-or-photo":
        want_svg = args.svg or args.svg_only or args.verify or (args.svg_engine != "auto")
        if not args.svg_only:
            export_3d_raster_formats(args.input, out_base, resize_px=args.size)
        if want_svg:
            run_svg_conversion(args.input, out_svg, info, args)
            if args.verify:
                verify_output(args.input, out_svg)
        else:
            print("Hint:     Pass --svg (with --svg-engine windfoil|vtracer) if you also want an SVG output.")
    else:
        # Lottie, Animated GIF/APNG, and 2D flat/mono icons always output SVG directly
        run_svg_conversion(args.input, out_svg, info, args)
        if args.verify:
            if info["kind"] == "lottie-json":
                print("Verification: skipped (Lottie JSON has no raster source)")
            else:
                verify_output(args.input, out_svg)


def verify_output(input_path, output_path):
    from pixellens.compare.pixels import compare_images, find_magick
    magick = find_magick()
    if not magick:
        raise RuntimeError("ImageMagick 'magick' is required for --verify")
    size_parts = subprocess.check_output([magick, "identify", "-format", "%w %h\n", f"{input_path}[0]"], text=True).strip().split()
    if len(size_parts) < 2:
        raise RuntimeError(f"Could not determine dimensions for {input_path!r}")
    width, height = size_parts[0], size_parts[1]
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as rendered:
            tmp_path = rendered.name
        # Render output_path onto transparent canvas matching input dimensions.
        # Note: -background none must precede output_path so vector formats render with alpha.
        target = f"{output_path}[0]" if not output_path.endswith("]") else output_path
        subprocess.run([magick, "-background", "none", target, "-resize", f"{width}x{height}!", tmp_path], check=True)
        result = compare_images(input_path, tmp_path, common_area=True)
        summary = {k: result[k] for k in ("layout_similarity", "perceptual_similarity", "differing_pixels", "total_pixels", "passed")}
        print("Verification: " + json.dumps(summary, indent=2))
        return result
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
