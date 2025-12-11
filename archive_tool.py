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

ARCHIVE_URL = "https://archive.ph/"

@dataclass
class SubmissionResult:
    succeeded: bool
    destination_url: Optional[str]
    detail: str
    active_page: Page

async def prompt_for_captcha_resolution(page: Page) -> None:
    """
    Checks for captcha frames. If found, pauses and alerts the user.
    """
    captcha_locators = [
        "iframe[src*='captcha']",
        "iframe[src*='turnstile']",
        "#cf-challenge-running", 
        "text='One more step'",
        "text='Verify you are human'"
    ]

    detected = False
    for locator in captcha_locators:
        try:
            if await page.locator(locator).count() > 0:
                detected = True
                break
        except Exception:
            continue

    if detected:
        print("\n" + "!" * 60)
        print("CAPTCHA DETECTED! Please solve it in the opened browser window.")
        print("!" * 60 + "\n")
        await asyncio.to_thread(input, ">> Press ENTER here AFTER you have solved the captcha... ")
        await page.wait_for_timeout(2000)

async def handle_wip_page(page: Page, timeout: float) -> Optional[str]:
    """
    Waits for the 'Work In Progress' (/wip/) page to redirect to the final archive.
    """
    print("Page is processing (WIP). Waiting for final snapshot (this takes time)...")
    start_time = asyncio.get_running_loop().time()
    
    while True:
        current_url = page.url
        # If we are not on a WIP page, not on the home page, and not loading... we are done.
        if "/wip/" not in current_url and "submitid" not in current_url and current_url != ARCHIVE_URL:
            return current_url
        
        # Check for timeout
        if (asyncio.get_running_loop().time() - start_time) > timeout:
            print("Timeout waiting for processing to finish.")
            return current_url
        
        await asyncio.sleep(2)

async def attempt_submission(
    context: BrowserContext,
    page: Page,
    url_value: str,
    form_selector: str,  # The ID of the form (e.g., '#submiturl')
    input_selector: str, # The selector for the input box inside that form
    timeout: float,
    label: str,
) -> SubmissionResult:
    starting_url = page.url
    
    # 1. Locate the Input Field
    input_locator = page.locator(f"{form_selector} {input_selector}")
    if await input_locator.count() == 0:
        return SubmissionResult(False, None, f"Input box {input_selector} not found", page)
    
    print(f"[{label}] Pasting URL into '{form_selector}'...")
    try:
        await input_locator.first.click()
        await input_locator.first.fill(url_value)
    except Exception as e:
        return SubmissionResult(False, None, f"Error filling input: {e}", page)

    # 2. Locate and Click the Submit Button (CRITICAL FIX)
    # We look for a submit input or button specifically inside the parent form
    submit_btn = page.locator(f"{form_selector} input[type='submit']")
    if await submit_btn.count() == 0:
        submit_btn = page.locator(f"{form_selector} button")
    
    if await submit_btn.count() > 0:
        print(f"[{label}] Clicking 'Save/Search' button...")
        await submit_btn.first.click()
    else:
        print(f"[{label}] Button not found, trying Enter key...")
        await input_locator.first.press("Enter")

    # 3. Wait for Navigation
    try:
        # Wait until URL changes from the starting URL
        await page.wait_for_url(lambda u: u != starting_url, timeout=15000)
    except PlaywrightTimeoutError:
        print(f"[{label}] No navigation detected (Timeout).")
        return SubmissionResult(False, None, "Navigation timeout", page)

    # 4. Check for Captcha again after click
    await prompt_for_captcha_resolution(page)

    # 5. Handle WIP / Processing Redirects
    if "/wip/" in page.url or "submitid" in page.url:
        final_url = await handle_wip_page(page, timeout)
        if final_url:
            return SubmissionResult(True, final_url, "Archived via WIP", page)
    elif page.url != starting_url:
        # Direct success
        return SubmissionResult(True, page.url, "Direct success", page)

    return SubmissionResult(False, None, "Unknown failure", page)


def resolve_url_from_args_or_clipboard(provided: Optional[str]) -> str:
    if provided:
        return provided
    if pyperclip:
        content = pyperclip.paste().strip()
        if content:
            return content
    print("Error: No URL provided and clipboard is empty.")
    sys.exit(1)

async def run_automation(target_url: str, timeout: float, headless: bool):
    async with async_playwright() as p:
        # Launch with anti-bot flags
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print(f"Opening {ARCHIVE_URL} ...")
        try:
            await page.goto(ARCHIVE_URL, timeout=30000)
        except Exception:
            print("Failed to load archive.ph homepage.")
            await browser.close()
            return

        await prompt_for_captcha_resolution(page)

        # --- Attempt 1: Red Box (Create New Archive) ---
        # Form ID: #submiturl, Input name: url
        primary_result = await attempt_submission(
            context, page, target_url, 
            form_selector="#submiturl", 
            input_selector="input[name='url']", 
            timeout=timeout, 
            label="primary"
        )

        if primary_result.succeeded:
            print("\n" + "="*60)
            print(f"RESULT: {primary_result.destination_url}")
            print("="*60 + "\n")
            if not headless:
                print("Closing in 5 seconds...")
                await asyncio.sleep(5)
            await browser.close()
            return

        # --- Attempt 2: Black Box (Search Existing) ---
        print("\nPrimary attempt failed. Trying fallback (Search)...")
        
        # Go back home if we aren't there
        if "archive" not in page.url or "/wip/" in page.url:
            await page.goto(ARCHIVE_URL)
        
        # Form ID: #searchurl, Input name: q (sometimes 'url', we try generic selector)
        # Note: The input name in the black box is often 'q' or 'url' depending on the mirror.
        # We use a generic input selector inside the form.
        fallback_result = await attempt_submission(
            context, page, target_url, 
            form_selector="#searchurl", 
            input_selector="input", 
            timeout=timeout, 
            label="fallback"
        )

        if fallback_result.succeeded:
            print("\n" + "="*60)
            print(f"RESULT (Found Existing): {fallback_result.destination_url}")
            print("="*60 + "\n")
        else:
            print("\nFAILED: Could not archive or find URL.")
        
        if not headless:
            await asyncio.sleep(2)
        await browser.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", help="URL to archive")
    parser.add_argument("--timeout", type=float, default=300, help="Timeout in seconds")
    parser.add_argument("--headless", action="store_true", help="Run without window")
    
    args = parser.parse_args()
    target_url = resolve_url_from_args_or_clipboard(args.url)
    
    print(f"Target: {target_url}")
    asyncio.run(run_automation(target_url, args.timeout, args.headless))

if __name__ == "__main__":
    main()
