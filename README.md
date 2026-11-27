# PixelLens

> **TL;DR:** Local-first CLI for converting **Lottie JSON**, **animated GIF/APNG/WebP**, and **2D/3D raster images** into optimal web formats (**SVG**, **AVIF**, **WebP Lossless**), plus a zero-pip-dependency **pixel, layout, and perceptual (SSIM) image diff engine** (`compare` & `--verify`). *(Backward-compatible successor to `iconkat`.)*

```bash
# 1. Auto-detect & convert any icon, animation, or 3D image
python3 pixellens.py input.png

# 2. Convert & self-verify similarity against the original
python3 pixellens.py icon3d.png --svg --verify

# 3. Compare two images (JSON output for CI / automation)
python3 pixellens.py compare before.png after.png --common-area --tolerance 8 --json
```

---

## Features

- **Smart Format Detection (`pixellens.py` / `iconkat.py`)**: Inspects binary headers and pixel tone distribution (ignoring file extensions) and routes to the best converter:
  - **Lottie JSON** → Self-contained Animated SVG (`json2svg/lottie_to_svg.py`)
  - **Animated GIF / APNG / WebP** → Pure CSS keyframe Animated SVG (`gif2svg/gif_to_svg_frames.py` or `--gif-mode stroke`)
  - **2D Flat / Mono Icons** → Crisp SVG via **VTracer** (`img2svg/vtracer_cli.js`)
  - **3D / Multi-tone Images** → **AVIF** (`q=88`) + **WebP Lossless** by default, or SVG via **Windfoil** (WebGPU/Metal) / **VTracer** with `--svg`
- **Visual & Perceptual Diff (`compare`)**:
  - **Pixel Diff**: Channel-level RGBA diff with configurable `--tolerance`, bounding box, and optional `--diff-out` PNG.
  - **Layout Similarity**: `16×16` tile mean-RGB matching (`0.0`–`1.0`).
  - **Perceptual Similarity**: Proportional downscale (max `512px`) + `8×8` luminance SSIM (`0.0`–`1.0`).
  - **Noise Mask & Cropping**: `--ignore mask.png` skips white mask tiles; `--common-area` crops mismatched viewport heights from top-left.
- **Zero Python Dependencies**: Pure Python stdlib + ImageMagick (`magick`).

---

## Prerequisites

- **Python 3.10+**
- **ImageMagick** (`brew install imagemagick`)
- **Node.js >= 22** (for VTracer: `cd img2svg && npm install`)
- **Windfoil (optional, macOS GPU 3D→SVG)**:
  ```bash
  git clone --depth 1 https://github.com/texel-org/windfoil-paper.git /tmp/windfoil-paper
  cd /tmp/windfoil-paper && npm install
  ```

---

## CLI Usage

### Convert & Verify

| Flag | Values | Default | Description |
| :--- | :--- | :--- | :--- |
| `--svg` | — | off | Also generate SVG for 3D/multi-tone images |
| `--svg-only` | — | off | Generate only SVG (skip AVIF/WebP) |
| `--svg-engine` | `auto\|windfoil\|vtracer\|python` | `auto` | SVG vectorization engine |
| `--wf-shapes` / `--wf-steps` | int | `512` / `600` | Windfoil shape count and optimization steps |
| `--size` | int (px) | original | Resize AVIF/WebP output (e.g. `128`, `256`) |
| `--gif-mode` | `frames\|stroke` | `frames` | Animated GIF SVG mode |
| `--step` | int | `1` | Frame sampling step in `frames` mode |
| `--grid` | — | off | Export `512×512` 64px reference grid PNG |
| `--inspect-only` | — | off | Print detected file type without converting |
| `--verify` | — | off | Rasterize generated SVG and score similarity |

### Compare Two Images

```bash
python3 pixellens.py compare before.png after.png \
  --common-area \
  --tolerance 8 \
  --ignore noise-mask.png \
  --diff-out diff.png \
  --min-similarity 0.90 \
  --json
```

Exit codes: `0` = passed (`layout_similarity >= min` and `perceptual_similarity >= min`), `1` = failed/not comparable, `2` = error.

---

## Structure

```text
pixellens/
├── pixellens.py              # Main PixelLens CLI entry point
├── iconkat.py                # Backward-compatible CLI core
├── compare/                  # Pixel, layout, and SSIM comparison engine
├── json2svg/                 # Lottie JSON -> Animated SVG
├── gif2svg/                  # GIF/APNG/WebP -> Pure CSS Animated SVG
├── img2svg/                  # VTracer, Windfoil, and Python SVG engines
└── tests/                    # Unit & regression tests (pytest)
```
