# webmail-screenshot-tour

This repository makes screenshot tours of [Thundermail webmail](https://webmail.thundermail.com)
in Firefox. The screenshots are for the Thundermail Knowledge Base at
[support.tb.pro/hc](https://support.tb.pro/hc).

The plan is in issue #1. The tour (issues #3 and #4) signs in, opens the account menu and Settings,
and clicks every control in the Settings dialog. It needs [uv](https://docs.astral.sh/uv/) and Firefox Release.

```
uv run webmail_shot.py login   # one time: sign in to the test account by hand
uv run webmail_shot.py tour    # saves screenshots/DATE-LOCALE/
uv run webmail_shot.py publish screenshots/DATE-LOCALE   # zip it as a GitHub release asset
uv run test_webmail_shot.py    # tests that need no browser
```

The tour for the Thunderbird desktop app is in
[thunderbird/sumo-linter](https://github.com/thunderbird/sumo-linter), in `tools/checklist/tb_shot.py`.

## License

MPL-2.0. See `LICENSE`.
