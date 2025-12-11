import argparse
import asyncio
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence
import sys

# Try to import pyperclip, handle if missing
try:
    import pyperclip
except ImportError:
    pyperclip = None

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

# Using .is or .li often works better as a base, but .ph is the main hub.
# Redirects happen automatically.
ARCHIVE_URL = "https://archive.ph/"

@dataclass
class SubmissionResult:
    succeeded: bool
    destination_url: Optional[str]
    detail: str
    active_page: Page

async def prompt_for_captcha_resolution(page: Page) -> None:
    """
    Checks for common captcha/challenge indicators. If found, pauses execution
    and waits for the user to solve it in the browser.
    """
    # Common Cloudflare or Google Recaptcha frames/divs
    captcha_locators = [
        "iframe[src*='captcha']",
        "iframe[src*='turnstile']",
        "iframe[src*='challenge']",
        "#cf-challenge-running", 
        "text='One more step'",
        "text='Verify you are human'"
    ]

    detected = False
    for locator in captcha_locators:
        try:
            count = await page.locator(locator).count()
            if count > 0:
                detected = True
                break
        except Exception:
            continue

    if detected:
        print("\n" + "!" * 60)
        print("CAPTCHA DETECTED!")
        print("Please switch to the browser window, solve the puzzle,")
        print("and wait for the page to load normally.")
        print("!" * 60 + "\n")
        
        # In a real GUI environment, we just wait for the user to press Enter in terminal
        await asyncio.to_thread(input, ">> Press ENTER in this terminal AFTER solving the captcha... ")
        
        # Wait a moment for page to stabilize after user input
        await page.wait_for_timeout(2000)

async def handle_wip_page(page: Page, timeout: float) -> str:
    """
    Archive.ph redirects to a /wip/ (work in progress) URL while processing.
    We must wait for this to change to the final timestamped URL.
    """
    print("Processing (WIP) page detected. Waiting for archive to finish...")
    print("This can take 1-5 minutes. Please be patient.")

    start_time = asyncio.get_running_loop().time()
    
    while True:
        current_url = page.url
        
        # If we are no longer on a WIP page and not on the home page, we are likely done
        if "/wip/" not in current_url and "submitid" not in current_url and current_url != ARCHIVE_URL:
             # Double check: does the page look like a finished archive?
             # Usually contains a header with the date or "share" buttons
             return current_url

        # Check for failure text
        content = await page.content()
        if "No results" in content and "search" in current_url:
            return None # Search failed

        if (asyncio.get_running_loop().time() - start_time) > timeout:
            print("Timeout waiting for WIP page to finish.")
            return current_url # Return whatever we have

        # Wait 2 seconds before checking again
        await asyncio.sleep(2)

async def attempt_submission(
    context: BrowserContext,
    page: Page,
    url_value: str,
    selectors: Sequence[str],
    timeout: float,
    label: str,
) -> SubmissionResult:
    starting_url = page.url

    # Find a working selector
    target_selector = None
    for selector in selectors:
        if await page.locator(selector).count() > 0:
            target_selector = selector
            break
    
    if not target_selector:
        return SubmissionResult(False, None, f"[{label}] No input box found.", page)

    print(f"[{label}] Submitting via '{target_selector}'...")
    
    try:
        target = page.locator(target_selector).first
        await target.click()
        await target.fill(url_value)
        await target.press("Enter")
    except Exception as e:
        return SubmissionResult(False, None, f"Error interacting with page: {e}", page)

    # 1. Wait for initial navigation (leaving the home page)
    try:
        await page.wait_for_url(lambda u: u != starting_url, timeout=15000)
    except PlaywrightTimeoutError:
        print(f"[{label}] Timed out waiting for initial form submission.")
        return SubmissionResult(False, None, "Form submission timeout", page)

    # 2. Check for Captcha immediately after submission
    await prompt_for_captcha_resolution(page)

    # 3. Handle "WIP" (Loading) Phase
    # Archive.ph often goes: Home -> WIP -> Final
    if "/wip/" in page.url or "submitid" in page.url:
        final_url = await handle_wip_page(page, timeout)
        if final_url:
            return SubmissionResult(True, final_url, "Archived successfully via WIP", page)
        else:
             return SubmissionResult(False, page.url, "WIP finished but no valid URL found", page)

    # 4. If we went straight to a result (rare but happens on cached hits)
    await page.wait_for_load_state("domcontentloaded")
    return SubmissionResult(True, page.url, "Direct navigation to result", page)


def resolve_url_from_args_or_clipboard(provided: Optional[str]) -> str:
    if provided:
        return provided
    
    if pyperclip:
        url_from_clipboard = pyperclip.paste().strip()
        if url_from_clipboard:
            return url_from_clipboard
            
    print("Error: No URL provided and clipboard is empty (or pyperclip not installed).")
    sys.exit(1)

async def run_automation(
    target_url: str,
    timeout: float,
    headless: bool,
    primary_selectors: Sequence[str],
    fallback_selectors: Sequence[str],
) -> None:
    
    # Setup browser with anti-bot flags
    async with async_playwright() as p:
        # Launch options to make it look more 'human'
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"] 
        )
        
        # Use a generic User-Agent to avoid immediate blocking
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print(f"Opening {ARCHIVE_URL} ...")
        try:
            await page.goto(ARCHIVE_URL, timeout=30000)
        except Exception as e:
            print(f"Error loading {ARCHIVE_URL}: {e}")
            await browser.close()
            return

        await prompt_for_captcha_resolution(page)

        # --- Attempt 1: New Archive (Red Box) ---
        print("\n--- Attempting New Archive (Red Box) ---")
        primary_result = await attempt_submission(
            context, page, target_url, primary_selectors, timeout, label="primary"
        )

        if primary_result.succeeded:
            print("\n" + "="*60)
            print(f"SUCCESS! Archive URL: {primary_result.destination_url}")
            print("="*60 + "\n")
            # Keep browser open briefly so user can see it if not headless
            if not headless:
                print("Leaving browser open for 10 seconds to view result...")
                await asyncio.sleep(10)
            await browser.close()
            return

        # --- Attempt 2: Search Existing (Black Box) ---
        print("\n--- Primary failed. Attempting Search (Black Box) ---")
        
        # Reload home
        await page.goto(ARCHIVE_URL)
        await prompt_for_captcha_resolution(page)

        fallback_result = await attempt_submission(
            context, page, target_url, fallback_selectors, timeout, label="fallback"
        )

        if fallback_result.succeeded:
            print("\n" + "="*60)
            print(f"SUCCESS (Found Existing): {fallback_result.destination_url}")
            print("="*60 + "\n")
            if not headless:
                await asyncio.sleep(10)
        else:
            print("\nFAILED. Could not obtain a result URL.")
            print(f"Last URL was: {fallback_result.destination_url}")

        await browser.close()


def main():
    parser = argparse.ArgumentParser(description="Automate archive.ph submission.")
    parser.add_argument("url", nargs="?", help="URL to submit.")
    
    # Increased default timeout to 300s (5 mins) because archiving is slow
    parser.add_argument("--timeout", type=float, default=300, help="Wait timeout in seconds.")
    
    # Default is HEADED (visible) because captchas are likely
    parser.add_argument("--headless", action="store_true", help="Run invisible (not recommended).")
    
    args = parser.parse_args()
    target_url = resolve_url_from_args_or_clipboard(args.url)

    # Refined Selectors based on current archive.ph layout
    # "submiturl" is the red box (create new), "searchurl" is the black box (find existing)
    primary_selectors = ["#submiturl input[name='url']", "#url", "input[name='url']"]
    fallback_selectors = ["#searchurl input[name='url']", "input[name='search']", "#search"]

    print(f"Target URL: {target_url}")
    
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
