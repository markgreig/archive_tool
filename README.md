# archive_tool

A small Playwright-based helper for automating submissions to [archive.ph](https://archive.ph/).
The script can pull a URL from your clipboard (or take it on the command line), paste it
into the site's main submission box, wait for the returned archive link, and fall back to
the alternate input if the first attempt does not return a result.

## Prerequisites

* Python 3.10+
* Google Chrome/Chromium installed by Playwright (see below)

Install dependencies and browser support:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Usage

```bash
python archive_tool.py "https://example.com/article"
```

If you omit the URL, the script reads from your clipboard:

```bash
python archive_tool.py
```

Useful flags:

* `--timeout 240` — seconds to wait for archive.ph to return a link before falling back.
* `--headless` — runs Chromium without a visible window (not recommended if a captcha is shown).
* `--primary-selector` / `--fallback-selector` — override the CSS selectors used for the
  primary and secondary input boxes if archive.ph changes its layout.

During execution the script opens archive.ph in Chromium, enters the URL, and waits for a
navigation or new page. If a captcha frame is detected, you'll be prompted to complete it
manually in the browser window before the script continues.

## Testing

Dependency installation was verified with:

```bash
pip install -r requirements.txt
```
