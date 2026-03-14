from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

profile_spec = importlib.util.spec_from_file_location("profile", PROJECT_ROOT / "profile.py")
if profile_spec and profile_spec.loader:
    profile_module = importlib.util.module_from_spec(profile_spec)
    profile_spec.loader.exec_module(profile_module)
    sys.modules["profile"] = profile_module

from orchestrator import Orchestrator
from Outreach.referral_messenger import (
    ADD_NOTE_SELECTORS,
    CONNECT_BUTTON_SELECTORS,
    MESSAGE_BUTTON_SELECTORS,
    PENDING_BUTTON_SELECTORS,
    SEND_INVITE_SELECTORS,
)

PROFILE_URL = "https://www.linkedin.com/in/varsha-mishra-a3874b11b/"


async def visible_map(orch: Orchestrator, selectors: list[str]) -> list[dict]:
    data = []
    for selector in selectors:
        locator = orch.browser.page.locator(selector).first
        try:
            count = await locator.count()
            visible = await locator.is_visible() if count > 0 else False
            text = await locator.inner_text() if count > 0 else ""
            aria = await locator.get_attribute("aria-label") if count > 0 else ""
            data.append({
                "selector": selector,
                "count": count,
                "visible": visible,
                "text": text[:200],
                "aria": aria,
            })
        except Exception as exc:
            data.append({"selector": selector, "error": str(exc)[:200]})
    return data


async def main() -> None:
    orch = Orchestrator()
    out_path = Path("data") / f"probe_connect_state_{datetime.now():%Y%m%d_%H%M%S}.json"
    payload: dict = {}
    try:
        await orch.start()
        if not await orch.auth.login():
            payload["error"] = "login_failed"
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(out_path)
            return
        messenger = orch.referral_messenger
        await orch.browser.goto(PROFILE_URL)
        await asyncio.sleep(3)
        await messenger._dismiss_popups()
        await orch.browser.take_screenshot("probe_before_connect")
        payload["before"] = {
            "connect": await visible_map(orch, CONNECT_BUTTON_SELECTORS),
            "message": await visible_map(orch, MESSAGE_BUTTON_SELECTORS),
            "pending": await visible_map(orch, PENDING_BUTTON_SELECTORS),
            "body_text": (await orch.browser.get_page_text())[:4000],
        }
        clicked = await messenger._click_any_selector(CONNECT_BUTTON_SELECTORS, timeout=5000)
        payload["clicked_connect"] = clicked
        await asyncio.sleep(3)
        await orch.browser.take_screenshot("probe_after_connect")
        payload["after"] = {
            "add_note": await visible_map(orch, ADD_NOTE_SELECTORS),
            "send_invite": await visible_map(orch, SEND_INVITE_SELECTORS),
            "pending": await visible_map(orch, PENDING_BUTTON_SELECTORS),
            "message": await visible_map(orch, MESSAGE_BUTTON_SELECTORS),
            "body_text": (await orch.browser.get_page_text())[:4000],
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(out_path)
    finally:
        try:
            await orch.stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
