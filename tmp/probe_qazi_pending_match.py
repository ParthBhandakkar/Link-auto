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

from orchestrator import Orchestrator
from Outreach.referral_messenger import LinkedInReferralMessenger


PENDING_PROBE_JS = r"""
(() => {
  const rows = [];
  const buttons = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
  const isVisible = (el) => {
    if (!el) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  for (const el of buttons) {
    const text = (el.innerText || el.textContent || '').trim();
    const aria = (el.getAttribute('aria-label') || '').trim();
    const blob = (text + ' ' + aria).toLowerCase();
    if (!blob.includes('pending')) continue;
    rows.push({
      tag: el.tagName,
      text,
      aria,
      className: (el.className || '').toString(),
      visible: isVisible(el),
      role: el.getAttribute('role') || '',
      dataControlName: el.getAttribute('data-control-name') || '',
      dataViewName: el.getAttribute('data-view-name') || '',
      rect: (() => {
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      })(),
    });
  }
  return rows;
})()
"""

DEGREE_PROBE_JS = r"""
(() => {
  const main = document.querySelector('main');
  if (!main) return {thirdDegreeHint:false, snippet:''};
  const text = (main.innerText || '').slice(0, 2000);
  return {
    thirdDegreeHint: /\b3rd\b/i.test(text),
    pendingHint: /\bpending\b/i.test(text),
    snippet: text,
  };
})()
"""


async def main() -> None:
    out = {
        "profile": "https://www.linkedin.com/in/qazi-atif-3aab01324/",
        "before": {},
        "after_more_click": {},
        "after_connect_click": {},
    }

    orch = Orchestrator()
    try:
        await orch.start()
        if not await orch.auth.login():
            print("login failed")
            return

        browser = orch.browser
        messenger = LinkedInReferralMessenger(browser)

        await browser.goto(out["profile"])
        await asyncio.sleep(3)
        await messenger._dismiss_popups()

        out["before"]["pending_matches"] = await browser.evaluate(PENDING_PROBE_JS)
        out["before"]["degree"] = await browser.evaluate(DEGREE_PROBE_JS)

        await messenger._click_any_selector(["button[aria-label*='More actions']", "button:has-text('More')"], timeout=3500)
        await asyncio.sleep(1)
        out["after_more_click"]["pending_matches"] = await browser.evaluate(PENDING_PROBE_JS)
        out["after_more_click"]["degree"] = await browser.evaluate(DEGREE_PROBE_JS)

        clicked = await messenger._click_connect_in_open_menu()
        out["after_connect_click"]["connect_clicked"] = clicked
        await asyncio.sleep(3)
        out["after_connect_click"]["pending_matches"] = await browser.evaluate(PENDING_PROBE_JS)
        out["after_connect_click"]["degree"] = await browser.evaluate(DEGREE_PROBE_JS)

    finally:
        await orch.stop()

    dump_path = PROJECT_ROOT / "data" / "probe_qazi_pending_match.json"
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    dump_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(str(dump_path))


if __name__ == "__main__":
    asyncio.run(main())
