from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import quote_plus

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
from linkedin.company_people import LinkedInCompanyPeople


async def main() -> None:
    company = sys.argv[1] if len(sys.argv) > 1 else "Netskope"
    browser = BrowserEngine()
    await browser.start()
    try:
        auth = LinkedInAuth(browser)
        logged_in = await auth.login()
        print(f"logged_in={logged_in}")
        url = f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(company)}&origin=GLOBAL_SEARCH_HEADER"
        await browser.goto(url)
        await asyncio.sleep(5)

        scraper = LinkedInCompanyPeople(browser)
        people = await scraper.search_company_people(company, limit=5)
        print("PEOPLE", json.dumps(people, indent=2, ensure_ascii=False))

        page = browser.page
        body_text = await page.locator("body").inner_text()
        print("BODY_START")
        print(body_text[:4000])
        print("BODY_END")

        selectors = [
            ".search-results-container",
            ".reusable-search",
            ".scaffold-layout__main",
            ".reusable-search__result-container",
            ".entity-result",
            ".search-results-container li",
            "a[href*='/in/']",
        ]
        counts = {}
        for selector in selectors:
            try:
                counts[selector] = await page.locator(selector).count()
            except Exception as exc:
                counts[selector] = f"ERR: {exc}"
        print("COUNTS", json.dumps(counts, indent=2, default=str))

        screenshot = await browser.take_screenshot("people_search_debug")
        print(f"screenshot={screenshot}")
    finally:
        await browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
