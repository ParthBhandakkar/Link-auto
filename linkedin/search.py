"""
LinkedIn Job Search — searches for jobs and collects listings.
Uses mouse-wheel scrolling inside the job-list sidebar to load more cards.
Guarantees at least MIN_JOBS_PER_KEYWORD unique jobs per search keyword.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from datetime import datetime
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import build_opener, HTTPRedirectHandler, Request
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

# Selectors for the scrollable job-list sidebar (LinkedIn changes these)
JOB_LIST_CONTAINER_SELECTORS = [
    ".jobs-search-results-list",
    ".scaffold-layout__list",
    "[class*='jobs-search-results']",
    ".jobs-search-two-pane__wrapper",
]


class LinkedInSearch:
    """Searches LinkedIn for relevant job listings."""

    def __init__(self, browser: BrowserEngine) -> None:
        self.browser = browser
        self._seen_job_ids: set[str] = set()

    @staticmethod
    def _extract_target_from_linkedin_redirect(url: str) -> str:
        """Extract the destination URL from common LinkedIn redirect links."""
        if not url:
            return ""

        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in ("url", "redirect", "redirectUrl", "destination", "dest"):
            values = query.get(key)
            if not values:
                continue
            candidate = unquote(values[0]).strip()
            if candidate.startswith("http://") or candidate.startswith("https://"):
                return candidate
        return ""

    @staticmethod
    def _resolve_redirect_url(url: str) -> str:
        """Follow HTTP redirects for tracking links and return the final URL."""
        if not url:
            return ""
        if url.startswith("http://") or url.startswith("https://"):
            try:
                opener = build_opener(HTTPRedirectHandler())
                req = Request(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/123.0 Safari/537.36"
                        )
                    },
                )
                with opener.open(req, timeout=15) as resp:
                    final_url = getattr(resp, "geturl", lambda: "")()
                    return str(final_url or "")
            except Exception:
                return ""
        return ""

    async def _capture_external_apply_link(self, job: Job, debug_reasons: list[str] | None = None) -> str:
        """Capture final external destination URL for non-Easy-Apply jobs."""
        page = self.browser.page

        apply_selectors = [
            "a.jobs-apply-button",
            "button.jobs-apply-button",
            "a[aria-label*='Apply']",
            "button[aria-label*='Apply']",
        ]

        original_url = ""
        with suppress(Exception):
            original_url = await self.browser.get_current_url()
        if not original_url:
            original_url = job.url

        for sel in apply_selectors:
            locator = page.locator(sel).first
            if await locator.count() == 0:
                continue

            if debug_reasons is not None:
                debug_reasons.append(f"selector_found={sel}")

            text = ""
            with suppress(Exception):
                text = (await locator.inner_text()).strip().lower()
            if "easy apply" in text:
                if debug_reasons is not None:
                    debug_reasons.append("easy_apply_button_detected")
                return ""

            href = ""
            with suppress(Exception):
                href = (await locator.get_attribute("href") or "").strip()

            dom_candidate = ""
            with suppress(Exception):
                dom_candidate = (
                    await locator.evaluate(
                        """
                        el => {
                            const attrs = Array.from(el.attributes || []).map(a => String(a.value || ''));
                            const parentA = el.closest('a');
                            if (parentA && parentA.href) attrs.push(parentA.href);
                            const html = el.outerHTML || '';
                            const all = attrs.concat([html]);
                            const joined = all.join(' ');
                            const m = joined.match(/https?:\\/\\/[^\\s\"'<>]+/i);
                            return m ? m[0] : '';
                        }
                        """
                    )
                    or ""
                ).strip()

            if not href and dom_candidate:
                href = dom_candidate

            parsed_from_href = self._extract_target_from_linkedin_redirect(href)
            if parsed_from_href:
                if debug_reasons is not None:
                    debug_reasons.append("resolved_from_linkedin_redirect_query")
                return parsed_from_href

            resolved_from_href = self._resolve_redirect_url(href)
            if resolved_from_href:
                if debug_reasons is not None:
                    debug_reasons.append("resolved_from_http_redirect")
                return resolved_from_href

            # Fallback: open same link in current tab and capture resulting URL.
            if href:
                try:
                    await self.browser.goto(href)
                    await asyncio.sleep(4)
                    final_url = (await self.browser.get_current_url()).strip()
                    if final_url and "linkedin.com/jobs" not in final_url:
                        if debug_reasons is not None:
                            debug_reasons.append("resolved_from_direct_navigation")
                        return final_url
                    parsed_final = self._extract_target_from_linkedin_redirect(final_url)
                    if parsed_final:
                        if debug_reasons is not None:
                            debug_reasons.append("resolved_from_post_navigation_redirect_query")
                        return parsed_final
                except Exception:
                    if debug_reasons is not None:
                        debug_reasons.append("direct_navigation_failed")
                    pass
                finally:
                    with suppress(Exception):
                        await self.browser.goto(original_url or job.url)
                        await asyncio.sleep(2)

            # Last fallback: click the button itself (handles JS-only apply handlers).
            try:
                await self.browser.human_click(locator)
                await asyncio.sleep(4)
                clicked_url = (await self.browser.get_current_url()).strip()
                if clicked_url and clicked_url != original_url:
                    if "linkedin.com/jobs" not in clicked_url:
                        if debug_reasons is not None:
                            debug_reasons.append("resolved_from_button_click_navigation")
                        return clicked_url
                    parsed_click = self._extract_target_from_linkedin_redirect(clicked_url)
                    if parsed_click:
                        if debug_reasons is not None:
                            debug_reasons.append("resolved_from_button_click_redirect_query")
                        return parsed_click
            except Exception:
                if debug_reasons is not None:
                    debug_reasons.append("button_click_fallback_failed")
                pass
            finally:
                with suppress(Exception):
                    if original_url:
                        await self.browser.goto(original_url)
                        await asyncio.sleep(2)

        if debug_reasons is not None and not debug_reasons:
            debug_reasons.append("no_apply_button_selector_found")
        return ""

    async def resolve_external_apply_link_for_job(self, job_url: str) -> str:
        """Open a LinkedIn job page and resolve its final external apply URL."""
        temp_job = Job(url=job_url)
        try:
            await self.browser.goto(job_url)
            await asyncio.sleep(3)
        except Exception as e:
            logger.warning("Could not open job URL for apply-link resolve: {}", str(e)[:120])
            return ""

        final_url = await self._capture_external_apply_link(temp_job)
        if final_url:
            logger.debug("Resolved external apply URL: {}", final_url[:160])
        return final_url

    async def resolve_external_apply_link_for_job_detailed(self, job_url: str) -> tuple[str, str]:
        """Resolve external apply URL and return diagnostic reason when unresolved."""
        temp_job = Job(url=job_url)
        reasons: list[str] = []
        try:
            await self.browser.goto(job_url)
            await asyncio.sleep(3)
        except Exception as e:
            reason = f"job_page_navigation_failed:{str(e)[:120]}"
            return "", reason

        final_url = await self._capture_external_apply_link(temp_job, debug_reasons=reasons)
        if final_url:
            logger.debug("Resolved external apply URL: {}", final_url[:160])
            return final_url, "resolved"

        compact_reason = " | ".join(reasons[-4:]) if reasons else "unresolved_no_diagnostics"
        return "", compact_reason

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
            url = SEARCH_URL.format(
                keywords=quote_plus(keyword),
                location=quote_plus(location),
                start=start,
            )
            logger.info("Searching: keyword='{}', page={}, have={}/{} jobs",
                        keyword, page_num + 1, len(all_jobs), min_jobs)

            try:
                await self.browser.goto(url)
            except Exception as nav_err:
                logger.warning("Navigation failed for page {}: {} — skipping page",
                               page_num + 1, str(nav_err)[:120])
                break
            await asyncio.sleep(3)

            # Wait for job cards to load
            loaded = False
            for sel in JOB_LIST_CONTAINER_SELECTORS:
                try:
                    await self.browser.wait_for_selector(sel, timeout=12000)
                    loaded = True
                    break
                except Exception:
                    continue

            if not loaded:
                logger.warning("Job search results did not load for page {}", page_num + 1)
                await self.browser.take_screenshot(f"search_no_results_p{page_num}")
                break

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
            jobs = await self._extract_job_cards(keyword)
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

    async def _detect_card_selector(self) -> str:
        """Detect which job-card CSS selector is active on this page."""
        page = self.browser.page
        candidates = [
            ".jobs-search-results__list-item",
            "li.ember-view.jobs-search-results__list-item",
            ".job-card-container",
            "[data-occludable-job-id]",
            "li[data-occludable-job-id]",
            ".scaffold-layout__list-item",
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

    async def _extract_job_cards(self, search_keyword: str) -> list[Job]:
        """Extract job information from the current search results page."""
        jobs: list[Job] = []
        page = self.browser.page

        cards_selector = await self._detect_card_selector()
        cards = page.locator(cards_selector)
        card_count = await cards.count()

        if card_count == 0:
            logger.warning("No job card elements found with '{}'", cards_selector)
            return []

        logger.debug("Extracting from {} cards (selector: '{}')", card_count, cards_selector)

        for i in range(card_count):
            try:
                card = cards.nth(i)
                job = await self._parse_job_card(card, search_keyword)
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

        return jobs

    @staticmethod
    def _is_internship(title: str) -> bool:
        """Return True if the job title looks like an internship."""
        lower = title.lower()
        return any(kw in lower for kw in INTERNSHIP_KEYWORDS)

    async def _parse_job_card(self, card, search_keyword: str) -> Job | None:
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
                link_el = card.locator("a[href*='/jobs/view/']").first
                if await link_el.count() > 0:
                    href = await link_el.get_attribute("href") or ""
                    match = re.search(r"/jobs/view/(\d+)", href)
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
                search_location="Remote",
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
        logger.info("Getting details for: {} at {}", job.title, job.company)

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
                await self.browser.goto(job.url)
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

        if _safe_apply_method := (job.apply_method or "").strip().lower():
            if _safe_apply_method == "external":
                try:
                    resolved_external_link = await self._capture_external_apply_link(job)
                    if resolved_external_link:
                        job.apply_link = resolved_external_link
                except Exception as e:
                    logger.debug("External apply-link resolve failed for {}: {}", job.job_id, str(e)[:100])

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

        for keyword in keywords:
            logger.info("═══ Searching keyword: '{}' (min {} jobs) ═══", keyword, min_jobs)
            try:
                jobs = await self.search_jobs(
                    keyword=keyword,
                    location="Remote",
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
