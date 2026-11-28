import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if not sys.path or sys.path[0] != _SRC:
    sys.path.insert(0, _SRC)

from pixellens.audit.assets import (
    audit_inline_svgs,
    audit_raster_images,
    audit_svg_icons,
    parse_image_dimensions,
    run_full_asset_audit,
)


class AssetAuditTests(unittest.TestCase):
    def test_parse_png_jpeg_and_webp_dimensions(self):
        # PNG 100x50
        png_buf = bytearray(30)
        png_buf[:4] = b"\x89PNG"
        struct.pack_into(">II", png_buf, 16, 100, 50)
        self.assertEqual(
            parse_image_dimensions(bytes(png_buf)),
            {"width": 100, "height": 50, "format": "png"},
        )

        # JPEG with padding FF FF, standalone RST0 (FF D0), APP0, SOF0 640x480
        jpeg_buf = bytes([
            0xFF, 0xD8,
            0xFF, 0xFF, 0xD0,
            0xFF, 0xE0, 0x00, 0x04, 0x00, 0x00,
            0xFF, 0xC0, 0x00, 0x0B, 0x08,
            0x01, 0xE0,  # 480
            0x02, 0x80,  # 640
            0x01, 0x00, 0x00,
        ])
        self.assertEqual(
            parse_image_dimensions(jpeg_buf),
            {"width": 640, "height": 480, "format": "jpeg"},
        )

        # Malformed JPEG length < 2 terminates cleanly
        bad_jpeg = bytes([0xFF, 0xD8, 0xFF, 0xE1, 0x00, 0x00] + [0x00] * 12)
        self.assertEqual(
            parse_image_dimensions(bad_jpeg),
            {"width": 0, "height": 0, "format": "jpeg"},
        )

        # 25-byte lossless WebP VP8L (128x64)
        vp8l = bytearray(25)
        vp8l[0:4] = b"RIFF"
        vp8l[8:12] = b"WEBP"
        vp8l[12:16] = b"VP8L"
        vp8l[20] = 0x2F
        w_m1, h_m1 = 127, 63
        vp8l[21] = w_m1 & 0xFF
        vp8l[22] = ((w_m1 >> 8) & 0x3F) | ((h_m1 & 0x03) << 6)
        vp8l[23] = (h_m1 >> 2) & 0xFF
        vp8l[24] = (h_m1 >> 10) & 0x0F
        self.assertEqual(
            parse_image_dimensions(bytes(vp8l)),
            {"width": 128, "height": 64, "format": "webp"},
        )

    def test_audit_svg_icons_unique_compliant_count_and_id_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "clean.svg").write_text(
                '<svg viewBox="0 0 24 24" fill="currentColor"><path id="f00" clip-path="url(#abc)" d="M0 0h24v24H0z"/><text>&#123;</text></svg>',
                encoding="utf-8",
            )
            (d / "double-bad.svg").write_text(
                '<svg width="24" height="24" fill="#123456"><rect/></svg>',
                encoding="utf-8",
            )
            (d / "rgb-upper.svg").write_text(
                '<svg viewBox="0 0 24 24" fill="RGB(10, 20, 30)"><rect/></svg>',
                encoding="utf-8",
            )
            (d / "logo.svg").write_text(
                '<svg viewBox="0 0 24 24" fill="#ff0000"><rect/></svg>',
                encoding="utf-8",
            )

            res = audit_svg_icons([d], allow_multicolor=["logo.svg"])
            self.assertEqual(res["total"], 4)
            self.assertEqual(res["compliant"], 2)
            self.assertEqual(len(res["violations"]), 3)

    def test_audit_inline_svgs_and_raster_duplicates_recursively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "nested"
            sub.mkdir()

            html = root / "page.html"
            html.write_text(
                '<div>'
                '<svg viewBox="0 0 24 24" fill="currentColor"><path/></svg>'
                '<svg viewBox="0 0 24 24" fill="#ff0000"><circle/></svg>'
                '<svg width="16" height="16"><rect/></svg>'
                '</div>',
                encoding="utf-8",
            )
            inline_res = audit_inline_svgs([html])
            self.assertEqual(inline_res["total"], 3)
            self.assertEqual(inline_res["compliant"], 1)
            self.assertEqual(len(inline_res["violations"]), 2)

            small = bytearray(100)
            small[:4] = b"\x89PNG"
            struct.pack_into(">II", small, 16, 10, 10)
            large = bytearray(2048)
            large[:4] = b"\x89PNG"
            struct.pack_into(">II", large, 16, 100, 100)

            (root / "a.png").write_bytes(small)
            (sub / "b.png").write_bytes(small)
            (sub / "big.png").write_bytes(large)

            raster_res = audit_raster_images([root], max_kb=1.0, recursive=True)
            self.assertEqual(raster_res["total"], 3)
            self.assertEqual(len(raster_res["duplicates"]), 1)
            self.assertEqual(len(raster_res["oversized"]), 1)

            full = run_full_asset_audit(image_dirs=[root], max_raster_kb=1.0, fail_on_duplicates=True)
            self.assertFalse(full["ok"])

    def test_cli_audit_subcommand(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "ok.svg").write_text('<svg viewBox="0 0 24 24" fill="currentColor"><path/></svg>', encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, os.path.join(repo_root, "pixellens.py"), "audit", "--icons", str(d), "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(proc.stdout)
            self.assertTrue(data["ok"])
            self.assertEqual(data["svg"]["compliant"], 1)

            # Empty targets must fail with exit code 2 instead of silently passing
            empty_proc = subprocess.run(
                [sys.executable, os.path.join(repo_root, "pixellens.py"), "audit"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(empty_proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
