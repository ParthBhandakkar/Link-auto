"""
LinkedIn Job Search — searches for jobs and collects listings.
Uses mouse-wheel scrolling inside the job-list sidebar to load more cards.
Guarantees at least MIN_JOBS_PER_KEYWORD unique jobs per search keyword.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from urllib.parse import quote_plus

from loguru import logger

from browser.engine import BrowserEngine
from config import settings
from models.schemas import Job, JobStatus
from utils.helpers import human_delay, clean_text, extract_salary
from profile import PROFILE


# Titles containing any of these words are skipped (case-insensitive)
INTERNSHIP_KEYWORDS = [
    "intern", "internship", "trainee", "apprentice", "co-op",
    "working student", "placement",
]

# LinkedIn job search URL template
# f_WT=2  → Remote
# f_JT=F  → Full-time only
# f_E=1,2,3,4 → Entry / Associate / Mid-Senior / Director (internships filtered by title)
# f_TPR=r604800 → Past week
SEARCH_URL = (
    "https://www.linkedin.com/jobs/search/?"
    "keywords={keywords}"
    "&location={location}"
    "&f_WT=2"
    "&f_JT=F"
    "&f_E=1%2C2%2C3%2C4"
    "&f_TPR=r604800"
    "&sortBy=DD"
    "&start={start}"
)

FALLBACK_SEARCH_URL = (
    "https://www.linkedin.com/jobs/search/?"
    "keywords={keywords}"
    "&location={location}"
    "&f_WT=2"
    "&sortBy=DD"
    "&start={start}"
)

SEARCH_BLOCKED_URL_MARKERS = (
    "/checkpoint",
    "/challenge",
    "/authwall",
    "/uas/login",
    "/login",
)

SEARCH_BLOCKED_TEXT_MARKERS = (
    "verify your identity",
    "security verification",
    "security check",
    "captcha",
    "hcaptcha",
    "detected unusual activity",
)


def job_id_from_jobs_view_href(href: str) -> str:
    """
    Numeric id from a LinkedIn job URL (LinkConnect patterns).
    Slug form: /jobs/view/title-at-company-1234567890 — plain: /jobs/view/1234567890
    """
    if not href:
        return ""
    m = re.search(r"/jobs/view/[^?/]+-(\d+)(?:/|\?|$)", href)
    if m:
        return m.group(1).strip()
    m = re.search(r"/jobs/view/(\d+)", href)
    return m.group(1).strip() if m else ""


# Selectors for the scrollable job-list sidebar (LinkedIn changes these often)
JOB_LIST_CONTAINER_SELECTORS = [
    ".scaffold-layout__list-container",
    ".jobs-search-results-list",
    ".scaffold-layout__list",
    "main.scaffold-layout__list",
    "[class*='jobs-search-results']",
    ".jobs-search-two-pane__wrapper",
    ".jobs-search__results-list",
]


class LinkedInSearch:
    """Searches LinkedIn for relevant job listings."""

    def __init__(self, browser: BrowserEngine) -> None:
        self.browser = browser
        self._seen_job_ids: set[str] = set()

    async def search_jobs(
        self,
        keyword: str,
        location: str = "Remote",
        max_pages: int = 5,
        min_jobs: int | None = None,
        fetch_details: bool = True,
    ) -> list[Job]:
        """
        Search LinkedIn for jobs matching the keyword.
        Scrolls the sidebar with mouse wheel to reveal all cards.
        Keeps paginating until at least `min_jobs` unique results are found.

        When *fetch_details* is True (default), each card is clicked in-page
        so the side-panel description is extracted before moving on.
        """
        if min_jobs is None:
            min_jobs = settings.min_jobs_per_keyword

        all_jobs: list[Job] = []

        for page_num in range(max_pages):
            start = page_num * 25
            logger.info("Searching: keyword='{}', page={}, have={}/{} jobs",
                        keyword, page_num + 1, len(all_jobs), min_jobs)

            loaded = await self._open_search_page(keyword, location, start, page_num)

            if not loaded:
                logger.warning("Job search results did not load for page {}", page_num + 1)
                await self.browser.take_screenshot(f"search_no_results_p{page_num}")
                break

            # LinkConnect-style priming: window nudge + scroll the list shell that contains job links (lazy load).
            await self._prime_job_list_for_lazy_load()

            # ── Scroll the job-list sidebar with mouse wheel ────────────
            jobs_before = 0
            cards_selector = await self._detect_card_selector()
            list_container = await self._detect_list_container()

            for scroll_round in range(10):  # up to 10 scroll rounds per page
                current_count = await self.browser.count_elements(cards_selector) if cards_selector else 0
                logger.debug("Scroll round {} — visible cards: {}", scroll_round + 1, current_count)

                if current_count > 0 and current_count == jobs_before and scroll_round > 2:
                    # No new cards after scrolling — all loaded
                    break
                jobs_before = current_count

                # Scroll inside the sidebar container (not the whole page)
                if list_container:
                    await self.browser.scroll_element(list_container, amount=600)
                else:
                    await self.browser.scroll_down(500)

                await asyncio.sleep(1.5)

            # ── Scroll back to top so we can click cards from the beginning ──
            if list_container:
                for _ in range(5):
                    await self.browser.scroll_element(list_container, amount=-800)
                    await asyncio.sleep(0.3)

            # ── Extract job cards ───────────────────────────────────────
            try:
                jobs = await self._extract_job_cards(keyword, search_location=location)
            except Exception as e:
                logger.error("Failed extracting jobs for page {}: {}",
                             page_num + 1, str(e)[:120])
                await self.browser.take_screenshot(f"search_extract_error_p{page_num}")
                break
            if not jobs:
                # Main-style extra settle + container waits, then extract again (timing / hydration).
                logger.info("No jobs after first extract — main-style wait + re-extract")
                await self._wait_job_list_main_style()
                try:
                    jobs = await self._extract_job_cards(keyword, search_location=location)
                except Exception as e2:
                    logger.debug("Re-extract failed: {}", str(e2)[:100])
            if not jobs:
                logger.info("No job cards found on page {}", page_num + 1)
                break

            # ── Fetch details inline (click each card on this page) ─────
            if fetch_details:
                for idx, job in enumerate(jobs):
                    try:
                        job = await self.get_job_details(job)
                        jobs[idx] = job
                    except Exception as e:
                        logger.debug("Detail fetch failed for {}: {}", job.job_id, str(e)[:80])
                    await asyncio.sleep(1)

            all_jobs.extend(jobs)
            logger.info("Found {} new jobs on page {} (total: {})", len(jobs), page_num + 1, len(all_jobs))

            # Stop early if we have enough
            if len(all_jobs) >= min_jobs:
                break

            await human_delay(3, 6)

        logger.info("Search complete for '{}': {} unique jobs found (target was {})",
                     keyword, len(all_jobs), min_jobs)
        return all_jobs

    async def _open_search_page(
        self,
        keyword: str,
        location: str,
        start: int,
        page_num: int,
    ) -> bool:
        """
        Open search page with retries and fallback query.
        Handles blocked pages by taking a checkpoint pause window.
        """
        encoded_keyword = quote_plus(keyword)
        encoded_location = quote_plus(location)
        attempts = [
            SEARCH_URL,
            FALLBACK_SEARCH_URL,
        ]

        for idx, template in enumerate(attempts):
            url = template.format(
                keywords=encoded_keyword,
                location=encoded_location,
                start=start,
            )
            max_tries = 2 if idx == 0 else 1
            for attempt in range(1, max_tries + 1):
                logger.debug(
                    "Loading search page attempt {} for '{}' (template {}, page {})",
                    attempt,
                    keyword,
                    idx + 1,
                    page_num + 1,
                )
                try:
                    # Jobs SPA often paints after domcontentloaded; optional "load" + settle time.
                    await self.browser.goto(
                        url,
                        wait_until="domcontentloaded",
                        force_open=True,
                    )
                    await self.browser.wait_for_load_state("load", timeout=45000)
                    await asyncio.sleep(4.5 if attempt == 1 else 6.0)
                except Exception as nav_err:
                    logger.warning("Search navigation failed (template {}, attempt {}): {}",
                                   idx + 1,
                                   attempt,
                                   str(nav_err)[:120])
                    if attempt < max_tries:
                        await self._recover_search_context()
                        await asyncio.sleep(4)
                        continue
                    break

                if await self._is_search_blocked():
                    logger.warning("Search page appears blocked on page {}", page_num + 1)
                    if not await self._handle_search_checkpoint(page_num):
                        return False
                    if attempt < max_tries:
                        await asyncio.sleep(3)
                        continue
                    break

                wait_timeout_ms = 28000 if idx == 0 and attempt > 1 else (24000 - (idx * 4000))
                if await self._wait_for_job_list(timeout_ms=max(wait_timeout_ms, 20000)):
                    return True
                # Main branch: after goto + sleep, wait up to 12s per list-container (visible).
                # Keeps parity with proven main behavior when strict row polling times out first.
                if await self._wait_job_list_main_style():
                    return True

                logger.warning("Job list did not render for page {} (template {}, attempt {})",
                               page_num + 1, idx + 1, attempt)
                # Network/rendering instability on search pages can be transient.
                # Reset navigation context and retry once before switching template.
                if attempt < max_tries:
                    await self._recover_search_context()
                    await asyncio.sleep(2.5)
                    continue

                if await self._is_search_blocked():
                    if not await self._handle_search_checkpoint(page_num):
                        return False
                await asyncio.sleep(3 + attempt)

            if idx == 0:
                logger.warning("Switching to fallback search URL for page {}", page_num + 1)

        return False

    async def _wait_job_list_main_style(self) -> bool:
        """
        Same readiness sequence as origin/main `search_jobs`: brief settle, then
        `wait_for_selector` (visible) for each list shell selector with 12s budget each.
        """
        await asyncio.sleep(3.0)
        for sel in JOB_LIST_CONTAINER_SELECTORS:
            try:
                await self.browser.wait_for_selector(sel, timeout=12000)
                return True
            except Exception:
                continue
        return False

    async def _prime_job_list_for_lazy_load(self) -> None:
        """
        Mirror scripts from LinkConnect `linkedin_job_scraper.search_jobs`: small window scroll,
        then find the scrollable ancestor of job view links and step-scroll to hydrate virtualized lists.
        """
        try:
            await self.browser.evaluate(
                "(() => { window.scrollTo(0, Math.floor(window.innerHeight / 2)); })()"
            )
            await asyncio.sleep(1.0)
            await self.browser.evaluate("(() => { window.scrollTo(0, 0); })()")
            await asyncio.sleep(1.0)
        except Exception:
            pass
        scroll_js = r"""(() => {
            const links = Array.from(document.querySelectorAll('a[href*="/jobs/view/"]'));
            if (links.length === 0) return 0;
            let best = null;
            let maxSh = 0;
            links.forEach(link => {
                let p = link.parentElement;
                let depth = 0;
                while (p && depth < 14) {
                    const sh = p.scrollHeight;
                    const ch = p.clientHeight;
                    if (sh > ch + 50 && sh > maxSh) {
                        maxSh = sh;
                        best = p;
                    }
                    p = p.parentElement;
                    depth++;
                }
            });
            if (!best) return 0;
            for (let i = 0; i < 7; i++) best.scrollTop += 650;
            return best.scrollTop;
        })()"""
        try:
            await self.browser.evaluate(scroll_js)
            await asyncio.sleep(2.0)
        except Exception as e:
            logger.debug("List priming scroll skipped: {}", str(e)[:100])

    async def _recover_search_context(self) -> None:
        """
        Reset navigation context after transient network/LinkedIn instability.
        Going back to feed first usually clears hung request state before reloading search.
        """
        try:
            await self.browser.goto("https://www.linkedin.com/feed/")
            await asyncio.sleep(1.2)
        except Exception as e:
            logger.debug("Search recovery pre-step to feed failed: {}", str(e)[:120])
        await asyncio.sleep(0.5)

    async def _wait_for_job_list(self, timeout_ms: int = 22000) -> bool:
        """
        Wait for job rows first (selectors that match real list items / view links only).

        Avoid unscoped `div.base-card` / `jobPosting` urn — they match elsewhere on the page,
        so we used to return \"ready\" then parse 0 jobs. After the strict phase, fall back to
        main-branch-style visible list containers for the remaining time budget.
        """
        job_row_selectors = [
            ".jobs-search-results-list a[href*='/jobs/view/']",
            ".scaffold-layout__list a[href*='/jobs/view/']",
            ".jobs-search-results__list-item",
            "li.ember-view.jobs-search-results__list-item",
            ".job-card-container",
            "li.job-card-container",
            "li.scaffold-layout__list-item",
            "[data-occludable-job-id]",
            "li[data-occludable-job-id]",
            "[data-test-id='job-card']",
            "[data-job-id]",
        ]
        no_results_markers = (
            "we couldn't find anything matching",
            "we couldn’t find anything matching",
            "couldn't find any jobs",
            "couldn’t find any jobs",
            "no jobs that match",
            "no results found for",
        )
        t0 = time.monotonic()
        deadline_sec = timeout_ms / 1000.0
        strict_until = t0 + deadline_sec * 0.65

        while time.monotonic() < strict_until:
            try:
                body = (await self.browser.get_page_text()).lower()
                if any(m in body for m in no_results_markers):
                    return True
            except Exception:
                pass

            for sel in job_row_selectors:
                try:
                    if await self.browser.count_elements(sel) > 0:
                        return True
                except Exception:
                    continue

            await asyncio.sleep(0.45)

        remaining_ms = int(max(0.0, (t0 + deadline_sec - time.monotonic())) * 1000)
        if remaining_ms < 1500:
            return False
        per_sel = max(2000, min(12000, remaining_ms // max(1, len(JOB_LIST_CONTAINER_SELECTORS))))
        for sel in JOB_LIST_CONTAINER_SELECTORS:
            try:
                await self.browser.wait_for_selector(sel, timeout=per_sel)
                return True
            except Exception:
                continue
        return False

    async def _is_search_blocked(self) -> bool:
        """Detect login wall/challenge/restriction screens."""
        current_url = await self.browser.get_current_url()
        if any(marker in current_url for marker in SEARCH_BLOCKED_URL_MARKERS):
            return True

        try:
            body_text = (await self.browser.get_page_text()).lower()
            return any(marker in body_text for marker in SEARCH_BLOCKED_TEXT_MARKERS)
        except Exception:
            return False

    async def _handle_search_checkpoint(self, page_num: int) -> bool:
        """
        Keep a short checkpoint window for manual LinkedIn verification.
        Returns True if page becomes usable again.
        """
        await self.browser.take_screenshot(f"search_checkpoint_p{page_num}")
        logger.warning("Possible login/security checkpoint on search page {}. "
                       "Solve it in the open browser if needed.",
                       page_num + 1)
        for _ in range(8):
            await asyncio.sleep(5)
            if not await self._is_search_blocked():
                return True
        return False

    async def _detect_card_selector(self) -> str:
        """Detect which job-card CSS selector is active on this page."""
        page = self.browser.page
        candidates = [
            ".jobs-search-results__list-item",
            "li.ember-view.jobs-search-results__list-item",
            ".job-card-container",
            ".job-search-card",
            ".job-card-container--clickable",
            "li.job-card-container",
            "[data-occludable-job-id]",
            "li[data-occludable-job-id]",
            "[data-job-id]",
            "[data-test-id='job-card']",
            ".scaffold-layout__list-item",
            ".jobs-search-results__list-item .job-card-container__link",
        ]
        for sel in candidates:
            count = await page.locator(sel).count()
            if count > 0:
                return sel
        return candidates[0]

    async def _detect_list_container(self) -> str:
        """Detect the scrollable parent container for the job list."""
        page = self.browser.page
        for sel in JOB_LIST_CONTAINER_SELECTORS:
            if await page.locator(sel).count() > 0:
                return sel
        return ""

    async def _extract_job_cards_dom_fallback(
        self, search_keyword: str, *, search_location: str = "Remote"
    ) -> list[Job]:
        """
        Collect jobs by scanning live DOM for /jobs/view/ links (same idea as DevTools console).
        Used when locator-based card parsing yields 0 jobs — agent-browser counts can diverge from
        attributes Playwright-style locators expect on LinkedIn's current markup.
        """
        # Must be an IIFE: bare `() => { ... }` is only a function value; eval does not call it → undefined.
        expr = r"""(() => {
            const seen = new Set();
            const out = [];
            const nodes = document.querySelectorAll('a[href*="/jobs/view/"], a[href*="jobs/view"]');
            for (const a of nodes) {
                const href = a.href || a.getAttribute('href') || '';
                let m = href.match(/\/jobs\/view\/[^?/]+-(\d+)(?:\/|\?|$)/);
                if (!m) m = href.match(/\/jobs\/view\/(\d+)/);
                if (!m) continue;
                const id = m[1];
                if (seen.has(id)) continue;
                seen.add(id);
                let title = '';
                const card = a.closest('li, [data-occludable-job-id], .job-card-container, article, .base-card');
                const scope = card || a;
                const strong = scope.querySelector('strong');
                if (strong && strong.innerText) title = strong.innerText.trim();
                if (!title) {
                    const t = scope.querySelector('.base-search-card__title, .job-card-list__title, h3');
                    if (t && t.innerText) title = t.innerText.trim();
                }
                if (!title) title = (a.innerText || '').trim().split(/\n/)[0].slice(0, 240);
                title = title.replace(/\s+/g, ' ').trim() || 'Unknown';
                let company = '';
                for (const s of [
                    '.job-card-container__primary-description',
                    '.artdeco-entity-lockup__subtitle',
                    "[class*='company-name']",
                    '.job-card-container__company-name',
                    "a[data-tracking-control-name*='company']",
                ]) {
                    const el = scope.querySelector(s);
                    if (el && el.innerText) {
                        company = el.innerText.replace(/\s+/g, ' ').trim();
                        if (company) break;
                    }
                }
                let location = '';
                for (const s of [
                    '.job-card-container__metadata-item',
                    '.artdeco-entity-lockup__caption',
                ]) {
                    const el = scope.querySelector(s);
                    if (el && el.innerText) {
                        location = el.innerText.replace(/\s+/g, ' ').trim();
                        if (location) break;
                    }
                }
                out.push({
                    job_id: id,
                    title,
                    company,
                    location,
                    url: 'https://www.linkedin.com/jobs/view/' + id + '/',
                });
                if (out.length >= 60) break;
            }
            return out;
        })()"""
        raw = await self.browser.evaluate(expr)
        if raw is None:
            logger.warning("DOM fallback: page.evaluate returned None")
            return []
        if isinstance(raw, dict):
            for key in ("result", "value", "data"):
                if isinstance(raw.get(key), list):
                    raw = raw[key]
                    break
            else:
                if raw and all(str(k).isdigit() for k in raw.keys()):
                    raw = [raw[k] for k in sorted(raw.keys(), key=lambda x: int(str(x)))]
        if not isinstance(raw, list) or not raw:
            logger.warning(
                "DOM fallback: evaluate returned no list (got type={} preview={!r})",
                type(raw).__name__,
                str(raw)[:200],
            )
            return []

        jobs: list[Job] = []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for row in raw:
            if not isinstance(row, dict):
                continue
            job_id = str(row.get("job_id", "")).strip()
            title = clean_text(str(row.get("title", "Unknown")))
            company = clean_text(str(row.get("company", "")))
            location = clean_text(str(row.get("location", "")))
            url = str(row.get("url", f"https://www.linkedin.com/jobs/view/{job_id}/"))
            if not job_id or job_id in self._seen_job_ids:
                continue
            if self._is_internship(title):
                continue
            self._seen_job_ids.add(job_id)
            jobs.append(
                Job(
                    job_id=job_id,
                    title=title,
                    company=company,
                    location=location,
                    url=url,
                    is_easy_apply=False,
                    is_remote=False,
                    search_keyword=search_keyword,
                    scraped_timestamp=now,
                    apply_method="External",
                    search_location=search_location,
                    keywords_matched=search_keyword,
                )
            )
        return jobs

    async def _extract_job_cards(self, search_keyword: str, *, search_location: str = "Remote") -> list[Job]:
        """Extract job information from the current search results page."""
        jobs: list[Job] = []
        page = self.browser.page

        cards_selector = await self._detect_card_selector()
        cards = page.locator(cards_selector)
        card_count = await cards.count()

        if card_count == 0:
            logger.warning("No job card elements found with '{}'", cards_selector)
            probe = [
                ".jobs-search-results__list-item",
                "[data-occludable-job-id]",
                ".jobs-search-results-list a[href*='/jobs/view/']",
            ]
            for ps in probe:
                try:
                    n = await page.locator(ps).count()
                    logger.info("Job list probe '{}': count={}", ps, n)
                except Exception:
                    pass
            return await self._extract_job_cards_dom_fallback(
                search_keyword, search_location=search_location
            )

        logger.debug("Extracting from {} cards (selector: '{}')", card_count, cards_selector)

        for i in range(card_count):
            try:
                card = cards.nth(i)
                job = await self._parse_job_card(card, search_keyword, search_location=search_location)
                if job and job.job_id not in self._seen_job_ids:
                    # Skip internships / non-full-time titles
                    if self._is_internship(job.title):
                        logger.debug("Skipping internship: '{}'", job.title)
                        continue
                    self._seen_job_ids.add(job.job_id)
                    jobs.append(job)
            except Exception as e:
                logger.debug("Error parsing card {}: {}", i, str(e)[:100])
                continue

        if not jobs:
            logger.warning(
                "Parsed 0 jobs from {} element(s) with selector '{}' — using DOM link fallback",
                card_count,
                cards_selector,
            )
            jobs = await self._extract_job_cards_dom_fallback(
                search_keyword, search_location=search_location
            )
            if jobs:
                logger.info("DOM fallback recovered {} job(s)", len(jobs))

        return jobs

    @staticmethod
    def _is_internship(title: str) -> bool:
        """Return True if the job title looks like an internship."""
        lower = title.lower()
        return any(kw in lower for kw in INTERNSHIP_KEYWORDS)

    async def _parse_job_card(self, card, search_keyword: str, *, search_location: str = "Remote") -> Job | None:
        """Parse a single job card element into a Job object."""
        try:
            # Extract job ID
            job_id = ""
            data_id = await card.get_attribute("data-occludable-job-id")
            if data_id:
                job_id = data_id.strip()
            if not job_id:
                inner = card.locator("[data-job-id]").first
                if await inner.count() > 0:
                    job_id = (await inner.get_attribute("data-job-id") or "").strip()
            if not job_id:
                n_view = await card.locator("a[href*='/jobs/view/']").count()
                for j in range(n_view):
                    href = await card.locator("a[href*='/jobs/view/']").nth(j).get_attribute("href") or ""
                    job_id = job_id_from_jobs_view_href(href)
                    if job_id:
                        break

            if not job_id:
                urn_el = card.locator("[data-entity-urn*='jobPosting']").first
                if await urn_el.count() > 0:
                    urn = await urn_el.get_attribute("data-entity-urn") or ""
                    match = re.search(r"jobPosting[:(](\d+)", urn)
                    if match:
                        job_id = match.group(1)

            if not job_id:
                return None

            # Title
            title = ""
            title_selectors = [
                ".job-card-list__title strong",
                ".job-card-list__title",
                ".artdeco-entity-lockup__title strong",
                ".artdeco-entity-lockup__title",
                "a.job-card-list__title--link strong",
                "a[class*='job-card-list__title'] strong",
                ".base-search-card__title",
                "h3.base-search-card__title",
                "a[data-control-name*='job_card_title']",
            ]
            for sel in title_selectors:
                el = card.locator(sel).first
                if await el.count() > 0:
                    title = clean_text(await el.inner_text())
                    if title:
                        break
            # Fallback: grab first <strong> and deduplicate
            if not title:
                el = card.locator("strong").first
                if await el.count() > 0:
                    title = clean_text(await el.inner_text())
            # Fix duplicate title text (LinkedIn renders it twice sometimes)
            if title:
                half = len(title) // 2
                if half > 3 and title[:half].strip() == title[half:].strip():
                    title = title[:half].strip()

            # Company
            company = ""
            company_selectors = [
                ".job-card-container__primary-description",
                ".artdeco-entity-lockup__subtitle",
                "[class*='company-name']",
                ".job-card-container__company-name",
                "a[data-tracking-control-name*='company']",
            ]
            for sel in company_selectors:
                el = card.locator(sel).first
                if await el.count() > 0:
                    company = clean_text(await el.inner_text())
                    if company:
                        break

            # Location
            location = ""
            location_selectors = [
                ".job-card-container__metadata-item",
                ".artdeco-entity-lockup__caption",
                "[class*='job-card'] .job-card-container__metadata-wrapper",
                ".job-card-container__metadata-wrapper li",
            ]
            for sel in location_selectors:
                el = card.locator(sel).first
                if await el.count() > 0:
                    location = clean_text(await el.inner_text())
                    if location:
                        break

            # Build URL
            url = f"https://www.linkedin.com/jobs/view/{job_id}/"

            # Check Easy Apply badge
            is_easy_apply = False
            easy_apply_el = card.locator("[class*='easy-apply'], .job-card-container__apply-method")
            if await easy_apply_el.count() > 0:
                text = await easy_apply_el.first.inner_text()
                is_easy_apply = "easy apply" in text.lower()

            # Check if remote
            is_remote = "remote" in location.lower() if location else False

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return Job(
                job_id=job_id,
                title=title,
                company=company,
                location=location,
                url=url,
                is_easy_apply=is_easy_apply,
                is_remote=is_remote,
                search_keyword=search_keyword,
                scraped_timestamp=now,
                apply_method="Easy Apply" if is_easy_apply else "External",
                search_location=search_location,
                keywords_matched=search_keyword,
            )

        except Exception as e:
            logger.debug("Error parsing job card: {}", str(e)[:100])
            return None

    async def get_job_details(self, job: Job) -> Job:
        """
        Click the job card in the search results to load its details in the
        right-side panel, then extract description, salary, experience, etc.
        Falls back to direct navigation if the card click approach fails.
        """
        if (job.company or "").strip():
            logger.info("Getting details for: {} at {}", job.title, job.company)
        else:
            logger.info("Getting details for: {}", job.title)

        page = self.browser.page

        # ── Capture current panel text so we can detect stale content ───
        prev_desc = ""
        try:
            for _sel in ("#job-details", ".jobs-description__content"):
                _el = page.locator(_sel).first
                if await _el.count() > 0:
                    prev_desc = (await _el.inner_text())[:120]
                    break
        except Exception:
            pass

        # ── Strategy 1: click the card on the search page ───────────────
        card_clicked = False
        panel_changed = False
        try:
            # Find the card with matching job ID
            card_selectors = [
                f"[data-occludable-job-id='{job.job_id}']",
                f"[data-job-id='{job.job_id}']",
                f".job-card-container a[href*='/jobs/view/{job.job_id}/']",
            ]
            for sel in card_selectors:
                locator = page.locator(sel).first
                if await locator.count() > 0:
                    # Scroll the card into view inside the sidebar first
                    await locator.scroll_into_view_if_needed()
                    await asyncio.sleep(0.5)
                    await self.browser.human_click(locator)
                    card_clicked = True
                    await asyncio.sleep(3)

                    # Verify the panel actually updated
                    new_desc = ""
                    for _sel2 in ("#job-details", ".jobs-description__content"):
                        _el2 = page.locator(_sel2).first
                        if await _el2.count() > 0:
                            new_desc = (await _el2.inner_text())[:120]
                            break
                    if new_desc and new_desc != prev_desc:
                        panel_changed = True
                        break

                    # Panel didn't change — try clicking the <a> link inside
                    link = page.locator(
                        f"a[href*='/jobs/view/{job.job_id}/']"
                    ).first
                    if await link.count() > 0:
                        await link.scroll_into_view_if_needed()
                        await asyncio.sleep(0.3)
                        await link.click()
                        await asyncio.sleep(3)

                        # Re-check
                        for _sel3 in ("#job-details", ".jobs-description__content"):
                            _el3 = page.locator(_sel3).first
                            if await _el3.count() > 0:
                                new_desc = (await _el3.inner_text())[:120]
                                break
                        if new_desc and new_desc != prev_desc:
                            panel_changed = True
                    break
        except Exception as e:
            logger.debug("Card click failed: {}", str(e)[:80])

        # ── Strategy 2: if card click didn't work, navigate directly ────
        if not card_clicked:
            try:
                await self.browser.goto(job.url, force_open=True)
                await asyncio.sleep(3)
            except Exception as nav_err:
                logger.warning("Navigation to job page failed: {} — returning partial data",
                               str(nav_err)[:120])
                return job

        if card_clicked and not panel_changed:
            logger.debug("Panel did not update for job {} — description may be stale", job.job_id)

        # ── Wait for the detail panel to render ─────────────────────────
        detail_loaded = False
        detail_wait_selectors = [
            "#job-details",
            ".jobs-description__content",
            ".jobs-box__html-content",
            ".jobs-description",
        ]
        for sel in detail_wait_selectors:
            try:
                await self.browser.wait_for_selector(sel, timeout=5000)
                detail_loaded = True
                break
            except Exception:
                continue

        if not detail_loaded:
            logger.debug("Job details panel did not load")

        # ── Try to click "Show more" to expand the description ──────────
        try:
            show_more_selectors = [
                "button[aria-label*='Show more']",
                "button[aria-label*='show more']",
                "button.jobs-description__footer-button",
                "[class*='show-more']",
            ]
            for sel in show_more_selectors:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await self.browser.human_click(btn)
                    await asyncio.sleep(1)
                    break
        except Exception:
            pass

        # ── Extract description ─────────────────────────────────────────
        desc_selectors = [
            "#job-details",
            ".jobs-description__content",
            ".jobs-box__html-content",
            ".jobs-description",
            ".jobs-description-content",
            ".job-details-module",
        ]
        for sel in desc_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    text = clean_text(await el.inner_text())
                    if text and len(text) > 30:
                        job.description = text
                        break
            except Exception:
                continue

        # Description preview (first 200 chars)
        if job.description:
            job.description_preview = job.description[:200].replace("\n", " ").strip()

        # Extract salary if present
        if not job.salary:
            job.salary = extract_salary(job.description)

        # Extract experience from description
        if job.description:
            exp_match = re.search(
                r"(\d+)\+?\s*(?:years?|yrs?)\s*(?:of)?\s*(?:experience|exp)",
                job.description, re.IGNORECASE,
            )
            if exp_match:
                job.experience_required = f"{exp_match.group(1)}+ years"

        # ── Company info from the top card ──────────────────────────────
        try:
            company_selectors = [
                ".job-details-jobs-unified-top-card__company-name",
                ".jobs-unified-top-card__company-name",
            ]
            for sel in company_selectors:
                el = page.locator(sel).first
                if await el.count() > 0:
                    company_text = clean_text(await el.inner_text())
                    if company_text and not job.company:
                        job.company = company_text
                    try:
                        href = await el.get_attribute("href")
                        if href:
                            job.company_link = href
                    except Exception:
                        pass
                    if not job.company_link:
                        try:
                            a_tag = el.locator("a").first
                            if await a_tag.count() > 0:
                                href = await a_tag.get_attribute("href")
                                if href:
                                    job.company_link = href
                        except Exception:
                            pass
                    break
        except Exception:
            pass

        if not job.company_link:
            try:
                fallback_selectors = [
                    "main a[href*='/company/']",
                    ".jobs-unified-top-card a[href*='/company/']",
                    ".job-details-jobs-unified-top-card__company-name a",
                ]
                for sel in fallback_selectors:
                    link = page.locator(sel).first
                    if await link.count() > 0:
                        href = await link.get_attribute("href")
                        if href:
                            job.company_link = href
                            break
            except Exception:
                pass

        # ── Company followers / industry ────────────────────────────────
        try:
            primary_desc = page.locator(
                ".job-details-jobs-unified-top-card__primary-description-container"
            ).first
            if await primary_desc.count() > 0:
                desc_text = clean_text(await primary_desc.inner_text())
                if "follower" in desc_text.lower():
                    job.company_followers = desc_text
        except Exception:
            pass

        # ── Company description (from the company card) ─────────────────
        try:
            comp_desc_el = page.locator(".jobs-company__company-description").first
            if await comp_desc_el.count() > 0:
                job.company_description = clean_text(await comp_desc_el.inner_text())
        except Exception:
            pass

        # ── Re-check Easy Apply button ──────────────────────────────────
        easy_apply_selectors = [
            "button.jobs-apply-button",
            "button[aria-label*='Easy Apply']",
            ".jobs-apply-button--top-card",
        ]
        for sel in easy_apply_selectors:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                text = await btn.inner_text()
                if "easy apply" in text.lower():
                    job.is_easy_apply = True
                    job.apply_method = "Easy Apply"
                    job.apply_link = job.url
                    break
                else:
                    job.apply_method = "External"
                    # Playwright-only API; AgentPage (agent-browser) has no expect_popup — same gap as main.
                    exp_popup = getattr(page, "expect_popup", None)
                    if exp_popup is None:
                        logger.debug("expect_popup unavailable; external apply URL not captured")
                        job.apply_link = job.url
                    else:
                        try:
                            async with exp_popup(timeout=10000) as popup_info:
                                await btn.click()
                            popup = await popup_info.value
                            wls = getattr(popup, "wait_for_load_state", None)
                            if callable(wls):
                                await wls("domcontentloaded", timeout=15000)
                            purl = getattr(popup, "url", "") or ""
                            if "linkedin.com" in purl and "externalApply" in purl:
                                wf = getattr(popup, "wait_for_url", None)
                                if callable(wf):
                                    try:
                                        await wf(lambda u: "externalApply" not in u, timeout=10000)
                                    except Exception:
                                        pass
                            job.apply_link = getattr(popup, "url", None) or job.url
                            logger.info("Captured external apply link: {}", job.apply_link)
                            closer = getattr(popup, "close", None)
                            if callable(closer):
                                await closer()
                        except Exception as e:
                            logger.warning("Failed to capture external link: {}", e)
                            job.apply_link = job.url
                    break

        logger.debug(
            "Job details: easy_apply={}, desc_len={}, salary={}, exp={}",
            job.is_easy_apply, len(job.description), job.salary, job.experience_required,
        )
        return job

    async def search_all_keywords(self, max_pages_per_keyword: int = 5) -> list[Job]:
        """
        Run searches for all configured job keywords.
        Ensures at least min_jobs_per_keyword unique jobs per keyword.
        """
        all_jobs: list[Job] = []
        keywords = PROFILE["job_search_keywords"]
        min_jobs = settings.min_jobs_per_keyword

        geo = str(
            PROFILE.get("job_search_location")
            or PROFILE.get("country")
            or "Remote"
        ).strip() or "Remote"
        logger.info("Job search location filter (LinkedIn URL): '{}'", geo)

        for keyword in keywords:
            logger.info("═══ Searching keyword: '{}' (min {} jobs) ═══", keyword, min_jobs)
            try:
                jobs = await self.search_jobs(
                    keyword=keyword,
                    location=geo,
                    max_pages=max_pages_per_keyword,
                    min_jobs=min_jobs,
                )
                if len(jobs) < min_jobs:
                    logger.warning(
                        "Only found {} jobs for '{}' (wanted {})",
                        len(jobs), keyword, min_jobs,
                    )
                all_jobs.extend(jobs)
            except Exception as e:
                logger.error("Search failed for keyword '{}': {} — continuing with next",
                             keyword, str(e)[:150])
            await human_delay(5, 10)

        # Deduplicate by job_id
        seen = set()
        unique: list[Job] = []
        for j in all_jobs:
            if j.job_id not in seen:
                seen.add(j.job_id)
                unique.append(j)

        logger.info("Total unique jobs across all keywords: {}", len(unique))
        return unique
