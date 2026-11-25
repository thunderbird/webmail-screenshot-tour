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
3. The Settings dialog, after a click on Settings.

LOCALE is the language that Firefox asks webmail for, en-US unless you give
another. The files go to DIR/DATE-LOCALE, for example screenshots/2026-11-25-en-US,
with index.md and index.json that list the steps. The tour finds each control
by ID or ARIA role, never by visible text, so the same tour works in every
locale. If the session expired, webmail sends the browser to the OAuth sign-in
page. The tour then stops before it takes a screenshot and asks you to run
`login` again.

Each screenshot is the whole page, scaled down to fit 620 pixels each way, as
SUMO's "How to make screenshots" says. The KB theme at support.tb.pro shows
images at most 768 pixels wide, so 620 pixels stays below that limit. The outline is #f71701, square, and
OUTLINE_PX thick in the file, centered on the edge of the item.

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

# Webmail shows its cached message list first and refreshes it later, so the
# tour waits until the list stays the same for SETTLE_S seconds (#3).
SETTLE_S = 5
LIST_STATE_JS = """sel => { const l = document.querySelector(sel); if (!l) return null;
  return [l.getAttribute('aria-rowcount'), l.getAttribute('aria-busy'),
          ...[...l.querySelectorAll('[role=row]')].map(r => r.getAttribute('aria-label'))].join('\\n'); }"""

# Each step waits for `ready`, saves a screenshot with `outline` outlined,
# and then clicks `outline`, which leads to the next step.
STEPS = [
    {"file": "01-inbox-account-menu-next.png", "screen": "inbox",
     "ready": MESSAGE_LIST, "outline": ACCOUNT_MENU},
    {"file": "02-account-menu-settings-next.png", "screen": "account menu",
     "ready": ACCOUNT_MENU_POPUP, "outline": SETTINGS_ITEM},
    {"file": "03-settings.png", "screen": "settings",
     "ready": SETTINGS_DIALOG, "outline": None},
]

EXPIRED = ("The session expired: webmail sent the browser to the sign-in page at {url}.\n"
           "Run `uv run webmail_shot.py login` and sign in again. No screenshot was taken.")


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


def index_md(name, steps):
    """index.md of a tour folder: one row per step."""
    lines = [f"# Thundermail webmail tour {name}", "",
             "Made by `webmail_shot.py tour` (issue #3). Each step has one screenshot. If the step",
             "has an outlined item, the next step clicks it.", "",
             "| Step | Screen | Screenshot | Item outlined | Size (px) | Outline (left, top, right, bottom) |",
             "|---|---|---|---|---|---|"]
    for i, s in enumerate(steps, 1):
        size = "{} × {}".format(*s["size"]) if s.get("size") else ""
        rect = ", ".join(str(v) for v in s["outline"]) if s.get("outline") else ""
        cells = [str(i), s["screen"], s["file"], s.get("label") or "", size, rect]
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
    box["label"] = (loc.get_attribute("aria-label") or loc.get_attribute("title")
                    or loc.inner_text().strip())
    return box


def save_step(page, folder, step, selector=None, say=print):
    box = page_box(page, selector) if selector else None
    page.mouse.move(0, 0)  # no hover effect in the screenshot
    png, rect, size = shrink_and_outline(page.screenshot(), box)
    with open(os.path.join(folder, step["file"]), "wb") as f:
        f.write(png)
    step = dict(step, size=list(size), outline=list(rect) if rect else None,
                label=box["label"] if box else None)
    say(f"{step['file']}: {size[0]} × {size[1]} px"
        + (f", outline {rect} ({box['label']})" if rect else ""))
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
            for step in STEPS:
                wait_for_webmail(page, step["ready"])
                if step["ready"] == MESSAGE_LIST:
                    waited = wait_until_stable(lambda: page.evaluate(LIST_STATE_JS, MESSAGE_LIST))
                    say(f"The message list stayed the same for {SETTLE_S} s after {waited:.1f} s.")
                os.makedirs(folder, exist_ok=True)
                done.append(save_step(page, folder, step, step["outline"], say))
                if step["outline"]:
                    page.locator(step["outline"]).click()
        finally:
            ctx.close()
    with open(os.path.join(folder, "index.md"), "w", encoding="utf-8") as f:
        f.write(index_md(name, done))
    with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as f:
        f.write(index_json(name, locale, today, done))
    saved = [s["file"] for s in done if os.path.isfile(os.path.join(folder, s["file"]))]
    asked = [s["file"] for s in STEPS]
    if saved != asked:
        raise SystemExit(f"The tour asked for {asked} but saved {saved}.")
    say(f"Saved all {len(saved)} of {len(asked)} steps, index.md and index.json in {folder}.")
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
