#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright==1.65.2", "pillow==12.1.1"]
# ///
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Screenshot tour of Thundermail webmail, for the KB at support.tb.pro/hc (#3).

    uv run webmail_shot.py login
    uv run webmail_shot.py tour [--out DIR] [--locale LOCALE] [--headed]

`login` opens Firefox Release, driven by Playwright, with a persistent profile in
.auth/firefox-profile. A persistent profile is a Firefox profile folder that
keeps cookies between runs. Sign in to the test account by hand in the OAuth
window, with "Keep me signed in" on. When webmail shows the inbox, close the
Firefox window. The script
never types a password and stores no secret. .auth/ is gitignored, because it
holds session cookies: never commit it.

`tour` opens webmail with the same profile and saves one PNG for each step:

1. The inbox, with the account menu outlined: the round picture with the
   account's initials, at the top right.
2. The open account menu, with Settings outlined.
3. to 9. Each control in the Settings dialog (#4): the dialog with the
   control outlined, then what a click on it shows. The tour undoes each
   click without a screenshot, so the test account keeps its settings.
10. The inbox, after a click on the dialog's close button.

Before the first click in Settings, the tour reads the settings, and it stops
if they are not START_SETTINGS, because the steps expect that start. At the
end, it reads them again and reports each one that differs from the start.

LOCALE is the language that Firefox asks webmail for, en-US unless you give
another. The files go to DIR/DATE-LOCALE, for example screenshots/2026-11-25-en-US,
with index.md and index.json that list the steps. The tour finds each control
by ID or ARIA role, never by visible text, so the same tour works in every
locale. If the session expired, webmail sends the browser to the OAuth sign-in
page. The tour then stops before it takes a screenshot and asks you to run
`login` again.

Each step has two screenshots. The first is the whole page, scaled down to
fit 620 pixels each way, as SUMO's "How to make screenshots" says. Scaling
makes the text half size and blurry, so the second, FILE-crop.png, is a crop
of the page at full size: the step's `focus` (a dialog or a menu) and the
outlined item, with a margin, at most 620 pixels each way. A focus wider or
taller than that, such as the welcome dialog, keeps its top part, centered on
the outlined item if there is one. The KB theme at support.tb.pro shows
images at most 768 pixels wide, so 620 pixels stays below that limit. The
outline is #f71701, square, and OUTLINE_PX thick in the file, centered on the
edge of the item.

The browser is the Firefox Release that is installed on the computer, at
/Applications/Firefox.app on macOS, not Playwright's own Firefox build.
Playwright drives it over WebDriver BiDi, with its channel "moz-firefox", so
there is nothing to install with `playwright install`.
"""

import datetime
import io
import json
import os
import sys
import time
from urllib.parse import urlsplit

# SUMO's rules, from "How to make screenshots" (see CLAUDE.md).
OUTLINE_COLOR = "#f71701"
OUTLINE_PX = 6  # 4.5 pt, inside SUMO's 4 to 8 pt
MAX_PX = 620

WEBMAIL_URL = "https://webmail.thundermail.com/"
WEBMAIL_HOST = "webmail.thundermail.com"
# Firefox Release, as installed on the computer (Playwright's channel name).
FIREFOX_CHANNEL = "moz-firefox"
# The browser window of a tour, in CSS pixels. It scales to 620 by 434.
VIEWPORT = {"width": 1240, "height": 868}

REPO = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(REPO, ".auth", "firefox-profile")
DEFAULT_OUT = os.path.join(REPO, "screenshots")

DEFAULT_LOCALE = "en-US"

# The controls of the tour, never found by visible text or by aria-label,
# which are translated. Found in the signed-in inbox on 2026-11-25 (#3).
# The account menu and its items have no IDs, so these use the menu's class
# and the items' ARIA role and order: Settings is the first menu item.
MESSAGE_LIST = "#msg-listbox-primary"
ACCOUNT_MENU = "details.account-menu > summary"
ACCOUNT_MENU_POPUP = "details.account-menu[open] [role=menu]"
SETTINGS_ITEM = "details.account-menu [role=menu] [role=menuitem] >> nth=0"
SETTINGS_DIALOG = "[role=dialog][aria-labelledby=settings-dialog-title]"

# The controls in the Settings dialog (#4), by their data attributes.
SCHEME_WEB = "[data-shortcut-scheme=web]"
SCHEME_THUNDERBIRD = "[data-shortcut-scheme=thunderbird]"
THEME_SWITCH = "[data-system-theme-toggle]"
SHOW_WELCOME = "[data-show-welcome]"
CLOSE_SETTINGS = ".settings-dialog__close"
WELCOME_DIALOG = "[role=dialog][aria-labelledby=welcome-title]"
CLOSE_WELCOME = ".welcome__close"

# The settings that the steps expect at the start, and how to read them
# while the Settings dialog is open.
START_SETTINGS = {"shortcuts": "web", "follow_system_theme": "true"}
SETTINGS_JS = """() => ({
  shortcuts: document.querySelector('[data-shortcut-scheme][aria-checked=true]')?.dataset.shortcutScheme ?? null,
  follow_system_theme: document.querySelector('[data-system-theme-toggle]')?.getAttribute('aria-checked') ?? null,
})"""

# Webmail shows its cached message list first and refreshes it later, so the
# tour waits until the list stays the same for SETTLE_S seconds (#3).
SETTLE_S = 5
LIST_STATE_JS = """sel => { const l = document.querySelector(sel); if (!l) return null;
  return [l.getAttribute('aria-rowcount'), l.getAttribute('aria-busy'),
          ...[...l.querySelectorAll('[role=row]')].map(r => r.getAttribute('aria-label'))].join('\\n'); }"""

# Each step waits until `ready` shows and `gone` (if any) is hidden, saves a
# screenshot with `outline` outlined, and then clicks `outline`, which leads
# to the next step. A step with `undo` has no outline: after its screenshot,
# the tour clicks each selector in `undo`, in order, and waits for
# `undo_ready`. Then the next step starts.
STEPS = [
    {"file": "01-inbox-account-menu-next.png", "screen": "inbox",
     "ready": MESSAGE_LIST, "outline": ACCOUNT_MENU, "focus": ACCOUNT_MENU},
    {"file": "02-account-menu-settings-next.png", "screen": "account menu",
     "ready": ACCOUNT_MENU_POPUP, "outline": SETTINGS_ITEM, "focus": ACCOUNT_MENU_POPUP},
    {"file": "03-settings-shortcuts-thunderbird-next.png", "screen": "settings",
     "ready": SETTINGS_DIALOG, "outline": SCHEME_THUNDERBIRD, "focus": SETTINGS_DIALOG},
    {"file": "04-settings-shortcuts-thunderbird.png", "screen": "settings, Thunderbird shortcuts",
     "ready": SCHEME_THUNDERBIRD + "[aria-checked=true]", "outline": None, "focus": SETTINGS_DIALOG,
     "undo": [SCHEME_WEB], "undo_ready": SCHEME_WEB + "[aria-checked=true]"},
    {"file": "05-settings-system-theme-next.png", "screen": "settings",
     "ready": SETTINGS_DIALOG, "outline": THEME_SWITCH, "focus": SETTINGS_DIALOG},
    {"file": "06-settings-system-theme-off.png", "screen": "settings, system theme off",
     "ready": THEME_SWITCH + "[aria-checked=false]", "outline": None, "focus": SETTINGS_DIALOG,
     "undo": [THEME_SWITCH], "undo_ready": THEME_SWITCH + "[aria-checked=true]"},
    {"file": "07-settings-show-welcome-next.png", "screen": "settings",
     "ready": SETTINGS_DIALOG, "outline": SHOW_WELCOME, "focus": SETTINGS_DIALOG},
    {"file": "08-welcome.png", "screen": "welcome",
     "ready": WELCOME_DIALOG, "gone": SETTINGS_DIALOG, "outline": None, "focus": WELCOME_DIALOG,
     "undo": [CLOSE_WELCOME, ACCOUNT_MENU, SETTINGS_ITEM], "undo_ready": SETTINGS_DIALOG},
    {"file": "09-settings-close-next.png", "screen": "settings",
     "ready": SETTINGS_DIALOG, "outline": CLOSE_SETTINGS, "focus": SETTINGS_DIALOG},
    {"file": "10-inbox.png", "screen": "inbox, Settings closed",
     "ready": MESSAGE_LIST, "gone": SETTINGS_DIALOG, "outline": None, "focus": ACCOUNT_MENU},
]
# The settings are read before the click of this step, and after the undo of
# the step before the last one, while the Settings dialog is open.
FIRST_SETTINGS_STEP = 2
LAST_SETTINGS_STEP = 8

EXPIRED = ("The session expired: webmail sent the browser to the sign-in page at {url}.\n"
           "Run `uv run webmail_shot.py login` and sign in again. No screenshot was taken.")


# Waits two frames, so that a CSS transition that a click starts is running,
# and then until every animation and transition on the page has finished.
# Returns the number of animations it waited for.
SETTLE_ANIMATIONS_JS = """() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => {
  const running = document.getAnimations().filter(a => a.playState === 'running'
    && a.effect?.getComputedTiming().iterations !== Infinity);
  Promise.all(running.map(a => a.finished.catch(() => null))).then(() => resolve(running.length));
})))"""


# --- Pure functions: no browser ---------------------------------------------------

def is_signin_url(url, webmail_host=WEBMAIL_HOST):
    """True if `url` is not on webmail, which means webmail sent us to sign in."""
    host = urlsplit(url).hostname or ""
    return host != webmail_host


def tour_name(date, locale):
    """The tour's folder name, for example 2026-11-25-en-US."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in locale) or "unknown"
    return f"{date.isoformat()}-{safe}"


def fit_scale(width, height, max_px=MAX_PX):
    """The factor that fits width by height inside max_px each way, never above 1."""
    return min(1.0, max_px / width, max_px / height)


def scaled_size(width, height, scale):
    return max(1, round(width * scale)), max(1, round(height * scale))


def outline_rect(box, scale, image_size, width=OUTLINE_PX):
    """The outline's outer rectangle in the scaled image, as (left, top, right, bottom).

    `box` is the item's bounding box in CSS pixels: x, y, width, height. The
    outline is centered on the item's edge, half inside and half outside, so
    around a small item it covers padding and not the text of its neighbors.
    It is kept inside the image. right and bottom are exclusive.
    """
    iw, ih = image_size
    half = width / 2
    left = box["x"] * scale - half
    top = box["y"] * scale - half
    right = (box["x"] + box["width"]) * scale + half
    bottom = (box["y"] + box["height"]) * scale + half
    left, top = max(0, round(left)), max(0, round(top))
    right, bottom = min(iw, round(right)), min(ih, round(bottom))
    if right - left < 2 * width or bottom - top < 2 * width:
        raise ValueError(f"the item {box} is too small or outside the image {image_size}")
    return left, top, right, bottom


def shrink_and_outline(png, box=None, max_px=MAX_PX, color=OUTLINE_COLOR, width=OUTLINE_PX):
    """Scale a PNG to fit max_px, and outline `box` (CSS pixels of the page).

    The scale comes from the page size in CSS pixels, so a screenshot with
    more pixels, from a 2x screen, gets the same file size. Returns (png
    bytes, the outline rectangle or None, (width, height)).
    """
    from PIL import Image, ImageDraw

    img = Image.open(io.BytesIO(png)).convert("RGB")
    css_w, css_h = img.size if box is None else box.get("page", img.size)
    scale = fit_scale(css_w, css_h, max_px)
    size = scaled_size(css_w, css_h, scale)
    img = img.resize(size, Image.LANCZOS)
    rect = None
    if box is not None:
        rect = outline_rect(box, scale, size, width)
        left, top, right, bottom = rect
        ImageDraw.Draw(img).rectangle([left, top, right - 1, bottom - 1], outline=color, width=width)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue(), rect, size


CROP_MARGIN_PX = 24
CROP_MIN = (480, 320)  # a crop smaller than this gets more of the page around it


def crop_file(file):
    """The file name of a step's crop: 01-inbox.png gives 01-inbox-crop.png."""
    return file[:-len(".png")] + "-crop.png"


def crop_rect(focus, item, page_size, max_px=MAX_PX, min_size=CROP_MIN, margin=CROP_MARGIN_PX):
    """The crop of a step, in CSS pixels, as (left, top, right, bottom).

    It holds `focus` and `item` (boxes with x, y, width, height; item can be
    None) with `margin` around them, at least min_size and at most max_px each
    way, inside the page. If the boxes do not fit, the crop is centered on
    the item, or else keeps the top of the focus and its horizontal center.
    """
    pw, ph = page_size
    boxes = [b for b in (focus, item) if b]
    left = min(b["x"] for b in boxes) - margin
    top = min(b["y"] for b in boxes) - margin
    right = max(b["x"] + b["width"] for b in boxes) + margin
    bottom = max(b["y"] + b["height"] for b in boxes) + margin
    anchor = item or focus
    cx = anchor["x"] + anchor["width"] / 2
    cy = anchor["y"] + anchor["height"] / 2

    def fit(lo, hi, center, keep_start, smallest, largest, limit):
        size = hi - lo
        if size < smallest:
            lo, hi = center - smallest / 2, center + smallest / 2
        elif size > largest:
            lo = lo if keep_start else center - largest / 2
            hi = lo + largest
        size = min(hi - lo, limit)
        lo = min(max(0, lo), limit - size)
        return round(lo), round(lo + size)

    left, right = fit(left, right, cx, False, min_size[0], max_px, pw)
    top, bottom = fit(top, bottom, cy, item is None, min_size[1], max_px, ph)
    return left, top, right, bottom


def crop_and_outline(png, page_size, crop, box=None, color=OUTLINE_COLOR, width=OUTLINE_PX):
    """Cut `crop` (CSS pixels) out of a page screenshot at full size, and outline `box`.

    A screenshot from a 2x screen is scaled to CSS pixels. Returns (png bytes,
    the outline rectangle in the crop or None, (width, height)).
    """
    from PIL import Image, ImageDraw

    img = Image.open(io.BytesIO(png)).convert("RGB")
    device = img.size[0] / page_size[0]
    left, top, right, bottom = crop
    img = img.crop(tuple(round(v * device) for v in crop))
    size = (right - left, bottom - top)
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    rect = None
    if box is not None:
        moved = dict(box, x=box["x"] - left, y=box["y"] - top)
        rect = outline_rect(moved, 1.0, size, width)
        l, t, r, b = rect
        ImageDraw.Draw(img).rectangle([l, t, r - 1, b - 1], outline=color, width=width)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue(), rect, size


def index_md(name, steps):
    """index.md of a tour folder: one row per step."""
    lines = [f"# Thundermail webmail tour {name}", "",
             "Made by `webmail_shot.py tour` (issues #3 and #4). Each step has one screenshot. If",
             "the step has an outlined item, the tour clicks it, and the next step shows the result.",
             "A step without an outline shows what a click changed. The tour undoes that click",
             "without a screenshot before the next step. The crop is the same step at full size,",
             "cut to the dialog or menu that the step is about.", "",
             "Outlines are (left, top, right, bottom) in pixels of the file.", "",
             "| Step | Screen | Item outlined | Whole page | Size (px) | Outline | Crop | Size (px) | Outline |",
             "|---|---|---|---|---|---|---|---|---|"]

    def size_of(v):
        return "{} × {}".format(*v) if v else ""

    def rect_of(v):
        return ", ".join(str(x) for x in v) if v else ""

    for i, s in enumerate(steps, 1):
        cells = [str(i), s["screen"], s.get("label") or "",
                 s["file"], size_of(s.get("size")), rect_of(s.get("outline_rect")),
                 s.get("crop_file") or "", size_of(s.get("crop_size")), rect_of(s.get("crop_outline_rect"))]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def index_json(name, locale, date, steps):
    return json.dumps({"tour": name, "locale": locale, "date": date.isoformat(),
                       "url": WEBMAIL_URL, "steps": steps}, ensure_ascii=False, indent=1) + "\n"


def wait_until_stable(read, settle=SETTLE_S, timeout=30, clock=time.monotonic, sleep=time.sleep):
    """Call read() until its value stays the same for `settle` seconds.

    Returns the seconds it waited. Raises RuntimeError after `timeout`.
    """
    start = clock()
    value, since = read(), start
    while True:
        now = clock()
        if now - since >= settle:
            return now - start
        if now - start > timeout:
            raise RuntimeError(f"the value still changed after {timeout} s")
        sleep(0.5)
        new = read()
        if new != value:
            value, since = new, clock()


def settings_differences(expected, found):
    """The settings in `found` that differ from `expected`, as readable lines."""
    return [f"{k}: expected {expected[k]!r}, found {found.get(k)!r}"
            for k in expected if found.get(k) != expected[k]]


# --- The browser ---------------------------------------------------------------

def open_context(playwright, headless, locale=DEFAULT_LOCALE):
    os.makedirs(PROFILE, exist_ok=True)
    os.chmod(os.path.dirname(PROFILE), 0o700)
    return playwright.firefox.launch_persistent_context(
        PROFILE, channel=FIREFOX_CHANNEL, headless=headless, viewport=VIEWPORT, locale=locale)


def login(say=print):
    from playwright.sync_api import sync_playwright

    say(f"Opening Firefox with the profile {PROFILE}.")
    say("Sign in to the test account by hand in the OAuth window (not your own account),")
    say('with "Keep me signed in" on.')
    say("When webmail shows the inbox, close the Firefox window, and this script ends.")
    with sync_playwright() as p:
        ctx = open_context(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(WEBMAIL_URL)
        # On macOS, Firefox keeps running when its last window closes, so
        # wait for the windows (an OAuth popup is a window too), not for Firefox.
        while ctx.pages:
            ctx.pages[0].wait_for_timeout(500)
        ctx.close()
    say("Done. `tour` now uses this session.")


def wait_for_webmail(page, ready, timeout=30):
    """Wait for `ready`, or stop if webmail sent the browser to sign in.

    Webmail's script sends the browser to sign in after the page loads, so
    this watches the address until `ready` shows.
    """
    deadline = time.monotonic() + timeout
    loc = page.locator(ready).first
    while True:
        if is_signin_url(page.url):
            raise SystemExit(EXPIRED.format(url=page.url.split("?")[0]))
        if loc.is_visible():
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"{ready} did not show in {timeout} s at {page.url.split('?')[0]}")
        time.sleep(0.25)


def page_box(page, selector):
    """The bounding box of `selector` in CSS pixels, with the page size."""
    loc = page.locator(selector)
    if loc.count() != 1:
        raise RuntimeError(f"{selector} matches {loc.count()} elements, not 1")
    box = loc.bounding_box()
    if box is None:
        raise RuntimeError(f"{selector} is not visible")
    box["page"] = (VIEWPORT["width"], VIEWPORT["height"])
    # The label in the locale's words, for index.md only.
    box["label"] = loc.evaluate("""e => e.getAttribute('aria-label') || e.getAttribute('title')
      || (e.getAttribute('aria-labelledby') || '').split(' ').map(id => document.getElementById(id)?.textContent.trim() ?? '').join(' ').trim()
      || e.textContent.trim()""")
    return box


def save_step(page, folder, step, selector=None, say=print):
    page.mouse.move(0, 0)  # no hover effect in the screenshot
    page.evaluate(SETTLE_ANIMATIONS_JS)
    box = page_box(page, selector) if selector else None
    focus = page_box(page, step["focus"])
    shot = page.screenshot()
    png, rect, size = shrink_and_outline(shot, box)
    with open(os.path.join(folder, step["file"]), "wb") as f:
        f.write(png)
    crop = crop_rect(focus, box, focus["page"])
    cpng, crect, csize = crop_and_outline(shot, focus["page"], crop, box)
    with open(os.path.join(folder, crop_file(step["file"])), "wb") as f:
        f.write(cpng)
    step = dict(step, label=box["label"] if box else None,
                size=list(size), outline_rect=list(rect) if rect else None,
                crop_file=crop_file(step["file"]), crop=list(crop), crop_size=list(csize),
                crop_outline_rect=list(crect) if crect else None)
    say(f"{step['file']}: {size[0]} × {size[1]} px"
        + (f", outline {rect} ({box['label']})" if rect else "")
        + f"; crop {csize[0]} × {csize[1]} px" + (f", outline {crect}" if crect else ""))
    return step


def tour(out=DEFAULT_OUT, headless=True, locale=DEFAULT_LOCALE, today=None, say=print):
    from playwright.sync_api import sync_playwright

    if not os.path.isdir(PROFILE):
        raise SystemExit("There is no signed-in profile. Run `uv run webmail_shot.py login` first.")
    today = today or datetime.date.today()
    with sync_playwright() as p:
        ctx = open_context(p, headless=headless, locale=locale)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(WEBMAIL_URL)
            name = tour_name(today, locale)
            folder = os.path.join(out, name)
            done = []
            for i, step in enumerate(STEPS):
                wait_for_webmail(page, step["ready"])
                if step.get("gone"):
                    page.locator(step["gone"]).wait_for(state="hidden", timeout=10000)
                if step["ready"] == MESSAGE_LIST:
                    waited = wait_until_stable(lambda: page.evaluate(LIST_STATE_JS, MESSAGE_LIST))
                    say(f"The message list stayed the same for {SETTLE_S} s after {waited:.1f} s.")
                if i == FIRST_SETTINGS_STEP:
                    start = page.evaluate(SETTINGS_JS)
                    wrong = settings_differences(START_SETTINGS, start)
                    if wrong:
                        raise SystemExit("The test account's settings are not what the tour expects, "
                                         "so it clicked nothing in Settings:\n  " + "\n  ".join(wrong))
                if i == LAST_SETTINGS_STEP:
                    end = page.evaluate(SETTINGS_JS)
                os.makedirs(folder, exist_ok=True)
                done.append(save_step(page, folder, step, step["outline"], say))
                if step["outline"]:
                    page.locator(step["outline"]).click()
                for sel in step.get("undo", []):
                    page.locator(sel).click()
                    page.wait_for_timeout(300)
                if step.get("undo"):
                    wait_for_webmail(page, step["undo_ready"])
        finally:
            ctx.close()
    with open(os.path.join(folder, "index.md"), "w", encoding="utf-8") as f:
        f.write(index_md(name, done))
    with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as f:
        f.write(index_json(name, locale, today, done))
    saved = [f for s in done for f in (s["file"], s["crop_file"]) if os.path.isfile(os.path.join(folder, f))]
    asked = [f for s in STEPS for f in (s["file"], crop_file(s["file"]))]
    if saved != asked:
        raise SystemExit(f"The tour asked for {asked} but saved {saved}.")
    say(f"Saved all {len(saved)} of {len(asked)} screenshots ({len(STEPS)} steps), "
        f"index.md and index.json in {folder}.")
    changed = settings_differences(START_SETTINGS, end)
    if changed:
        raise SystemExit("The tour changed the test account's settings. Fix them by hand:\n  "
                         + "\n  ".join(changed))
    say(f"The settings at the end are the same as at the start: {end}.")
    return folder, done


def main(argv):
    if not argv or argv[0] not in ("login", "tour"):
        print(__doc__)
        return 2
    if argv[0] == "login":
        login()
        return 0
    out = argv[argv.index("--out") + 1] if "--out" in argv else DEFAULT_OUT
    locale = argv[argv.index("--locale") + 1] if "--locale" in argv else DEFAULT_LOCALE
    tour(out=out, headless="--headed" not in argv, locale=locale)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
