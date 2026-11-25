# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow==12.1.1"]
# ///
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests of webmail_shot.py that need no browser (#3).

    uv run test_webmail_shot.py
"""

import datetime
import io
import json
import unittest

from PIL import Image

import webmail_shot as w

RED = (0xF7, 0x17, 0x01)


def png_of(width, height, color=(255, 255, 255)):
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "PNG")
    return out.getvalue()


class SigninUrl(unittest.TestCase):
    def test_webmail_is_not_signin(self):
        self.assertFalse(w.is_signin_url("https://webmail.thundermail.com/mail/inbox"))

    def test_other_host_is_signin(self):
        self.assertTrue(w.is_signin_url("https://auth.tb.pro/realms/tbpro/protocol/openid-connect/auth?x=1"))

    def test_lookalike_host_is_signin(self):
        self.assertTrue(w.is_signin_url("https://webmail.thundermail.com.example.com/"))

    def test_empty_is_signin(self):
        self.assertTrue(w.is_signin_url("about:blank"))


class TourName(unittest.TestCase):
    def test_date_and_locale(self):
        self.assertEqual(w.tour_name(datetime.date(2026, 11, 25), "en-US"), "2026-11-25-en-US")

    def test_unsafe_characters(self):
        self.assertEqual(w.tour_name(datetime.date(2026, 11, 25), "../x"), "2026-11-25----x")

    def test_no_locale(self):
        self.assertEqual(w.tour_name(datetime.date(2026, 11, 25), ""), "2026-11-25-unknown")


class Scale(unittest.TestCase):
    def test_viewport_fits_620(self):
        s = w.fit_scale(w.VIEWPORT["width"], w.VIEWPORT["height"])
        self.assertEqual(w.scaled_size(w.VIEWPORT["width"], w.VIEWPORT["height"], s), (620, 434))

    def test_never_enlarges(self):
        self.assertEqual(w.fit_scale(300, 200), 1.0)

    def test_tall_page(self):
        self.assertEqual(w.scaled_size(800, 1240, w.fit_scale(800, 1240)), (400, 620))


class OutlineRect(unittest.TestCase):
    def test_centered_on_edge(self):
        # A 56 x 56 icon at (20, 800) in a 1240-wide page, scale 0.5: 28 x 28 at (10, 400).
        rect = w.outline_rect({"x": 20, "y": 800, "width": 56, "height": 56}, 0.5, (620, 434))
        self.assertEqual(rect, (7, 397, 41, 431))

    def test_kept_inside_image(self):
        rect = w.outline_rect({"x": 0, "y": 840, "width": 56, "height": 28}, 0.5, (620, 434))
        self.assertEqual(rect, (0, 417, 31, 434))

    def test_item_outside_image(self):
        with self.assertRaises(ValueError):
            w.outline_rect({"x": 2000, "y": 0, "width": 10, "height": 10}, 0.5, (620, 434))


class ShrinkAndOutline(unittest.TestCase):
    def test_size_and_outline_pixels(self):
        box = {"x": 20, "y": 800, "width": 56, "height": 56, "page": (1240, 868)}
        png, rect, size = w.shrink_and_outline(png_of(1240, 868), box)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        self.assertEqual(img.size, (620, 434))
        self.assertEqual(size, (620, 434))
        left, top, right, bottom = rect
        # Exactly OUTLINE_PX red pixels across the left edge, then white.
        row = top + (bottom - top) // 2
        reds = [img.getpixel((x, row)) == RED for x in range(left, left + w.OUTLINE_PX + 1)]
        self.assertEqual(reds, [True] * w.OUTLINE_PX + [False])
        self.assertEqual(img.getpixel((right - 1, bottom - 1)), RED)
        self.assertEqual(img.getpixel((left - 1, top - 1)), (255, 255, 255))

    def test_2x_screenshot_gives_same_size(self):
        box = {"x": 20, "y": 800, "width": 56, "height": 56, "page": (1240, 868)}
        _, rect1, size1 = w.shrink_and_outline(png_of(1240, 868), box)
        _, rect2, size2 = w.shrink_and_outline(png_of(2480, 1736), box)
        self.assertEqual((rect1, size1), (rect2, size2))

    def test_no_outline(self):
        png, rect, size = w.shrink_and_outline(png_of(1240, 868))
        self.assertIsNone(rect)
        self.assertEqual(size, (620, 434))
        img = Image.open(io.BytesIO(png)).convert("RGB")
        self.assertNotIn(RED, [c for _, c in img.getcolors(1 << 20)])


class Index(unittest.TestCase):
    steps = [
        dict(w.STEPS[0], size=[620, 434], outline=[591, 2, 615, 26], label="Open account menu"),
        dict(w.STEPS[1], size=[620, 434], outline=[493, 61, 611, 86], label="Settings"),
        dict(w.STEPS[2], size=[620, 434], outline=None, label=None),
    ]

    def test_md(self):
        md = w.index_md("2026-11-25-en-US", self.steps)
        self.assertIn("| 1 | inbox | 01-inbox-account-menu-next.png | Open account menu | 620 × 434 | 591, 2, 615, 26 |", md)
        self.assertIn("| 3 | settings | 03-settings.png |  | 620 × 434 |  |", md)

    def test_json(self):
        data = json.loads(w.index_json("2026-11-25-en-US", "en-US", datetime.date(2026, 11, 25), self.steps))
        self.assertEqual(data["tour"], "2026-11-25-en-US")
        self.assertEqual([s["file"] for s in data["steps"]],
                         ["01-inbox-account-menu-next.png", "02-account-menu-settings-next.png", "03-settings.png"])

    def test_each_outline_is_clicked_and_last_step_has_none(self):
        self.assertTrue(all(s["outline"] for s in w.STEPS[:-1]))
        self.assertIsNone(w.STEPS[-1]["outline"])

    def test_selectors_use_no_translated_text(self):
        for s in w.STEPS:
            for sel in (s["ready"], s["outline"] or ""):
                self.assertNotIn("[aria-label=", sel)
                self.assertNotIn("text=", sel)
                self.assertNotIn(":has-text", sel)

    def test_file_names_have_no_visible_text(self):
        for s in w.STEPS:
            self.assertRegex(s["file"], r"^\d\d-[a-z-]+\.png$")


if __name__ == "__main__":
    unittest.main()
