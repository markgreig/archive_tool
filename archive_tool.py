import argparse
import asyncio
import sys
import time
from typing import Optional

# Try to import pyperclip
try:
    import pyperclip
except ImportError:
    pyperclip = None

from playwright.async_api import async_playwright, Page, BrowserContext

# --- CONFIGURATION ---
ARCHIVE_URL = "https://archive.ph/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

async def check_for_captcha(page: Page):
    """Checks for captcha challenges and pauses if found."""
    # Common selectors for captchas on archive.ph
    captcha_selectors = [
        "iframe[src*='recaptcha']",
        "iframe[src*='hcaptcha']",
        "iframe[src*='turnstile']",
        "#cf-challenge-running",
        "text='One more step'",
        "text='Verify you are human'"
    ]

    detected = False
    for selector in captcha_selectors:
        if await page.locator(selector).count() > 0:
            detected = True
            break
    
    if detected:
        print("\n" + "!" * 50)
        print("CAPTCHA DETECTED!")
        print("1. Switch to the browser window.")
        print("2. Solve the captcha (click the box/images).")
        print("3. Wait for the page to reload.")
        print("!" * 50)
        
        # Wait for user to solve it. We monitor the URL or page state.
        print(">> Waiting 60 seconds for you to solve the captcha...")
        # We simply wait here; if the user solves it, the script will proceed naturally
        # when it tries to find the next element.
        await asyncio.sleep(15) 
        input(">> Press ENTER here once you have solved it and the page has loaded: ")

async def handle_loading_screen(page: Page):
    """Waits if the site redirects to a '/wip/' loading URL."""
    if "/wip/" in page.url or "submitid" in page.url:
        print(f"\n[Status] Archiving in progress... (Current URL: {page.url})")
        print("[Status] This can take 2 to 5 minutes. Please wait.")
        
        start_time = time.time()
        while "/wip/" in page.url or "submitid" in page.url:
            await asyncio.sleep(5)
            # Check for timeout (5 mins)
            if time.time() - start_time > 300:
                print("[Error] Timed out waiting for archive to finish.")
                return None
            
            # Print a dot every 5 seconds to show life
            print(".", end="", flush=True)

        print("\n[Status] Processing finished!")
    return page.url

async def run_archiver(target_url: str, headless: bool = False):
    print(f"--- Archive Tool V3 Starting ---")
    print(f"Target: {target_url}")
    
    async with async_playwright() as p:
        # Launch browser (Headless=False recommended so you can see what's happening)
        browser = await p.chromium.launch(headless=headless, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        # 1. Open Website
        print(f"Opening {ARCHIVE_URL}...")
        try:
            await page.goto(ARCHIVE_URL, timeout=60000)
        except Exception as e:
            print(f"Error loading site: {e}")
            await browser.close()
            return

        await check_for_captcha(page)

        # 2. Try the RED BOX (New Archive)
        print("\n[Attempt 1] Trying 'Red Box' (Create New)...")
        red_input = page.locator("#submiturl input[name='url']")
        
        if await red_input.count() > 0:
            await red_input.fill(target_url)
            
            # Find the submit button specifically inside the #submiturl form
            submit_btn = page.locator("#submiturl input[type='submit']")
            if await submit_btn.count() == 0:
                submit_btn = page.locator("#submiturl button")
            
            print("[Action] Clicking Save button...")
            await submit_btn.click()
            
            # Wait for navigation
            try:
                await page.wait_for_url(lambda u: u != ARCHIVE_URL, timeout=15000)
                await check_for_captcha(page)
                final_url = await handle_loading_screen(page)
                
                print(f"\nSUCCESS! Archive URL: {final_url}")
                if not headless:
                    print("Leaving browser open for 10s to view...")
                    await asyncio.sleep(10)
                await browser.close()
                return
            except Exception as e:
                print(f"[Info] Red box navigation failed or timed out: {e}")
        else:
            print("[Error] Could not find the Red Box input field.")

        # 3. Try the BLACK BOX (Search Existing)
        print("\n[Attempt 2] Red box failed. Trying 'Black Box' (Search)...")
        # Go back to home if needed
        if "/wip/" in page.url or page.url != ARCHIVE_URL:
            await page.goto(ARCHIVE_URL)
            await check_for_captcha(page)

        black_input = page.locator("#searchurl input[name='q'], #searchurl input[name='url']")
        
        if await black_input.count() > 0:
            await black_input.first.fill(target_url)
            
            search_btn = page.locator("#searchurl input[type='submit'], #searchurl button")
            print("[Action] Clicking Search button...")
            await search_btn.first.click()
            
            try:
                await page.wait_for_url(lambda u: u != ARCHIVE_URL, timeout=15000)
                await check_for_captcha(page)
                
                # Check if "No results" found
                content = await page.content()
                if "No results" in content:
                    print("\n[Result] No existing archive found for this URL.")
                else:
                    print(f"\nSUCCESS! Found existing archive: {page.url}")
            except Exception as e:
                print(f"[Error] Search navigation failed: {e}")
        else:
            print("[Error] Could not find the Black Box input field.")

        await browser.close()

def get_url_from_args():
    # 1. specific arg
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", help="URL to submit")
    args = parser.parse_args()
    
    if args.url: 
        return args.url
    
    # 2. clipboard
    if pyperclip:
        txt = pyperclip.paste().strip()
        if txt.startswith("http"):
            return txt
    
    print("Error: No URL provided and clipboard empty.")
    sys.exit(1)

if __name__ == "__main__":
    target = get_url_from_args()
    asyncio.run(run_archiver(target, headless=False))
