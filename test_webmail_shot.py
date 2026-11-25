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


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class WaitUntilStable(unittest.TestCase):
    def test_stable_value_waits_settle(self):
        c = FakeClock()
        self.assertEqual(w.wait_until_stable(lambda: "a", settle=5, clock=c, sleep=c.sleep), 5.0)

    def test_change_restarts_the_wait(self):
        c = FakeClock()
        # The cached list for 2 s, then the refreshed list.
        read = lambda: "cached" if c.t < 2 else "fresh"
        self.assertEqual(w.wait_until_stable(read, settle=5, clock=c, sleep=c.sleep), 7.0)

    def test_never_stable(self):
        c = FakeClock()
        with self.assertRaises(RuntimeError):
            w.wait_until_stable(lambda: c.t, settle=5, timeout=30, clock=c, sleep=c.sleep)


class SettingsDifferences(unittest.TestCase):
    def test_same(self):
        self.assertEqual(w.settings_differences(w.START_SETTINGS, dict(w.START_SETTINGS)), [])

    def test_changed_and_missing(self):
        found = {"shortcuts": "thunderbird"}
        self.assertEqual(w.settings_differences(w.START_SETTINGS, found), [
            "shortcuts: expected 'web', found 'thunderbird'",
            "follow_system_theme: expected 'true', found None",
        ])


class Index(unittest.TestCase):
    steps = [
        dict(w.STEPS[0], size=[620, 434], outline=[591, 2, 615, 26], label="Open account menu"),
        dict(w.STEPS[1], size=[620, 434], outline=[493, 61, 611, 86], label="Settings"),
        dict(w.STEPS[3], size=[620, 434], outline=None, label=None),
    ]

    def test_md(self):
        md = w.index_md("2026-11-25-en-US", self.steps)
        self.assertIn("| 1 | inbox | 01-inbox-account-menu-next.png | Open account menu | 620 × 434 | 591, 2, 615, 26 |", md)
        self.assertIn("| 3 | settings, Thunderbird shortcuts | 04-settings-shortcuts-thunderbird.png |  | 620 × 434 |  |", md)

    def test_json(self):
        data = json.loads(w.index_json("2026-11-25-en-US", "en-US", datetime.date(2026, 11, 25), self.steps))
        self.assertEqual(data["tour"], "2026-11-25-en-US")
        self.assertEqual([s["file"] for s in data["steps"]],
                         ["01-inbox-account-menu-next.png", "02-account-menu-settings-next.png",
                          "04-settings-shortcuts-thunderbird.png"])

    def test_each_step_has_an_outline_or_an_undo(self):
        for s in w.STEPS[:-1]:
            self.assertTrue(bool(s["outline"]) != bool(s.get("undo")), s["file"])
            if s.get("undo"):
                self.assertTrue(s["undo_ready"], s["file"])
        self.assertIsNone(w.STEPS[-1]["outline"])
        self.assertNotIn("undo", w.STEPS[-1])

    def test_files_are_numbered_in_order(self):
        self.assertEqual([int(s["file"][:2]) for s in w.STEPS], list(range(1, len(w.STEPS) + 1)))

    def test_every_settings_control_is_clicked(self):
        clicked = {s["outline"] for s in w.STEPS}
        for sel in (w.SCHEME_THUNDERBIRD, w.THEME_SWITCH, w.SHOW_WELCOME, w.CLOSE_SETTINGS):
            self.assertIn(sel, clicked)

    def test_settings_are_read_while_the_dialog_is_open(self):
        self.assertEqual(w.STEPS[w.FIRST_SETTINGS_STEP]["ready"], w.SETTINGS_DIALOG)
        self.assertEqual(w.STEPS[w.LAST_SETTINGS_STEP]["ready"], w.SETTINGS_DIALOG)
        # Every click before the last read is undone.
        for s in w.STEPS[w.FIRST_SETTINGS_STEP:w.LAST_SETTINGS_STEP]:
            if not s["outline"]:
                self.assertTrue(s.get("undo"), s["file"])

    def test_selectors_use_no_translated_text(self):
        for s in w.STEPS:
            for sel in (s["ready"], s["outline"] or "", s.get("gone") or "", *s.get("undo", [])):
                self.assertNotIn("[aria-label=", sel)
                self.assertNotIn("text=", sel)
                self.assertNotIn(":has-text", sel)

    def test_file_names_have_no_visible_text(self):
        for s in w.STEPS:
            self.assertRegex(s["file"], r"^\d\d-[a-z-]+\.png$")


if __name__ == "__main__":
    unittest.main()
