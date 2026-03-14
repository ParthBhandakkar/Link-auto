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

PROFILE_URL = "https://www.linkedin.com/in/varsha-mishra-a3874b11b/"


async def main() -> None:
    orch = Orchestrator()
    out_path = Path("data") / f"debug_referral_connect_modal_{datetime.now():%Y%m%d_%H%M%S}.json"
    payload: dict = {"profile_url": PROFILE_URL}

    try:
        await orch.start()
        logged_in = await orch.auth.login()
        if not logged_in:
            payload["error"] = "login_failed"
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(out_path)
            return

        messenger = orch.referral_messenger
        await orch.browser.goto(PROFILE_URL)
        await asyncio.sleep(3)
        await messenger._dismiss_popups()
        await messenger._click_any_selector(
            ["button:has-text('Connect')", "button[aria-label*='Connect']"],
            timeout=4000,
        )
        await asyncio.sleep(3)

        buttons = await orch.browser.evaluate(
            """
() => Array.from(document.querySelectorAll('button, a[role="button"], div[role="button"]'))
  .filter(el => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  })
  .slice(0, 200)
  .map(el => ({
    tag: el.tagName,
    text: (el.innerText || el.textContent || '').trim(),
    ariaLabel: el.getAttribute('aria-label') || '',
    role: el.getAttribute('role') || '',
    className: el.className || ''
  }))
"""
        )

        dialogs = await orch.browser.evaluate(
            """
() => Array.from(document.querySelectorAll('[role="dialog"], .artdeco-modal'))
  .map(el => ({
    text: (el.innerText || el.textContent || '').trim().slice(0, 2000),
    className: el.className || ''
  }))
"""
        )

        payload["buttons"] = buttons
        payload["dialogs"] = dialogs
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(out_path)
    finally:
        try:
            await orch.stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
