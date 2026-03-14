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

STATE_JS = r"""
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const btns = Array.from(document.querySelectorAll('button, a, div[role="button"], [role="menuitem"]'))
    .filter(isVisible)
    .map(el => ({
      tag: el.tagName,
      role: el.getAttribute('role') || '',
      text: (el.textContent || '').trim(),
      aria: el.getAttribute('aria-label') || '',
      className: (el.className || '').toString(),
    }))
    .filter(x => {
      const b = (x.text + ' ' + x.aria).toLowerCase();
      return ['connect', 'pending', 'send', 'note', 'invitation', 'limit', 'unable', 'error', 'done', 'message'].some(k => b.includes(k));
    })
    .slice(0, 120);

  const dialogs = Array.from(document.querySelectorAll('[role="dialog"], .artdeco-modal, .artdeco-toast-item'))
    .filter(isVisible)
    .map(el => ({
      text: (el.textContent || '').trim().slice(0, 600),
      className: (el.className || '').toString(),
      role: el.getAttribute('role') || '',
    }));

  return { btns, dialogs };
})()
"""


async def main() -> None:
    orch = Orchestrator()
    out = {}
    try:
        await orch.start()
        if not await orch.auth.login():
            print('login failed')
            return

        await orch.browser.goto('https://www.linkedin.com/in/qazi-atif-3aab01324/')
        await asyncio.sleep(3)

        messenger = LinkedInReferralMessenger(orch.browser)
        await messenger._dismiss_popups()

        opened = await messenger._open_profile_more_menu()
        clicked = False
        if opened:
            clicked = await messenger._mouse_click_connect_in_open_menu()
            if not clicked:
                clicked = await messenger._click_connect_in_open_menu()

        await asyncio.sleep(2)
        out['opened_more'] = opened
        out['clicked_connect'] = clicked
        out['state'] = await messenger._evaluate_unwrapped(STATE_JS)
    finally:
        await orch.stop()

    p = PROJECT_ROOT / 'data' / 'probe_after_connect_click.json'
    p.write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(p)


if __name__ == '__main__':
    asyncio.run(main())
