# CLAUDE.md

## What this is

`webmail-screenshot-tour` makes screenshot tours of webmail.thundermail.com in Firefox. The
screenshots are for the Thundermail Knowledge Base on Zendesk, at support.tb.pro/hc. They are
not for SUMO (support.mozilla.org).

A tour is a script that clicks through the webmail screens in a fixed order. It saves one PNG
for each step, plus `index.md` and `index.json` that list the steps.

The design record is issue #1. Read it and its comments first. The tour is `webmail_shot.py`,
and its tests that need no browser are in `test_webmail_shot.py` (issues #3 and #4). The tour
undoes every setting it changes, and stops if the test account's settings are not what it
expects at the start.

This repository started on 2026-11-25. It follows two pieces of work in
[thunderbird/sumo-linter](https://github.com/thunderbird/sumo-linter):

- sumo-linter #55 and #56: the screenshot tour of Thunderbird desktop Settings, in
  `tools/checklist/tb_shot.py`. Use its tour as the model for this one.
- sumo-linter #63: a test of `@mozilla/firefox-mcp`. The report is
  `docs/firefox-mcp-screenshot-test.md` in that repository.

## Decisions already made

- Playwright drives Firefox. The script is repeatable and runs without an AI agent.
- firefox-mcp is only for exploration. Its `take_snapshot` lists each control with its role
  and label, which helps you find IDs before you write the script. Webmail is an ordinary web
  page, so firefox-mcp needs no `MOZ_REMOTE_ALLOW_SYSTEM_ACCESS` for it.
- The script finds each control by ID or ARIA role, never by visible text. This way, the
  same tour works in other locales.
- A tour is named by date and locale, for example `2026-11-25-en-US`. Webmail has no
  version numbers, unlike Thunderbird desktop.
- Tours are published as GitHub release assets, one zip for each tour, as `tb_shot.py
  publish` does. They are not committed. `screenshots/` is gitignored.
- The tour starts at webmail.thundermail.com, signs in through OAuth, and then visits the
  Settings screens, as the desktop tour does.
- This repository is public, so every committed file and every published screenshot is
  public too.

## Sign-in and private data

The tour stores no secret. A `login` command opens Firefox with a persistent profile in
`.auth/`, and the maintainer signs in by hand in the OAuth window. Later runs reuse that
session. If the session expires, the tour sees the redirect to the OAuth sign-in page, stops
before it takes a screenshot, and asks the maintainer to run `login` again. Never commit
`.auth/`, because it holds session cookies. The script never types a password and never asks
for one, and no password goes in a file or an environment variable.

A webmail screenshot shows a mailbox. The screenshot rules below forbid personal
information, including email addresses and user names. Before a tour is published, make sure
that no screenshot shows private data. Use only the test account, `nemo@thundermail.com`. It
holds no private data, and as a dedicated test address it can show in screenshots.

## Screenshot rules

support.tb.pro has no screenshot guide that we know of, so the tour uses SUMO's rules for now
(issue #2). If support.tb.pro publishes its own rules, a new issue will replace these.

The source is the SUMO article `how-to-make-screenshots`. A copy of its wiki source is in
sumo-linter at `corpus-other/markup-docs/en-US/how-to-make-screenshots.wiki`.

- Keep each image under 620 pixels in width and in height. The KB theme at support.tb.pro
  shows images at most 768 pixels wide, so 620 pixels stays below that limit.
- Outline the important item with a red outline, color `#f71701`, with square corners.
- Make the outline 4 to 8 points thick, and center the item inside it.
- Crop to the part of the screen that the task needs.
- Remove all personal information.
- Save as PNG.

`tb_shot.py` learned one more thing: around a small item, such as a 28 px row, a thick
outline covers the text of its neighbors. Its tour draws the outline 6 px thick and centers
it on the item's edge.

## Conventions

- Every change gets a GitHub issue as its design record, however small. Commit straight to
  `main` with the issue number in the message. Do not make a branch or a pull request.
- Put the MPL-2.0 header on every new source file.
- Write tests with the code. Put logic that needs no browser in pure functions, so that CI
  can test it without a browser.
- Report measured values (sizes in pixels, colors, computed styles), not visual judgments.
- When a loop finishes, compare what it recorded with what it asked for, and say which case
  it hit. "Nothing left to do" and "nothing was done" look the same from inside a loop.
