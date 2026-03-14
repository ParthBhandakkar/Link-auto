"""LinkedIn investor discovery for startup outreach."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from urllib.parse import quote_plus

from loguru import logger

from browser.engine import BrowserEngine
from models.schemas import InvestorLead
from utils.helpers import clean_text, human_delay

from Outreach.startup_profile import STARTUP_PROFILE


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
    "li.reusable-search__result-container",
    ".search-results-container li",
]

INVESTOR_KEYWORDS = {
    "investor",
    "venture capital",
    "vc",
    "angel",
    "partner",
    "principal",
    "associate",
    "fund",
    "capital",
    "ventures",
    "investment",
}

EXCLUDED_KEYWORDS = {
    "recruiter",
    "talent acquisition",
    "hiring",
    "sales",
    "account executive",
    "business development representative",
    "sdr",
    "hr",
}


class LinkedInInvestorSearch:
    """Search LinkedIn people results for startup-relevant investors and VCs."""

    def __init__(self, browser: BrowserEngine) -> None:
        self.browser = browser
        self.startup = STARTUP_PROFILE
        self._seen_urls: set[str] = set()

    async def search_investors(self, max_results: int = 60) -> list[InvestorLead]:
        """Run multiple LinkedIn searches and return deduplicated investor leads."""
        leads: list[InvestorLead] = []
        queries = list(self.startup.get("search_queries", []))

        for query in queries:
            if len(leads) >= max_results:
                break

            logger.info("Searching LinkedIn investors with query '{}'", query)
            query_leads = await self._search_query(
                query=query,
                remaining=max_results - len(leads),
            )
            leads.extend(query_leads)
            await human_delay(2, 4)

        leads.sort(key=lambda lead: lead.relevance_score, reverse=True)
        logger.info("Investor search complete — {} total leads", len(leads))
        return leads[:max_results]

    async def _search_query(self, query: str, remaining: int) -> list[InvestorLead]:
        page = self.browser.page
        url = PEOPLE_SEARCH_URL.format(keywords=quote_plus(query))
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
            logger.warning("Investor results did not load for query '{}'", query)
            return []

        leads: list[InvestorLead] = []
        for _ in range(6):
            page_leads = await self._extract_investors_from_links(
                query=query,
                remaining=remaining - len(leads),
            )
            for lead in page_leads:
                if lead.linkedin_profile_url in self._seen_urls:
                    continue
                self._seen_urls.add(lead.linkedin_profile_url)
                leads.append(lead)
                if len(leads) >= remaining:
                    break

            if len(leads) >= remaining:
                break

            await self.browser.scroll_down(1200)
            await asyncio.sleep(1.2)

        if not leads:
            logger.warning("No investor leads found on page for query '{}'", query)

        logger.info("Query '{}' produced {} investor leads", query, len(leads))
        return leads

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

    async def _extract_investors_from_links(self, query: str, remaining: int) -> list[InvestorLead]:
        page = self.browser.page
        leads: list[InvestorLead] = []

        try:
            links = page.locator("a[href*='/in/']")
            count = await links.count()
        except Exception:
            return []

        for idx in range(count):
            if len(leads) >= remaining:
                break

            try:
                link = links.nth(idx)
                profile_url = await link.get_attribute("href") or ""
                if not profile_url or "/in/" not in profile_url:
                    continue
                if profile_url.startswith("/"):
                    profile_url = f"https://www.linkedin.com{profile_url}"
                profile_url = profile_url.split("?")[0]
                if profile_url in self._seen_urls or any(
                    lead.linkedin_profile_url == profile_url for lead in leads
                ):
                    continue

                container = await self._find_result_container(link)
                if container is None:
                    continue

                lead = await self._parse_investor_card(container, query, profile_url=profile_url)
                if lead is None:
                    continue
                leads.append(lead)
            except Exception as e:
                logger.debug("Investor link parse failed at index {}: {}", idx, str(e)[:120])
                continue

        return leads

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

    async def _parse_investor_card(self, card, query: str, profile_url: str = "") -> InvestorLead | None:
        profile_url = profile_url or await self._extract_profile_url(card)
        if not profile_url:
            return None

        raw_text = await card.inner_text()
        lines = self._extract_lines(raw_text)
        if not lines:
            return None

        name = await self._extract_name(card, lines)
        if not name:
            return None

        headline = await self._extract_headline(card, lines, name)
        if not headline:
            headline = self._best_line_after_name(lines, name)

        full_text = clean_text(" ".join(lines))
        if not self._looks_like_investor(full_text, headline):
            return None

        location = await self._extract_location(card, lines, headline)
        firm_name = self._extract_firm_name(headline, full_text)
        investor_type = self._classify_investor_type(headline, full_text)
        score, why_fit, matched_sectors, matched_stages, geo_match = self._score_lead(
            headline=headline,
            query=query,
            full_text=full_text,
        )
        if score < 45:
            return None

        return InvestorLead(
            scraped_timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            startup_name=self.startup["startup_name"],
            investor_name=name,
            headline=headline,
            firm_name=firm_name,
            investor_type=investor_type,
            location=location,
            linkedin_profile_url=profile_url,
            search_query=query,
            sectors_matched=", ".join(matched_sectors),
            stages_matched=", ".join(matched_stages),
            geography_match=geo_match,
            relevance_score=score,
            why_fit=why_fit,
            source="LinkedIn Search",
        )

    async def _extract_profile_url(self, card) -> str:
        try:
            links = card.locator("a[href*='/in/']")
            count = await links.count()
            for idx in range(count):
                href = await links.nth(idx).get_attribute("href") or ""
                if "/in/" not in href:
                    continue
                if href.startswith("/"):
                    href = f"https://www.linkedin.com{href}"
                return href.split("?")[0]
        except Exception:
            return ""
        return ""

    async def _extract_name(self, card, lines: list[str]) -> str:
        selectors = [
            ".entity-result__title-text a span[aria-hidden='true']",
            ".entity-result__title-text a",
            "a[href*='/in/'] span[aria-hidden='true']",
            "a[href*='/in/']",
        ]
        for selector in selectors:
            try:
                el = card.locator(selector).first
                if await el.count() > 0:
                    text = clean_text(await el.inner_text())
                    text = re.sub(r"\b(1st|2nd|3rd)\b", "", text).strip(" •-")
                    if text and len(text) <= 100 and "connect" not in text.lower():
                        return text
            except Exception:
                continue

        for line in lines:
            cleaned = re.sub(r"\b(1st|2nd|3rd)\b", "", line).strip(" •-")
            if cleaned and len(cleaned.split()) <= 8 and "connect" not in cleaned.lower():
                return cleaned
        return ""

    async def _extract_headline(self, card, lines: list[str], name: str) -> str:
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
                    if text and text.lower() != name.lower():
                        return text
            except Exception:
                continue
        return self._best_line_after_name(lines, name)

    async def _extract_location(self, card, lines: list[str], headline: str) -> str:
        selectors = [
            ".entity-result__secondary-subtitle",
            ".t-12.t-black--light.t-normal",
            "[class*='entity-result__secondary-subtitle']",
        ]
        for selector in selectors:
            try:
                el = card.locator(selector).first
                if await el.count() > 0:
                    text = clean_text(await el.inner_text())
                    if text and text.lower() != headline.lower():
                        return text
            except Exception:
                continue

        for line in lines:
            low = line.lower()
            if line == headline:
                continue
            if any(token in low for token in ("india", "remote", "mumbai", "bengaluru", "bangalore", "delhi", "gurugram", "hyderabad", "pune", "singapore", "dubai", "london")):
                return line
        return ""

    def _extract_lines(self, raw_text: str) -> list[str]:
        return [clean_text(line) for line in raw_text.splitlines() if clean_text(line)]

    def _best_line_after_name(self, lines: list[str], name: str) -> str:
        lowered_name = name.lower()
        for line in lines:
            low = line.lower()
            if not line or low == lowered_name:
                continue
            if any(skip in low for skip in ("connect", "message", "follow", "mutual connection", "current:", "past:")):
                continue
            if len(line.split()) <= 2:
                continue
            return line
        return ""

    def _looks_like_investor(self, text: str, headline: str) -> bool:
        combined = f"{headline} {text}".lower()
        if any(bad in combined for bad in EXCLUDED_KEYWORDS):
            return False
        return any(keyword in combined for keyword in INVESTOR_KEYWORDS)

    def _extract_firm_name(self, headline: str, full_text: str) -> str:
        patterns = [
            r"\bat\s+([A-Z][A-Za-z0-9&.,'\- ]+)",
            r"\b@\s*([A-Z][A-Za-z0-9&.,'\- ]+)",
        ]
        for source in (headline, full_text):
            for pattern in patterns:
                match = re.search(pattern, source)
                if match:
                    return clean_text(match.group(1)).strip(" .,-")

        firm_markers = ["capital", "ventures", "fund", "vc", "partners"]
        for line in self._extract_lines(full_text):
            low = line.lower()
            if any(marker in low for marker in firm_markers) and len(line) <= 80:
                return line
        return ""

    def _classify_investor_type(self, headline: str, full_text: str) -> str:
        combined = f"{headline} {full_text}".lower()
        if "angel" in combined:
            return "Angel Investor"
        if "partner" in combined:
            return "VC Partner"
        if "principal" in combined:
            return "VC Principal"
        if "associate" in combined:
            return "VC Associate"
        if "founder" in combined and "fund" in combined:
            return "Fund Founder"
        if "venture capital" in combined or "vc" in combined:
            return "VC Investor"
        return "Investor"

    def _score_lead(self, headline: str, query: str, full_text: str) -> tuple[int, str, list[str], list[str], str]:
        score = 0
        reasons: list[str] = []

        combined = f"{headline} {query} {full_text}".lower()

        title_matches = [
            keyword for keyword in self.startup["investor_title_keywords"]
            if keyword in combined
        ]
        if title_matches:
            score += 30
            reasons.append("investor title present")

        sector_matches = [
            keyword for keyword in self.startup["sector_keywords"]
            if keyword.lower() in combined
        ]
        if sector_matches:
            score += min(25, 10 + 5 * len(sector_matches))
            reasons.append(f"sector match: {', '.join(sector_matches[:3])}")

        stage_matches = [
            keyword for keyword in self.startup["stage_keywords"]
            if keyword.lower() in combined
        ]
        if stage_matches:
            score += 15
            reasons.append("early-stage alignment")

        geo_matches = [
            keyword for keyword in self.startup["geo_keywords"]
            if keyword.lower() in combined
        ]
        geo_match = ", ".join(geo_matches[:3]) if geo_matches else ""
        if geo_matches:
            score += 15
            reasons.append("India / regional focus")

        if any(marker in combined for marker in ("capital", "ventures", "fund", "syndicate")):
            score += 10
            reasons.append("fund / VC firm context")

        if "payments" in combined and "mobility" in combined:
            score += 5
            reasons.append("payments + mobility overlap")

        return (
            min(score, 100),
            "; ".join(reasons),
            sector_matches,
            stage_matches,
            geo_match,
        )