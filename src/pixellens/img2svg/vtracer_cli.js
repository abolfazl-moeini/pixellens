#!/usr/bin/env node
/**
 * Fast raster-to-SVG CLI powered by VisionCortex VTracer (@neplex/vectorizer).
 * Automatically handles monochrome transparent icons (flattens onto white + upscales)
 * and multi-color graphics/3D renders with stacked spline curves.
 *
 * Usage:
 *   node vtracer_cli.js input.png output.svg [--mode auto|binary|color] [--precision 6] [--speckle 4]
 */

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const {
  vectorizeSync,
  ColorMode,
  Hierarchical,
  PathSimplifyMode,
} = require("@neplex/vectorizer");

function findMagick() {
  for (const cand of ["/opt/homebrew/bin/magick", "/usr/local/bin/magick", "magick"]) {
    try {
      execFileSync(cand, ["-version"], { stdio: "ignore" });
      return cand;
    } catch (_) {}
  }
  return null;
}

function parseArgs(argv) {
  const args = { mode: "auto", precision: 6, layerDiff: 16, speckle: 4, input: null, output: null };
  const pos = [];
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--mode") args.mode = argv[++i];
    else if (a === "--precision") args.precision = Number(argv[++i]);
    else if (a === "--layer-diff") args.layerDiff = Number(argv[++i]);
    else if (a === "--speckle") args.speckle = Number(argv[++i]);
    else if (a === "-h" || a === "--help") {
      console.log(
        "Usage: node vtracer_cli.js <input> <output.svg> [--mode auto|binary|color] [--precision 6] [--layer-diff 16] [--speckle 4]"
      );
      process.exit(0);
    } else {
      pos.push(a);
    }
  }
  args.input = pos[0];
  args.output = pos[1];
  if (!args.input || !args.output) {
    console.error("Error: <input> and <output.svg> are required. Run with --help for usage.");
    process.exit(1);
  }
  return args;
}

function pgmBody(buf) {
  let idx = 0;
  for (let i = 0; i < 3; i++) {
    idx = buf.indexOf(10, idx) + 1;
  }
  return buf.subarray(idx);
}

function detectMode(magick, inputPath) {
  if (!magick) return "color";
  try {
    const out = execFileSync(
      magick,
      [
        inputPath,
        "-resize", "64x64",
        "-background", "white", "-alpha", "remove", "-alpha", "off",
        "-depth", "8", "-posterize", "8",
        "-format", "%k", "info:",
      ],
      { encoding: "utf8" }
    ).trim();
    const uniq = parseInt(out, 10);

    const gray = pgmBody(execFileSync(magick, [inputPath, "-resize", "64x64", "-colorspace", "Gray", "-depth", "8", "PGM:-"]));
    const alpha = pgmBody(execFileSync(magick, [inputPath, "-resize", "64x64", "-alpha", "extract", "-depth", "8", "PGM:-"]));
    let fgCount = 0;
    let midCount = 0;
    const len = Math.min(gray.length, alpha.length);
    for (let i = 0; i < len; i++) {
      if (alpha[i] >= 200) {
        fgCount++;
        if (gray[i] >= 30 && gray[i] <= 225) midCount++;
      }
    }
    const midRatio = midCount / Math.max(fgCount, 1);
    return Number.isFinite(uniq) && uniq <= 10 && midRatio < 0.25 ? "binary" : "color";
  } catch (_) {
    return "color";
  }
}

function main() {
  const args = parseArgs(process.argv);
  const magick = findMagick();
  const mode = args.mode === "auto" ? detectMode(magick, args.input) : args.mode;

  let srcPath = args.input;
  let tmpFile = null;

  // In binary mode, flatten transparent monochrome icons onto white and upscale to 512px
  // so transparent black pixels don't merge with the foreground icon.
  if (mode === "binary" && magick) {
    tmpFile = path.join(os.tmpdir(), `vtracer_bw_${Date.now()}.png`);
    execFileSync(magick, [
      args.input,
      "-background", "white",
      "-alpha", "remove",
      "-alpha", "off",
      "-resize", "512x512>",
      "-resize", "512x512<",
      "-blur", "0x1",
      "-threshold", "50%",
      tmpFile,
    ]);
    srcPath = tmpFile;
  }

  try {
    const t0 = Date.now();
    const buf = fs.readFileSync(srcPath);
    const svg = vectorizeSync(buf, {
      colorMode: mode === "binary" ? ColorMode.Binary : ColorMode.Color,
      hierarchical: Hierarchical.Stacked,
      mode: PathSimplifyMode.Spline,
      filterSpeckle: args.speckle,
      colorPrecision: args.precision,
      layerDifference: args.layerDiff,
      cornerThreshold: 60,
      lengthThreshold: 4,
      maxIterations: 10,
      spliceThreshold: 45,
      pathPrecision: 2,
    });
    fs.writeFileSync(args.output, svg, "utf8");
    const ms = Date.now() - t0;
    console.log(`[VTracer:${mode}] ${args.input} -> ${args.output} (${svg.length.toLocaleString()} bytes, ${ms}ms)`);
  } finally {
    if (tmpFile && fs.existsSync(tmpFile)) fs.unlinkSync(tmpFile);
  }
}

main();
