import argparse
import asyncio
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import pyperclip
from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

ARCHIVE_URL = "https://archive.ph/"


@dataclass
class SubmissionResult:
    succeeded: bool
    destination_url: Optional[str]
    detail: str
    active_page: Page


async def prompt_for_captcha_resolution(page: Page) -> None:
    """
    If a captcha is present, pause execution until the user confirms it has been solved.
    The function searches for common captcha iframe patterns and only prompts when
    necessary to avoid unnecessary blocking.
    """

    captcha_locators = [
        "iframe[src*='captcha']",
        "iframe[src*='hcaptcha']",
        "iframe[src*='recaptcha']",
        "div#cf-challenge-running",
    ]
    for locator in captcha_locators:
        if await page.locator(locator).count() > 0:
            print("Captcha detected. Please complete it in the browser window.")
            await asyncio.to_thread(input, "Press Enter after solving the captcha...")
            break


async def wait_for_result_navigation(
    context: BrowserContext, page: Page, starting_url: str, timeout: float
) -> tuple[Optional[str], Page]:
    """
    Wait for either the current page to navigate away from the starting URL or for a
    new page to open. Returns the resulting URL (if any) and the active page object
    that contains that URL.
    """

    navigation_task = asyncio.create_task(
        page.wait_for_url(lambda url: url != starting_url, timeout=timeout)
    )
    new_page_task = asyncio.create_task(context.wait_for_event("page", timeout=timeout))

    done, pending = await asyncio.wait(
        {navigation_task, new_page_task}, return_when=asyncio.FIRST_COMPLETED
    )

    result_url: Optional[str] = None
    active_page = page

    for task in done:
        try:
            result = task.result()
            if result is None:
                if page.url != starting_url:
                    result_url = page.url
            elif hasattr(result, "url"):
                # context.wait_for_event("page") returns a Page
                active_page = result
                await active_page.wait_for_load_state("networkidle")
                result_url = active_page.url
        except PlaywrightTimeoutError:
            pass

    for task in pending:
        task.cancel()

    return result_url, active_page


async def attempt_submission(
    context: BrowserContext,
    page: Page,
    url_value: str,
    selectors: Sequence[str],
    timeout: float,
    label: str,
) -> SubmissionResult:
    starting_url = page.url

    for selector in selectors:
        locator = page.locator(selector)
        if await locator.count() == 0:
            continue

        target = locator.first
        print(f"[{label}] Using selector '{selector}' to submit the URL...")
        await target.click()
        await target.fill(url_value)
        await target.press("Enter")

        result_url, active_page = await wait_for_result_navigation(
            context, page, starting_url, timeout
        )
        if result_url:
            return SubmissionResult(
                True, result_url, f"Navigated to {result_url} via {selector}", active_page
            )

        print(f"[{label}] No navigation detected with selector '{selector}'. Trying next.")

    return SubmissionResult(False, None, "No matching selectors produced a result", page)


def resolve_url_from_args_or_clipboard(provided: Optional[str]) -> str:
    if provided:
        return provided

    url_from_clipboard = pyperclip.paste().strip()
    if not url_from_clipboard:
        raise ValueError("No URL provided and clipboard is empty.")

    return url_from_clipboard


async def run_automation(
    target_url: str,
    timeout: float,
    headless: bool,
    primary_selectors: Sequence[str],
    fallback_selectors: Sequence[str],
) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()

        print(f"Opening {ARCHIVE_URL} ...")
        await page.goto(ARCHIVE_URL)
        await prompt_for_captcha_resolution(page)

        primary_result = await attempt_submission(
            context, page, target_url, primary_selectors, timeout, label="primary"
        )

        if primary_result.succeeded:
            print(f"Archive created or loaded: {primary_result.destination_url}")
            if primary_result.active_page.url != primary_result.destination_url:
                await primary_result.active_page.goto(primary_result.destination_url)
            await browser.close()
            return

        print("Primary submission did not yield a result. Trying fallback path...")
        await page.goto(ARCHIVE_URL)
        await prompt_for_captcha_resolution(page)

        fallback_result = await attempt_submission(
            context, page, target_url, fallback_selectors, timeout, label="fallback"
        )

        if fallback_result.succeeded:
            print(f"Fallback archive result: {fallback_result.destination_url}")
            if fallback_result.active_page.url != fallback_result.destination_url:
                await fallback_result.active_page.goto(fallback_result.destination_url)
        else:
            print("Automation could not obtain a result URL from archive.ph.")

        await browser.close()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Automate submitting a URL to archive.ph, waiting for a result, and "
            "falling back to the secondary submission box when necessary."
        )
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="URL to submit. If omitted, the clipboard contents will be used.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180,
        help="Seconds to wait for archive.ph to return a result before falling back.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser in headless mode (not recommended when captchas appear).",
    )
    parser.add_argument(
        "--primary-selector",
        action="append",
        dest="primary_selectors",
        help="Override the selectors used for the primary submission box.",
    )
    parser.add_argument(
        "--fallback-selector",
        action="append",
        dest="fallback_selectors",
        help="Override the selectors used for the fallback submission box.",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    target_url = resolve_url_from_args_or_clipboard(args.url)

    primary_selectors = (
        args.primary_selectors
        if args.primary_selectors
        else ["form#submiturl input[name='url']", "input#submiturl", "input[name='url']"]
    )
    fallback_selectors = (
        args.fallback_selectors
        if args.fallback_selectors
        else ["form#searchurl input[name='url']", "input#searchurl", "input[name='q']"]
    )

    print(f"Submitting URL: {target_url}")
    asyncio.run(
        run_automation(
            target_url=target_url,
            timeout=args.timeout,
            headless=args.headless,
            primary_selectors=primary_selectors,
            fallback_selectors=fallback_selectors,
        )
    )


if __name__ == "__main__":
    main()
