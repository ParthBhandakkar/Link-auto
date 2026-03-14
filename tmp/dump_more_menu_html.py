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

MENU_HTML_JS = r"""
(() => {
  const out = [];
  const isVisible = (el) => {
    if (!el) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const menus = Array.from(document.querySelectorAll('.artdeco-dropdown__content, div[role="menu"]'));
  for (const menu of menus) {
    if (!isVisible(menu)) continue;
    const items = Array.from(menu.querySelectorAll('*')).filter(isVisible).slice(0, 150);
    out.push({
      menuClass: menu.className || '',
      menuTag: menu.tagName,
      html: menu.outerHTML,
      texts: items
        .map(el => ({
          tag: el.tagName,
          role: el.getAttribute('role') || '',
          className: (el.className || '').toString(),
          text: (el.textContent || '').trim(),
          aria: el.getAttribute('aria-label') || '',
          dataControlName: el.getAttribute('data-control-name') || '',
          dataViewName: el.getAttribute('data-view-name') || ''
        }))
        .filter(x => x.text || x.aria)
        .slice(0, 80)
    });
  }
  return out;
})()
"""


async def main() -> None:
    orch = Orchestrator()
    try:
        await orch.start()
        if not await orch.auth.login():
            print("login failed")
            return

        await orch.browser.goto("https://www.linkedin.com/in/qazi-atif-3aab01324/")
        await asyncio.sleep(3)

        messenger = LinkedInReferralMessenger(orch.browser)
        await messenger._dismiss_popups()
        await messenger._click_any_selector(["button[aria-label*='More actions']", "button:has-text('More')"], timeout=3500)
        await asyncio.sleep(1)

        menu_info = await orch.browser.evaluate(MENU_HTML_JS)
    finally:
        await orch.stop()

    out_path = PROJECT_ROOT / "data" / "qazi_more_menu_dump.json"
    out_path.write_text(json.dumps(menu_info, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    asyncio.run(main())
