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
import os
import subprocess
import tempfile
import unittest
import zipfile

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


PAGE = (1240, 868)
DIALOG = {"x": 390, "y": 283, "width": 460, "height": 302}
AVATAR = {"x": 1188, "y": 10, "width": 36, "height": 36}
WELCOME = {"x": 100, "y": 26, "width": 1040, "height": 816}


class CropRect(unittest.TestCase):
    def test_dialog_with_margin(self):
        item = {"x": 732, "y": 367, "width": 96, "height": 25}
        self.assertEqual(w.crop_rect(DIALOG, item, PAGE), (366, 259, 874, 609))

    def test_small_item_gets_min_size_inside_page(self):
        # The avatar is at the top right, so the crop moves left and down to stay inside.
        self.assertEqual(w.crop_rect(AVATAR, AVATAR, PAGE), (760, 0, 1240, 320))

    def test_large_focus_keeps_its_top_and_center(self):
        self.assertEqual(w.crop_rect(WELCOME, None, PAGE), (310, 2, 930, 622))

    def test_large_focus_is_centered_on_the_item(self):
        item = {"x": 1060, "y": 600, "width": 40, "height": 40}
        left, top, right, bottom = w.crop_rect(WELCOME, item, PAGE)
        self.assertEqual((right - left, bottom - top), (620, 620))
        self.assertTrue(left <= item["x"] and item["x"] + item["width"] <= right)
        self.assertTrue(top <= item["y"] and item["y"] + item["height"] <= bottom)

    def test_never_larger_than_max(self):
        for focus in (DIALOG, AVATAR, WELCOME):
            l, t, r, b = w.crop_rect(focus, None, PAGE)
            self.assertLessEqual(r - l, w.MAX_PX)
            self.assertLessEqual(b - t, w.MAX_PX)
            self.assertTrue(0 <= l and r <= PAGE[0] and 0 <= t and b <= PAGE[1])


class CropAndOutline(unittest.TestCase):
    def test_full_size_and_outline(self):
        item = dict(x=732, y=367, width=96, height=25)
        crop = w.crop_rect(DIALOG, item, PAGE)
        png, rect, size = w.crop_and_outline(png_of(*PAGE), PAGE, crop, item)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        self.assertEqual(img.size, (508, 350))
        self.assertEqual(size, (508, 350))
        # The item is at full size: 96 x 25 inside the outline, centered on its edge.
        self.assertEqual(rect, (732 - 366 - 3, 367 - 259 - 3, 828 - 366 + 3, 392 - 259 + 3))
        row = (rect[1] + rect[3]) // 2
        reds = [img.getpixel((x, row)) == RED for x in range(rect[0], rect[0] + w.OUTLINE_PX + 1)]
        self.assertEqual(reds, [True] * w.OUTLINE_PX + [False])

    def test_2x_screenshot_gives_css_size(self):
        crop = w.crop_rect(DIALOG, None, PAGE)
        _, _, size = w.crop_and_outline(png_of(2480, 1736), PAGE, crop)
        self.assertEqual(size, (crop[2] - crop[0], crop[3] - crop[1]))

    def test_crop_file_name(self):
        self.assertEqual(w.crop_file("01-inbox-account-menu-next.png"), "01-inbox-account-menu-next-crop.png")


class BuildOf(unittest.TestCase):
    def test_main_script(self):
        urls = ["https://webmail.thundermail.com/assets/main-BbnY9DAP.js", ""]
        self.assertEqual(w.build_of(urls), "assets/main-BbnY9DAP.js")

    def test_other_scripts_are_ignored(self):
        urls = ["https://webmail.thundermail.com/assets/modulepreload-polyfill-Dezn_h7o.js",
                "https://example.com/assets/main-XXXX.js",
                "https://webmail.thundermail.com/assets/main-BbnY9DAP.js"]
        self.assertEqual(w.build_of(urls), "assets/main-BbnY9DAP.js")

    def test_none_or_two(self):
        self.assertIsNone(w.build_of([]))
        self.assertIsNone(w.build_of(["https://webmail.thundermail.com/assets/main-A.js",
                                      "https://webmail.thundermail.com/assets/main-B.js"]))


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
        dict(w.STEPS[0], size=[620, 434], outline_rect=[591, 2, 615, 26], label="Open account menu",
             crop_file="01-inbox-account-menu-next-crop.png", crop_size=[480, 320], crop_outline_rect=[425, 7, 467, 49]),
        dict(w.STEPS[1], size=[620, 434], outline_rect=[493, 61, 611, 86], label="Settings"),
        dict(w.STEPS[3], size=[620, 434], outline_rect=None, label=None,
             crop_file="04-settings-shortcuts-thunderbird-crop.png", crop_size=[508, 350], crop_outline_rect=None),
    ]

    def test_md(self):
        md = w.index_md("2026-11-25-en-US", self.steps)
        self.assertIn("| 1 | inbox | Open account menu | 01-inbox-account-menu-next.png | 620 × 434 | 591, 2, 615, 26 "
                      "| 01-inbox-account-menu-next-crop.png | 480 × 320 | 425, 7, 467, 49 |", md)
        self.assertIn("| 3 | settings, Thunderbird shortcuts |  | 04-settings-shortcuts-thunderbird.png | 620 × 434 |  "
                      "| 04-settings-shortcuts-thunderbird-crop.png | 508 × 350 |  |", md)

    def test_build_and_alpha_note(self):
        md = w.index_md("2026-11-25-en-US", self.steps, "assets/main-BbnY9DAP.js")
        self.assertIn("Webmail build: `assets/main-BbnY9DAP.js`", md)
        self.assertIn("stormbox#236", md)
        self.assertIn("Webmail build: `unknown`", w.index_md("x", self.steps))
        notes = w.release_notes("2026-11-25", "assets/main-BbnY9DAP.js")
        self.assertIn("`assets/main-BbnY9DAP.js`", notes)
        self.assertIn("stormbox#236", notes)

    def test_json(self):
        data = json.loads(w.index_json("2026-11-25-en-US", "en-US", datetime.date(2026, 11, 25), self.steps,
                                       "assets/main-BbnY9DAP.js"))
        self.assertEqual(data["build"], "assets/main-BbnY9DAP.js")
        self.assertTrue(data["staff_account"])
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

    def test_every_step_has_a_focus(self):
        for s in w.STEPS:
            self.assertTrue(s["focus"], s["file"])

    def test_file_names_have_no_visible_text(self):
        for s in w.STEPS:
            self.assertRegex(s["file"], r"^\d\d-[a-z-]+\.png$")


class Publish(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.tmp.name, "2026-11-25-en-US")
        os.makedirs(self.folder)
        steps = [dict(s, crop_file=w.crop_file(s["file"])) for s in w.STEPS]
        with open(os.path.join(self.folder, "index.json"), "w") as f:
            f.write(w.index_json("2026-11-25-en-US", "en-US", datetime.date(2026, 11, 25), steps,
                                 "assets/main-BbnY9DAP.js"))
        with open(os.path.join(self.folder, "index.md"), "w") as f:
            f.write(w.index_md("2026-11-25-en-US", steps))
        for s in steps:
            for n in (s["file"], s["crop_file"]):
                with open(os.path.join(self.folder, n), "wb") as f:
                    f.write(png_of(4, 4))
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def gh(self, view_code=0, view_err="", assets=(), upload_code=0):
        def run(cmd, **kw):
            self.calls.append(cmd)
            if cmd[:3] == ["gh", "release", "view"]:
                out = json.dumps({"assets": [{"name": a} for a in assets]})
                return subprocess.CompletedProcess(cmd, view_code, out, view_err)
            # The zip must exist and hold every file while gh runs.
            with zipfile.ZipFile(cmd[4]) as z:
                self.zipped = z.namelist()
            return subprocess.CompletedProcess(cmd, upload_code, "", "upload failed" if upload_code else "")
        return run

    def test_creates_the_release(self):
        cmd = w.publish(self.folder, run=self.gh(1, "release not found"), say=lambda line: None)
        self.assertEqual(cmd[:5], ["gh", "release", "create", "screenshots-2026-11-25", cmd[4]])
        self.assertTrue(cmd[4].endswith("/2026-11-25-en-US.zip"))
        self.assertIn("--notes", cmd)
        self.assertIn("assets/main-BbnY9DAP.js", cmd[cmd.index("--notes") + 1])
        self.assertEqual(len(self.zipped), 2 + 2 * len(w.STEPS))
        self.assertIn("2026-11-25-en-US/01-inbox-account-menu-next-crop.png", self.zipped)
        self.assertEqual(self.zipped[:2], ["2026-11-25-en-US/index.md", "2026-11-25-en-US/index.json"])

    def test_adds_to_the_release(self):
        cmd = w.publish(self.folder, run=self.gh(assets=["2026-11-25-de.zip"]), say=lambda line: None)
        self.assertEqual(cmd[:3], ["gh", "release", "upload"])
        self.assertNotIn("--clobber", cmd)

    def test_refuses_to_replace(self):
        with self.assertRaisesRegex(RuntimeError, "--replace"):
            w.publish(self.folder, run=self.gh(assets=["2026-11-25-en-US.zip"]), say=lambda line: None)
        self.assertEqual(len(self.calls), 1)

    def test_replaces_with_flag(self):
        cmd = w.publish(self.folder, replace=True, run=self.gh(assets=["2026-11-25-en-US.zip"]), say=lambda line: None)
        self.assertIn("--clobber", cmd)

    def test_gh_error(self):
        with self.assertRaisesRegex(RuntimeError, "not logged in"):
            w.publish(self.folder, run=self.gh(1, "not logged in"), say=lambda line: None)

    def test_upload_error(self):
        with self.assertRaisesRegex(RuntimeError, "upload failed"):
            w.publish(self.folder, run=self.gh(assets=[], upload_code=1), say=lambda line: None)

    def test_missing_file(self):
        os.remove(os.path.join(self.folder, "03-settings-shortcuts-thunderbird-next-crop.png"))
        with self.assertRaisesRegex(RuntimeError, "lacks 03-settings-shortcuts-thunderbird-next-crop.png"):
            w.publish(self.folder, run=self.gh(), say=lambda line: None)
        self.assertEqual(self.calls, [])

    def test_not_a_tour(self):
        os.remove(os.path.join(self.folder, "index.json"))
        with self.assertRaisesRegex(RuntimeError, "not a finished tour"):
            w.publish(self.folder, run=self.gh(), say=lambda line: None)


if __name__ == "__main__":
    unittest.main()
