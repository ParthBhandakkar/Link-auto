import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auto_apply.browser.engine import BrowserEngine


async def main() -> None:
    browser = BrowserEngine()
    page = await browser.start()
    await page.goto("https://example.com")
    print(await browser.get_current_url())
    print((await page.inner_text("body"))[:80])
    await browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
