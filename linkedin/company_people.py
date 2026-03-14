"""LinkedIn company-people scraper.

Searches LinkedIn people results for a company name and extracts up to
five likely employees with their name, designation, and profile URL.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus

from loguru import logger

from browser.engine import BrowserEngine
from utils.helpers import clean_text


PEOPLE_SEARCH_URL = (
    "https://www.linkedin.com/search/results/people/?"
    "keywords={keywords}&origin=GLOBAL_SEARCH_HEADER"
)

RESULT_CONTAINER_SELECTORS = [
    ".search-results-container",
    ".reusable-search",
    ".scaffold-layout__main",
    "main",
]

RESULT_CARD_SELECTORS = [
    ".reusable-search__result-container",
    ".entity-result",
    ".search-results-container li",
]


class LinkedInCompanyPeople:
    """Scrape likely employee profiles for a company from LinkedIn search results."""

    def __init__(self, browser: BrowserEngine) -> None:
        self.browser = browser

    async def search_company_people(self, company: str, limit: int = 5) -> list[dict[str, str]]:
        page = self.browser.page
        url = PEOPLE_SEARCH_URL.format(keywords=quote_plus(company))
        logger.info("Searching LinkedIn people for company '{}'", company)

        await self.browser.goto(url)
        await asyncio.sleep(3)

        loaded = False
        for selector in RESULT_CONTAINER_SELECTORS:
            try:
                await self.browser.wait_for_selector(selector, timeout=10000)
                loaded = True
                break
            except Exception:
                continue

        if not loaded:
            try:
                await self.browser.wait_for_selector("a[href*='/in/']", timeout=10000)
                loaded = True
            except Exception:
                pass

        if not loaded:
            logger.warning("LinkedIn people results did not load for '{}'", company)
            return []

        people: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for _ in range(6):
            page_people = await self._extract_people_from_links(company, seen_urls, limit - len(people))
            if page_people:
                people.extend(page_people)
            if len(people) >= limit:
                break
            await self.browser.scroll_down(1200)
            await asyncio.sleep(1.2)

        logger.info("Found {} people for '{}'", len(people), company)
        return people[:limit]

    async def _extract_people_from_links(
        self,
        company: str,
        seen_urls: set[str],
        remaining: int,
    ) -> list[dict[str, str]]:
        page = self.browser.page
        people: list[dict[str, str]] = []

        try:
            links = page.locator("a[href*='/in/']")
            count = await links.count()
        except Exception:
            return []

        for idx in range(count):
            if len(people) >= remaining:
                break
            try:
                link = links.nth(idx)
                profile_url = await link.get_attribute("href") or ""
                if not profile_url or "/in/" not in profile_url:
                    continue
                if profile_url.startswith("/"):
                    profile_url = f"https://www.linkedin.com{profile_url}"
                profile_url = profile_url.split("?")[0]
                if profile_url in seen_urls:
                    continue

                container = await self._find_result_container(link)
                if container is None:
                    continue

                raw_text = await container.inner_text()
                card_text = clean_text(raw_text)
                if not self._text_matches_company(card_text, company):
                    continue

                name = await self._extract_name_from_link(link, raw_text)
                if not name:
                    continue
                designation = self._extract_designation_from_text(raw_text, name)
                people.append({
                    "name": name,
                    "designation": designation,
                    "profile_url": profile_url,
                })
                seen_urls.add(profile_url)
            except Exception:
                continue

        return people

    async def _locate_result_cards(self):
        page = self.browser.page
        for selector in RESULT_CARD_SELECTORS:
            try:
                cards = page.locator(selector)
                if await cards.count() > 0:
                    return cards
            except Exception:
                continue
        return None

    async def _find_result_container(self, link):
        candidates = [
            "xpath=ancestor::li[1]",
            "xpath=ancestor::div[@data-chameleon-result-urn][1]",
            "xpath=ancestor::div[contains(@class, 'linked-area')][1]",
            "xpath=ancestor::div[1]",
        ]
        for selector in candidates:
            try:
                container = link.locator(selector).first
                if await container.count() > 0:
                    return container
            except Exception:
                continue
        return None

    async def _parse_person_card(self, card, company: str) -> dict[str, str] | None:
        try:
            card_text = clean_text(await card.inner_text())
        except Exception:
            return None

        if not self._text_matches_company(card_text, company):
            return None

        profile_url = await self._extract_profile_url(card)
        if not profile_url:
            return None

        name = await self._extract_name(card)
        designation = await self._extract_designation(card)
        if not name:
            return None

        return {
            "name": name,
            "designation": designation,
            "profile_url": profile_url,
        }

    async def _extract_profile_url(self, card) -> str:
        try:
            links = card.locator("a[href*='/in/']")
            count = await links.count()
            for idx in range(count):
                href = await links.nth(idx).get_attribute("href")
                if not href or "/in/" not in href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.linkedin.com{href}"
                href = href.split("?")[0]
                return href
        except Exception:
            return ""
        return ""

    async def _extract_name(self, card) -> str:
        selectors = [
            ".entity-result__title-text a span[aria-hidden='true']",
            "a[href*='/in/'] span[aria-hidden='true']",
            ".entity-result__title-text a",
            "a[href*='/in/']",
        ]
        for selector in selectors:
            try:
                el = card.locator(selector).first
                if await el.count() > 0:
                    text = clean_text(await el.inner_text())
                    text = re.sub(r"\b(1st|2nd|3rd)\b", "", text).strip()
                    if text:
                        return text
            except Exception:
                continue
        return ""

    async def _extract_name_from_link(self, link, raw_text: str) -> str:
        try:
            direct = clean_text(await link.inner_text())
            direct = re.sub(r"\b(1st|2nd|3rd)\b", "", direct).strip()
            if direct and len(direct) <= 80 and "connect" not in direct.lower() and "current:" not in direct.lower():
                return direct
        except Exception:
            pass

        compact = clean_text(raw_text)
        match = re.match(r"^(.*?)\s*•\s*\d+(?:st|nd|rd)", compact)
        if match:
            candidate = clean_text(match.group(1)).strip(" •-")
            if candidate:
                return candidate

        for line in raw_text.splitlines():
            line = clean_text(line)
            if not line:
                continue
            line = re.sub(r"\b(1st|2nd|3rd)\b", "", line).strip(" •-")
            if line and "connect" not in line.lower() and len(line.split()) <= 8:
                return line
        return ""

    async def _extract_designation(self, card) -> str:
        selectors = [
            ".entity-result__primary-subtitle",
            ".t-14.t-black.t-normal",
            "[class*='entity-result__primary-subtitle']",
        ]
        for selector in selectors:
            try:
                el = card.locator(selector).first
                if await el.count() > 0:
                    text = clean_text(await el.inner_text())
                    if text:
                        return text
            except Exception:
                continue
        return ""

    def _extract_designation_from_text(self, raw_text: str, name: str) -> str:
        compact = clean_text(raw_text)
        compact = re.sub(rf"^{re.escape(name)}\s*", "", compact, count=1, flags=re.IGNORECASE)
        compact = re.sub(r"^•\s*\d+(?:st|nd|rd)\s*", "", compact)
        compact = re.sub(r"\b\d+(?:st|nd|rd)\b", "", compact).strip()
        compact = re.split(r"\b(?:Connect|Current:|Past:|Message|Follow|mutual connection|mutual connections)\b", compact, maxsplit=1)[0]
        compact = re.split(r"(?=[A-Z][a-z]+,\s*[A-Z])", compact, maxsplit=1)[0]
        compact = re.sub(
            r"\b(?:India|Remote|Bengaluru|Delhi|Hyderabad|Kochi|Lucknow|Rajasthan|Telangana|Karnataka|Maharashtra|Uttarakhand|Chhattisgarh)\b.*$",
            "",
            compact,
            flags=re.IGNORECASE,
        ).strip(" •-")
        if compact:
            return compact

        lines = [clean_text(line) for line in raw_text.splitlines() if clean_text(line)]
        lowered_name = name.lower()
        skip_fragments = (
            "connect",
            "message",
            "follow",
            "mutual connection",
            "current:",
            "past:",
            "location",
            "years",
        )
        for line in lines:
            low = line.lower()
            if lowered_name in low:
                continue
            if any(fragment in low for fragment in skip_fragments):
                continue
            if re.search(r"\b(india|remote|karnataka|maharashtra|telangana|uttarakhand|bengaluru|hyderabad|delhi)\b", low):
                continue
            return line
        return ""

    def _normalize(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    def _text_matches_company(self, text: str, company: str) -> bool:
        text_norm = self._normalize(text)
        company_norm = self._normalize(company)
        if not text_norm or not company_norm:
            return False
        if company_norm in text_norm:
            return True

        tokens = [
            token for token in company_norm.split()
            if token not in {"inc", "llc", "ltd", "limited", "pvt", "private", "solutions", "technologies", "technology", "india"}
            and len(token) >= 3
        ]
        if not tokens:
            tokens = [token for token in company_norm.split() if len(token) >= 3]
        if not tokens:
            return False

        overlap = sum(1 for token in tokens if token in text_norm)
        required = 1 if len(tokens) == 1 else 2
        return overlap >= required