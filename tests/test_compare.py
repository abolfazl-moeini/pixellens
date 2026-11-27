import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from compare.pixels import (
    _crop,
    _downscale_for_perceptual,
    compare_images,
    compare_main,
)
import iconkat


def rgba(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    return bytes(color * (width * height))


class CompareTests(unittest.TestCase):
    def images(self, mapping):
        def load(path, magick=None):
            return mapping[path]
        return patch("compare.pixels.load_rgba", side_effect=load)

    def test_identical_images_pass_and_report_zero_diff(self):
        image = (2, 2, rgba(2, 2, (12, 80, 190, 255)))
        with self.images({"before": image, "after": image}):
            result = compare_images("before", "after")
        self.assertTrue(result["identical"])
        self.assertEqual(result["differing_pixels"], 0)
        self.assertEqual(result["difference_fraction"], 0.0)
        self.assertEqual(result["max_channel_delta"], 0)
        self.assertIsNone(result["changed_bounding_box"])
        self.assertEqual(result["layout_similarity"], 1.0)
        self.assertEqual(result["perceptual_similarity"], 1.0)
        self.assertEqual(result["ignored_tiles"], 0)
        self.assertTrue(result["passed"])

    def test_tolerance_and_changed_bounds(self):
        before = (2, 1, rgba(2, 1, (0, 0, 0, 255)))
        after = (2, 1, bytes((3, 0, 0, 255, 40, 0, 0, 255)))
        with self.images({"before": before, "after": after}):
            result = compare_images("before", "after", tolerance=8)
        self.assertFalse(result["identical"])
        self.assertEqual(result["differing_pixels"], 1)
        self.assertEqual(result["difference_fraction"], 0.5)
        self.assertEqual(result["max_channel_delta"], 40)
        self.assertEqual(result["changed_bounding_box"], {"x": 1, "y": 0, "width": 1, "height": 1})

    def test_dimension_mismatch_requires_common_area(self):
        before = (2, 1, rgba(2, 1, (0, 0, 0, 255)))
        after = (1, 1, rgba(1, 1, (0, 0, 0, 255)))
        with self.images({"before": before, "after": after}):
            result = compare_images("before", "after")
        self.assertFalse(result["comparable"])
        self.assertIsNone(result["shared_area"])
        self.assertIsNone(result["difference_fraction"])

        with self.images({"before": before, "after": after}):
            result = compare_images("before", "after", common_area=True)
        self.assertTrue(result["comparable"])
        self.assertEqual(result["shared_area"], {"width": 1, "height": 1})

    def test_invalid_parameters_rejected(self):
        with self.assertRaises(ValueError):
            compare_images("before", "after", tolerance=256)
        with self.assertRaises(ValueError):
            compare_images("before", "after", tolerance=-1)
        with self.assertRaises(ValueError):
            compare_images("before", "after", min_similarity=1.1)
        with self.assertRaises(ValueError):
            compare_images("before", "after", min_similarity=-0.1)

    def test_layout_similarity_calculation(self):
        # 32x32 image = 4 tiles of 16x16: (0,0), (16,0), (0,16), (16,16)
        # We make tile (0,0) have a large color diff (>24) while other 3 tiles are identical
        before_pixels = bytearray(32 * 32 * 4)
        after_pixels = bytearray(32 * 32 * 4)
        for y in range(32):
            for x in range(32):
                idx = (y * 32 + x) * 4
                if x < 16 and y < 16:
                    before_pixels[idx:idx + 4] = bytes((0, 0, 0, 255))
                    after_pixels[idx:idx + 4] = bytes((100, 100, 100, 255))
                else:
                    color = bytes((50, 50, 50, 255))
                    before_pixels[idx:idx + 4] = color
                    after_pixels[idx:idx + 4] = color

        before = (32, 32, bytes(before_pixels))
        after = (32, 32, bytes(after_pixels))
        with self.images({"before": before, "after": after}):
            result = compare_images("before", "after", min_similarity=0.70)
        # 3 out of 4 tiles match => layout similarity = 0.75
        self.assertEqual(result["layout_similarity"], 0.75)
        self.assertEqual(result["differing_pixels"], 256)
        self.assertTrue(result["passed"])

    def test_noise_mask_ignores_masked_regions(self):
        # Difference at (0, 0), but noise mask has white pixel (255, 255, 255, 255) at (0, 0)
        before = (2, 2, rgba(2, 2, (0, 0, 0, 255)))
        after_data = bytearray(rgba(2, 2, (0, 0, 0, 255)))
        after_data[0:4] = bytes((255, 0, 0, 255))  # diff at (0, 0)
        after = (2, 2, bytes(after_data))

        # Mask is white (255, 255, 255, 255) at (0, 0) and black elsewhere
        mask_data = bytearray(rgba(2, 2, (0, 0, 0, 255)))
        mask_data[0:4] = bytes((255, 255, 255, 255))
        mask = (2, 2, bytes(mask_data))

        with self.images({"before": before, "after": after, "mask": mask}):
            result = compare_images("before", "after", ignore="mask")
        self.assertTrue(result["identical"])
        self.assertEqual(result["differing_pixels"], 0)
        self.assertEqual(result["total_pixels"], 3)  # 4 total minus 1 masked pixel
        self.assertEqual(result["layout_similarity"], 1.0)
        self.assertEqual(result["perceptual_similarity"], 1.0)
        self.assertTrue(result["passed"])

    def test_noise_mask_crop_with_common_area(self):
        # before: 2x2, after: 2x3. Common area: 2x2.
        # mask is 2x3 (size of after). With common_area=True, it should crop mask to 2x2 without error.
        before = (2, 2, rgba(2, 2, (0, 0, 0, 255)))
        after = (2, 3, rgba(2, 3, (0, 0, 0, 255)))
        mask = (2, 3, rgba(2, 3, (0, 0, 0, 255)))

        with self.images({"before": before, "after": after, "mask": mask}):
            result = compare_images("before", "after", common_area=True, ignore="mask")
        self.assertTrue(result["comparable"])
        self.assertEqual(result["shared_area"], {"width": 2, "height": 2})

    def test_noise_mask_size_mismatch_raises(self):
        before = (4, 4, rgba(4, 4, (0, 0, 0, 255)))
        after = (4, 4, rgba(4, 4, (0, 0, 0, 255)))
        mask = (2, 2, rgba(2, 2, (0, 0, 0, 255)))
        with self.images({"before": before, "after": after, "mask": mask}):
            with self.assertRaises(ValueError):
                compare_images("before", "after", ignore="mask")

    def test_perceptual_downscale_for_large_images(self):
        # 600x600 image downscaled so max dimension is 512
        w, h = 600, 600
        b = rgba(w, h, (100, 100, 100, 255))
        a = rgba(w, h, (100, 100, 100, 255))
        b_down, a_down, m_down, pw, ph = _downscale_for_perceptual(b, a, None, w, h)
        self.assertEqual(pw, 512)
        self.assertEqual(ph, 512)
        self.assertEqual(len(b_down), 512 * 512 * 4)

    def test_diff_out_writes_png(self):
        before = (2, 2, rgba(2, 2, (0, 0, 0, 255)))
        after = (2, 2, bytes((255, 0, 0, 255) * 4))
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as diff_file:
            diff_path = diff_file.name
        try:
            with self.images({"before": before, "after": after}):
                compare_images("before", "after", diff_out=diff_path)
            self.assertTrue(os.path.isfile(diff_path))
            self.assertGreater(os.path.getsize(diff_path), 0)
        finally:
            if os.path.exists(diff_path):
                os.remove(diff_path)

    def test_compare_main_json_and_exit_codes(self):
        image = (2, 2, rgba(2, 2, (10, 20, 30, 255)))
        with self.images({"img1": image, "img2": image}):
            # Test pass
            with patch("sys.stdout") as mock_stdout:
                code = compare_main(["img1", "img2", "--json"])
            self.assertEqual(code, 0)

        diff_image = (2, 2, rgba(2, 2, (200, 200, 200, 255)))
        with self.images({"img1": image, "img2": diff_image}):
            # Test fail
            with patch("sys.stdout") as mock_stdout:
                code = compare_main(["img1", "img2", "--min-similarity", "0.99"])
            self.assertEqual(code, 1)

    def test_iconkat_inspect_only_regression(self):
        # Verify iconkat inspect_file on a real SVG file from the repo
        svg_path = os.path.join(os.path.dirname(__file__), "..", "output.svg")
        info = iconkat.inspect_file(svg_path)
        self.assertEqual(info["kind"], "static-2d-icon")

    def test_cli_subcommand_and_pixellens_integration(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        img_path = os.path.join(repo_root, "output.svg")

        # Test pixellens.py compare identity
        res_comp = subprocess.run(
            [sys.executable, os.path.join(repo_root, "pixellens.py"), "compare", img_path, img_path, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res_comp.stdout)
        self.assertTrue(data["identical"])
        self.assertEqual(data["layout_similarity"], 1.0)
        self.assertEqual(data["perceptual_similarity"], 1.0)
        self.assertTrue(data["passed"])

        # Test iconkat.py compare backward compatibility
        res_iconkat = subprocess.run(
            [sys.executable, os.path.join(repo_root, "iconkat.py"), "compare", img_path, img_path, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data2 = json.loads(res_iconkat.stdout)
        self.assertTrue(data2["identical"])

        # Test iconkat.py inspect-only backward compatibility
        res_inspect = subprocess.run(
            [sys.executable, os.path.join(repo_root, "iconkat.py"), img_path, "--inspect-only"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("2D Flat/Mono Icon", res_inspect.stdout)


if __name__ == "__main__":
    unittest.main()
