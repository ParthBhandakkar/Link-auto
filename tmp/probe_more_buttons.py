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

JS = r"""
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const out = [];
  const nodes = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
  for (const el of nodes) {
    if (!isVisible(el)) continue;
    const text = (el.textContent || '').trim();
    const aria = (el.getAttribute('aria-label') || '').trim();
    const blob = (text + ' ' + aria).toLowerCase();
    if (!blob.includes('more')) continue;
    const r = el.getBoundingClientRect();
    out.push({
      tag: el.tagName,
      text,
      aria,
      className: (el.className || '').toString(),
      role: el.getAttribute('role') || '',
      x: r.x,
      y: r.y,
      w: r.width,
      h: r.height,
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
        data = await orch.browser.evaluate(JS)
    finally:
        await orch.stop()

    out = PROJECT_ROOT / "data" / "probe_more_buttons.json"
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
