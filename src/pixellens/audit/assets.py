"""Zero-dependency asset hygiene auditor for SVG icons, inline SVGs, and raster images."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
from pathlib import Path
from typing import Any, Iterable

_VIEWBOX_RE = re.compile(r"\bviewBox\s*=", re.IGNORECASE)
_ID_ATTR_RE = re.compile(r"""\bid\s*=\s*["'][^"']+["']""", re.IGNORECASE)
_URL_REF_RE = re.compile(r"""url\s*\(\s*["']?#[^"')]+["']?\s*\)""", re.IGNORECASE)
_HEX_COLOR_RE = re.compile(r"(?<!&)#([0-9a-fA-F]{3,8})\b")
_RGB_COLOR_RE = re.compile(r"rgba?\([^)]+\)", re.IGNORECASE)
_HSL_COLOR_RE = re.compile(r"hsla?\([^)]+\)", re.IGNORECASE)
_SVG_BLOCK_RE = re.compile(r"<svg\b([^>]*)>([\s\S]*?)</svg>", re.IGNORECASE)

_RASTER_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def parse_image_dimensions(data: bytes) -> dict[str, Any] | None:
    """Parse width, height, and format from PNG, JPEG, or WebP binary headers."""
    if not data or len(data) < 16:
        return None

    # PNG: 89 50 4E 47 ... IHDR width @ 16, height @ 20
    if data[:4] == b"\x89PNG":
        if len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return {"width": width, "height": height, "format": "png"}

    # JPEG: FF D8 FF
    if data[:3] == b"\xff\xd8\xff":
        offset = 2
        n = len(data)
        while offset + 1 < n:
            if data[offset] != 0xFF:
                offset += 1
                continue
            while offset + 1 < n and data[offset + 1] == 0xFF:
                offset += 1
            if offset + 1 >= n:
                break
            marker = data[offset + 1]
            # Stuffed byte or standalone markers without length
            if marker in (0x00, 0x01) or (0xD0 <= marker <= 0xD9):
                offset += 2
                if marker == 0xD9:  # EOI
                    break
                continue
            # SOF0, SOF1, SOF2
            if marker in (0xC0, 0xC1, 0xC2):
                if offset + 9 < n:
                    height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
                    return {"width": width, "height": height, "format": "jpeg"}
                break
            if offset + 3 >= n:
                break
            seg_len = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
            if seg_len < 2:
                break
            offset += 2 + seg_len
        return {"width": 0, "height": 0, "format": "jpeg"}

    # WebP: RIFF ... WEBP
    if len(data) >= 25 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8 " and len(data) >= 30:
            w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
            h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
            return {"width": w, "height": h, "format": "webp"}
        if chunk == b"VP8L" and len(data) >= 25:
            b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
            w = 1 + (((b1 & 0x3F) << 8) | b0)
            h = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return {"width": w, "height": h, "format": "webp"}
        if chunk == b"VP8X" and len(data) >= 30:
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return {"width": w, "height": h, "format": "webp"}
        return {"width": 0, "height": 0, "format": "webp"}

    return {"width": 0, "height": 0, "format": "unknown"}


def _discover_files(directory: str | Path, extensions: set[str], recursive: bool = True) -> list[str]:
    root = Path(directory)
    if not root.is_dir():
        return []
    found: list[str] = []
    if recursive:
        for dirpath, _, filenames in os.walk(root):
            for name in sorted(filenames):
                if Path(name).suffix.lower() in extensions:
                    found.append(str(Path(dirpath) / name))
    else:
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            if entry.is_file() and entry.suffix.lower() in extensions:
                found.append(str(entry))
    return found


def _strip_svg_id_refs(content: str) -> str:
    return _URL_REF_RE.sub("", _ID_ATTR_RE.sub("", content))


def audit_svg_icons(
    icon_dirs: Iterable[str | Path],
    *,
    allow_multicolor: Iterable[str] | None = None,
    require_current_color: bool = False,
    recursive: bool = True,
) -> dict[str, Any]:
    """Audit standalone .svg icon files for viewBox and currentColor compliance."""
    allow_set = set(allow_multicolor or [])
    violations: list[dict[str, Any]] = []
    scanned_files: list[str] = []

    for d in icon_dirs:
        scanned_files.extend(_discover_files(d, {".svg"}, recursive=recursive))

    for file_path in scanned_files:
        file_name = Path(file_path).name
        content = Path(file_path).read_text(encoding="utf-8", errors="replace")

        if not _VIEWBOX_RE.search(content):
            violations.append({
                "file": file_path,
                "reason": "Missing viewBox attribute in SVG root",
            })

        is_allowlisted = file_name in allow_set or file_path in allow_set
        if not is_allowlisted:
            sanitized = _strip_svg_id_refs(content)
            hex_matches = [f"#{m}" for m in _HEX_COLOR_RE.findall(sanitized)]
            rgb_matches = _RGB_COLOR_RE.findall(sanitized)
            hsl_matches = _HSL_COLOR_RE.findall(sanitized)

            if hex_matches or rgb_matches or hsl_matches:
                violations.append({
                    "file": file_path,
                    "reason": "Hardcoded color detected; SVGs must use currentColor for theme-aware reactivity",
                    "details": {
                        "hexColors": sorted(set(hex_matches)),
                        "rgbColors": sorted(set(rgb_matches)),
                        "hslColors": sorted(set(hsl_matches)),
                    },
                })

            if require_current_color and "currentColor" not in content:
                violations.append({
                    "file": file_path,
                    "reason": "SVG does not explicitly declare currentColor for theme reactivity",
                })

    violating_files = {v["file"] for v in violations}
    compliant = max(0, len(scanned_files) - len(violating_files))

    return {
        "total": len(scanned_files),
        "compliant": compliant,
        "violations": violations,
        "files": scanned_files,
    }


def audit_inline_svgs(
    html_files: Iterable[str | Path],
    *,
    check_hardcoded_colors: bool = True,
) -> dict[str, Any]:
    """Audit inline <svg> blocks in HTML files for viewBox and hardcoded colors."""
    violations: list[dict[str, Any]] = []
    violating_keys: set[str] = set()
    total_inline = 0

    for file_item in html_files:
        file_path = str(file_item)
        path_obj = Path(file_path)
        if not path_obj.is_file():
            continue
        content = path_obj.read_text(encoding="utf-8", errors="replace")

        for idx, match in enumerate(_SVG_BLOCK_RE.finditer(content), start=1):
            total_inline += 1
            full_tag = match.group(0)
            attributes = match.group(1)

            if not _VIEWBOX_RE.search(attributes):
                violating_keys.add(f"{file_path}#{idx}")
                violations.append({
                    "file": file_path,
                    "index": idx,
                    "reason": "Inline SVG missing viewBox",
                })

            if check_hardcoded_colors:
                sanitized = _strip_svg_id_refs(full_tag)
                hex_matches = [f"#{m}" for m in _HEX_COLOR_RE.findall(sanitized)]
                rgb_matches = _RGB_COLOR_RE.findall(sanitized)
                hsl_matches = _HSL_COLOR_RE.findall(sanitized)

                if hex_matches or rgb_matches or hsl_matches:
                    violating_keys.add(f"{file_path}#{idx}")
                    violations.append({
                        "file": file_path,
                        "index": idx,
                        "reason": "Inline SVG contains hardcoded color; use currentColor for theme reactivity",
                        "details": {
                            "hexColors": sorted(set(hex_matches)),
                            "rgbColors": sorted(set(rgb_matches)),
                            "hslColors": sorted(set(hsl_matches)),
                        },
                    })

    return {
        "total": total_inline,
        "compliant": max(0, total_inline - len(violating_keys)),
        "violations": violations,
    }


def audit_raster_images(
    image_dirs: Iterable[str | Path],
    *,
    max_kb: float = 100.0,
    recursive: bool = True,
) -> dict[str, Any]:
    """Audit raster images for file size limits, header dimensions, and SHA-256 duplicates."""
    oversized: list[dict[str, Any]] = []
    hash_map: dict[str, dict[str, Any]] = {}
    dimensions: dict[str, dict[str, Any] | None] = {}
    total_bytes = 0
    scanned_files: list[str] = []

    for d in image_dirs:
        scanned_files.extend(_discover_files(d, _RASTER_EXTS, recursive=recursive))

    for file_path in scanned_files:
        data = Path(file_path).read_bytes()
        size_bytes = len(data)
        total_bytes += size_bytes
        size_kb = size_bytes / 1024.0

        if size_kb > max_kb:
            oversized.append({
                "file": file_path,
                "sizeKb": round(size_kb, 1),
                "maxKb": max_kb,
            })

        digest = hashlib.sha256(data).hexdigest()
        if digest not in hash_map:
            hash_map[digest] = {"files": [file_path], "sizeBytes": size_bytes}
        else:
            hash_map[digest]["files"].append(file_path)

        dimensions[file_path] = parse_image_dimensions(data)

    duplicates = [
        {"hash": digest, "files": info["files"], "sizeBytes": info["sizeBytes"]}
        for digest, info in hash_map.items()
        if len(info["files"]) > 1
    ]

    return {
        "total": len(scanned_files),
        "totalBytes": total_bytes,
        "oversized": oversized,
        "duplicates": duplicates,
        "dimensions": dimensions,
    }


def run_full_asset_audit(
    *,
    icon_dirs: Iterable[str | Path] = (),
    image_dirs: Iterable[str | Path] = (),
    html_files: Iterable[str | Path] = (),
    allow_multicolor: Iterable[str] = (),
    require_current_color: bool = False,
    max_raster_kb: float = 100.0,
    recursive: bool = True,
    fail_on_duplicates: bool = False,
) -> dict[str, Any]:
    """Run unified SVG, inline SVG, and raster hygiene audit."""
    svg = audit_svg_icons(
        icon_dirs,
        allow_multicolor=allow_multicolor,
        require_current_color=require_current_color,
        recursive=recursive,
    )
    inline_svg = audit_inline_svgs(html_files)
    raster = audit_raster_images(image_dirs, max_kb=max_raster_kb, recursive=recursive)

    ok = (
        len(svg["violations"]) == 0
        and len(inline_svg["violations"]) == 0
        and len(raster["oversized"]) == 0
        and (not fail_on_duplicates or len(raster["duplicates"]) == 0)
    )

    summary = (
        f"Scanned {svg['total']} SVG icons ({len(svg['violations'])} violations across "
        f"{svg['total'] - svg['compliant']} files), {inline_svg['total']} inline SVGs "
        f"({len(inline_svg['violations'])} violations), {raster['total']} raster images "
        f"({len(raster['oversized'])} oversized >{max_raster_kb:g}KB, {len(raster['duplicates'])} duplicate sets)."
    )

    return {
        "ok": ok,
        "svg": svg,
        "inlineSvg": inline_svg,
        "raster": raster,
        "summary": summary,
    }


def audit_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pixellens audit",
        description="Audit SVG icons (viewBox, currentColor), inline SVGs, and raster image weights/duplicates.",
    )
    parser.add_argument("--icons", nargs="*", default=[], help="Directories of standalone SVG icons")
    parser.add_argument("--images", nargs="*", default=[], help="Directories of raster images (PNG/JPG/WebP)")
    parser.add_argument("--html", nargs="*", default=[], help="HTML files to scan for inline <svg> blocks")
    parser.add_argument("--allow-multicolor", nargs="*", default=[], help="SVG basenames allowed to use hardcoded colors")
    parser.add_argument("--max-kb", type=float, default=100.0, help="Max raster image size in KB (default: 100)")
    parser.add_argument("--require-current-color", action="store_true", help="Require explicit currentColor in non-allowlisted SVGs")
    parser.add_argument("--fail-on-duplicates", action="store_true", help="Exit 1 if exact binary duplicates are found")
    parser.add_argument("--no-recursive", action="store_true", help="Disable recursive directory scanning")
    parser.add_argument("--json", action="store_true", help="Output full audit report as JSON")
    args = parser.parse_args(argv)

    if not args.icons and not args.images and not args.html:
        parser.error("at least one target must be specified (--icons, --images, or --html)")

    result = run_full_asset_audit(
        icon_dirs=args.icons,
        image_dirs=args.images,
        html_files=args.html,
        allow_multicolor=args.allow_multicolor,
        require_current_color=args.require_current_color,
        max_raster_kb=args.max_kb,
        recursive=not args.no_recursive,
        fail_on_duplicates=args.fail_on_duplicates,
    )

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        status = "PASS" if result["ok"] else "FAIL"
        print(f"\n=== pixellens asset audit: {status} ===")
        print(f"Summary: {result['summary']}")
        for v in result["svg"]["violations"][:10]:
            print(f"  [SVG] {v['file']}: {v['reason']}")
        for v in result["inlineSvg"]["violations"][:10]:
            print(f"  [Inline SVG #{v['index']}] {v['file']}: {v['reason']}")
        for o in result["raster"]["oversized"][:10]:
            print(f"  [Oversized] {o['file']}: {o['sizeKb']}KB > {o['maxKb']}KB")
        for d in result["raster"]["duplicates"][:10]:
            print(f"  [Duplicate] {' == '.join(d['files'])}")
        print("")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(audit_main())
