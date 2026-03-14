from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

profile_spec = importlib.util.spec_from_file_location("profile", PROJECT_ROOT / "profile.py")
if profile_spec and profile_spec.loader:
    profile_module = importlib.util.module_from_spec(profile_spec)
    profile_spec.loader.exec_module(profile_module)
    sys.modules["profile"] = profile_module

from browser.engine import BrowserEngine
from linkedin.auth import LinkedInAuth
from Outreach.messenger import MESSAGE_BUTTON_SELECTORS, MESSAGE_INPUT_SELECTORS, SEND_MESSAGE_SELECTORS

URL = "https://www.linkedin.com/in/nidhivaishnav/"
MESSAGE = "Hi Nidhi — I’m building ZapPay, a zero-wait fuel payments layer for India’s petrol pumps. We’re piloting across 10–20 pumps and opening our seed round. Would you be open to a short conversation?"


async def main() -> None:
    browser = BrowserEngine()
    await browser.start()
    try:
        auth = LinkedInAuth(browser)
        print("logged_in", await auth.login())
        await browser.goto(URL)
        await asyncio.sleep(3)

        page = browser.page
        body = await browser.get_page_text()
        print("BODY_BEFORE_START")
        print(body[:3000])
        print("BODY_BEFORE_END")

        before_counts = {}
        for selector in MESSAGE_BUTTON_SELECTORS + MESSAGE_INPUT_SELECTORS + SEND_MESSAGE_SELECTORS:
            try:
                before_counts[selector] = await page.locator(selector).count()
            except Exception as exc:
                before_counts[selector] = f"ERR: {exc}"
        print("COUNTS_BEFORE", json.dumps(before_counts, indent=2, default=str))

        clicked = False
        for selector in MESSAGE_BUTTON_SELECTORS:
            try:
                if await browser.safe_click(selector, timeout=4000):
                    print("CLICKED_SELECTOR", selector)
                    clicked = True
                    break
            except Exception as exc:
                print("CLICK_FAIL", selector, str(exc))
        print("CLICKED", clicked)
        await asyncio.sleep(5)

        after_counts = {}
        for selector in MESSAGE_BUTTON_SELECTORS + MESSAGE_INPUT_SELECTORS + SEND_MESSAGE_SELECTORS:
            try:
                after_counts[selector] = await page.locator(selector).count()
            except Exception as exc:
                after_counts[selector] = f"ERR: {exc}"
        print("COUNTS_AFTER", json.dumps(after_counts, indent=2, default=str))

        body = await browser.get_page_text()
        print("BODY_AFTER_START")
        print(body[:5000])
        print("BODY_AFTER_END")

        typed = False
        for selector in MESSAGE_INPUT_SELECTORS:
            try:
                group = page.locator(selector)
                count = await group.count()
                for idx in range(count):
                    locator = group.nth(idx)
                    if not await locator.is_visible():
                        continue
                    print("TRY_TYPE_SELECTOR", selector, idx)
                    await browser.human_type(locator, MESSAGE, clear_first=True)
                    await asyncio.sleep(2)
                    payload = await locator.evaluate("el => ({ text: el.textContent || '', html: el.innerHTML || '', value: 'value' in el ? el.value : '', contenteditable: !!el.isContentEditable })")
                    print("COMPOSER_AFTER_TYPE", json.dumps(payload, indent=2, default=str))
                    typed = True
                    break
                if typed:
                    break
            except Exception as exc:
                print("TYPE_FAIL", selector, str(exc))

        send_state = {}
        for selector in SEND_MESSAGE_SELECTORS:
            try:
                group = page.locator(selector)
                count = await group.count()
                info = []
                for idx in range(count):
                    locator = group.nth(idx)
                    try:
                        info.append(await locator.evaluate("el => ({ text: el.textContent || '', aria: el.getAttribute('aria-label') || '', disabled: !!el.disabled, classes: el.className || '' })"))
                    except Exception as exc:
                        info.append({"error": str(exc)})
                send_state[selector] = info
            except Exception as exc:
                send_state[selector] = f"ERR: {exc}"
        print("SEND_STATE", json.dumps(send_state, indent=2, default=str))

        shot = await browser.take_screenshot("vc_message_debug_nidhi")
        print("SCREENSHOT", shot)
    finally:
        await browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
