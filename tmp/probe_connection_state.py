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

URL = "https://www.linkedin.com/in/aditya-pardeshi-01b26753/"

STATE_JS = r"""
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const main = document.querySelector('main');
  const mainText = (main?.innerText || '').slice(0, 3000);

  const header = document.querySelector('main .pv-top-card, main .pv-top-card-v2-ctas, main h1')?.closest('section, div, main') || main;
  const headerText = (header?.innerText || '').slice(0, 1500);

  const controls = Array.from(document.querySelectorAll('main button, main a, main div[role="button"]'))
    .filter(isVisible)
    .map(el => ({
      tag: el.tagName,
      text: (el.textContent || '').trim(),
      aria: el.getAttribute('aria-label') || '',
      role: el.getAttribute('role') || '',
      className: (el.className || '').toString(),
    }))
    .filter(x => {
      const b = (x.text + ' ' + x.aria).toLowerCase();
      return ['connect', 'pending', 'message', 'follow', 'more', 'remove connection', 'withdraw invitation', 'invitation'].some(k => b.includes(k));
    })
    .slice(0, 120);

  return {
    headerText,
    mainText,
    controls,
  };
})()
"""


async def main() -> None:
    orch = Orchestrator()
    out = {"url": URL}
    try:
        await orch.start()
        if not await orch.auth.login():
            print("login failed")
            return

        await orch.browser.goto(URL)
        await asyncio.sleep(3)

        messenger = LinkedInReferralMessenger(orch.browser)
        await messenger._dismiss_popups()

        out["is_third_degree_helper"] = await messenger._is_third_degree_profile()
        out["state"] = await messenger._evaluate_unwrapped(STATE_JS)

    finally:
        await orch.stop()

    path = PROJECT_ROOT / "data" / "probe_aditya_connection_state.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    asyncio.run(main())
