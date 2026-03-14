"""
LinkedIn Job Application — handles the full Easy Apply and external application flow.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime

from loguru import logger

from browser.engine import BrowserEngine
from linkedin.form_filler import FormFiller
from llm.client import llm_client
from llm.prompts import JOB_RELEVANCE_SYSTEM, JOB_RELEVANCE_USER, PAGE_STATE_SYSTEM, PAGE_STATE_USER
from models.schemas import (
    ApplicationResult,
    ApplicationStep,
    Job,
    JobStatus,
)
from profile import PROFILE
from utils.helpers import human_delay


class LinkedInApply:
    """
    Applies to LinkedIn jobs — both Easy Apply and external redirects.
    Uses the FormFiller for intelligent form filling and the LLM for
    error analysis and recovery.
    """

    def __init__(self, browser: BrowserEngine, form_filler: FormFiller) -> None:
        self.browser = browser
        self.form_filler = form_filler
        self._applied_ids: set[str] = set()

    # ── Main apply pipeline ─────────────────────────────────────────────
    async def apply_to_job(self, job: Job) -> ApplicationResult:
        """
        Full application pipeline for a single job.
        Returns an ApplicationResult with status, steps, errors, etc.

        Uses LinkedIn's ``currentJobId`` query-param so the browser opens
        the search-results page with the target job pre-selected in the
        right-side panel — this ensures the Easy Apply button renders.
        """
        start_time = time.time()
        result = ApplicationResult(job=job, status=JobStatus.APPLYING)

        # Skip if already applied
        if job.job_id in self._applied_ids:
            logger.info("Already applied to {} — skipping", job.job_id)
            result.status = JobStatus.SKIPPED
            return result

        try:
            # Step 1: Open the job in search context so the panel loads
            result.steps_completed.append(ApplicationStep.OPEN_JOB)
            opened = await self._open_job_in_search_context(job)
            if not opened:
                result.errors.append("Could not load job panel")
                result.status = JobStatus.FAILED
                return result

            # Step 2: Check if already applied
            if await self._is_already_applied():
                logger.info("Already applied to '{}' at {}", job.title, job.company)
                result.status = JobStatus.SKIPPED
                self._applied_ids.add(job.job_id)
                return result

            # Step 3: Check job relevance using LLM (if description available)
            if job.description:
                is_relevant = await self._check_relevance(job)
                if not is_relevant:
                    logger.info("Job not relevant: '{}' at {}", job.title, job.company)
                    result.status = JobStatus.SKIPPED
                    return result

            # Step 4: Determine application type and apply
            if await self._find_easy_apply_button():
                logger.info("Easy Apply button found for '{}'", job.title)
                result.steps_completed.append(ApplicationStep.CLICK_APPLY)
                success = await self._do_easy_apply(job, result)
            else:
                logger.info("No Easy Apply button — trying external apply for '{}'", job.title)
                result.steps_completed.append(ApplicationStep.EXTERNAL_APPLY)
                success = await self._do_external_apply(job, result)

            if success:
                result.status = JobStatus.APPLIED
                result.steps_completed.append(ApplicationStep.COMPLETE)
                self._applied_ids.add(job.job_id)
                job.applied_at = datetime.now()
                logger.info("Successfully applied to '{}' at {}", job.title, job.company)
            else:
                result.status = JobStatus.FAILED
                logger.warning("Application failed for '{}': {}", job.title,
                               "; ".join(result.errors) if result.errors else "unknown reason")

        except Exception as e:
            error_msg = str(e)[:300]
            logger.error("Application error for '{}': {}", job.title, error_msg)
            result.status = JobStatus.FAILED
            result.errors.append(error_msg)
            result.steps_completed.append(ApplicationStep.ERROR)

            # Take error screenshot
            try:
                ss = await self.browser.take_screenshot(f"error_{job.job_id}")
                result.screenshots.append(str(ss))
            except Exception:
                pass

        result.duration_seconds = time.time() - start_time
        return result

    async def _open_job_in_search_context(self, job: Job) -> bool:
        """
        Navigate to the search-results page with ``currentJobId`` so LinkedIn
        pre-selects the target job in the right-side panel.  Falls back to
        clicking the card if the param alone doesn't load the panel.
        """
        from urllib.parse import quote_plus

        page = self.browser.page
        kw = job.search_keyword or job.keywords_matched or job.title
        url = (
            "https://www.linkedin.com/jobs/search/?"
            f"keywords={quote_plus(kw)}"
            "&f_WT=2&f_JT=F&f_E=1%2C2%2C3%2C4&f_TPR=r604800&sortBy=DD"
            f"&currentJobId={job.job_id}"
        )
        logger.info("Opening job {} in search context", job.job_id)

        try:
            await self.browser.goto(url)
        except Exception as e:
            logger.warning("Navigation failed: {} — retrying once", str(e)[:100])
            try:
                await asyncio.sleep(3)
                await self.browser.goto(url)
            except Exception:
                return False

        await asyncio.sleep(4)

        # Wait for the detail panel to render
        detail_selectors = [
            "#job-details",
            ".jobs-description__content",
            ".jobs-box__html-content",
            ".jobs-unified-top-card",
        ]
        for sel in detail_selectors:
            try:
                await self.browser.wait_for_selector(sel, timeout=8000)
                logger.debug("Job panel loaded (selector: {})", sel)
                return True
            except Exception:
                continue

        # Fallback: try to click the card matching this job ID
        card_selectors = [
            f"[data-occludable-job-id='{job.job_id}']",
            f"[data-job-id='{job.job_id}']",
            f"a[href*='/jobs/view/{job.job_id}/']",
        ]
        for sel in card_selectors:
            locator = page.locator(sel).first
            if await locator.count() > 0:
                try:
                    await locator.scroll_into_view_if_needed()
                    await asyncio.sleep(0.3)
                    await self.browser.human_click(locator)
                    await asyncio.sleep(3)
                    # Check if panel loaded now
                    for dsel in detail_selectors[:2]:
                        try:
                            await self.browser.wait_for_selector(dsel, timeout=5000)
                            logger.debug("Job panel loaded after card click")
                            return True
                        except Exception:
                            continue
                except Exception:
                    continue

        logger.warning("Job panel did not load for job {}", job.job_id)
        return False
        return result

    # ── Easy Apply flow ─────────────────────────────────────────────────
    async def _do_easy_apply(self, job: Job, result: ApplicationResult) -> bool:
        """Handle the LinkedIn Easy Apply flow."""
        page = self.browser.page

        # Click the Easy Apply button
        clicked = await self._click_easy_apply()
        if not clicked:
            result.errors.append("Could not click Easy Apply button")
            return False

        await asyncio.sleep(2)

        # Handle the multi-step form
        max_pages = 12  # Safety limit
        retry_count = 0
        max_retries = 3
        consecutive_empty = 0  # Track pages with no fields
        last_page_html = ""  # Detect stuck on same page

        for page_num in range(max_pages):
            logger.info("Easy Apply -- processing page {}", page_num + 1)

            # Dismiss any blocking confirmation dialog layered on top of the form.
            await self._dismiss_blocking_dialogs()

            # LinkedIn sometimes shows a safety reminder interstitial before the
            # actual Easy Apply form. Advance past it first.
            if await self._handle_easy_apply_intro_dialog():
                await asyncio.sleep(2)
                continue

            # Check if modal is visible
            modal_visible = await self.browser.is_element_visible(
                ".jobs-easy-apply-modal, .artdeco-modal"
            )
            if not modal_visible:
                logger.info("Easy Apply modal disappeared -- verifying applied state")
                if await self._verify_applied_after_submit(job):
                    return True
                logger.warning("Modal gone but job is not marked applied")
                break

            # Detect stuck on same page (infinite loop guard)
            try:
                cur_html = await page.locator(".jobs-easy-apply-content, .artdeco-modal__content").first.inner_html()
                cur_html = cur_html[:500]
            except Exception:
                cur_html = ""
            if cur_html and cur_html == last_page_html:
                logger.warning("Stuck on same page -- attempting recovery (page {})", page_num + 1)
                if retry_count < max_retries:
                    fixed = await self.form_filler.analyze_and_fix_errors(job, {})
                    retry_count += 1
                    last_page_html = ""
                    if fixed:
                        await asyncio.sleep(1)
                        continue
                logger.warning("Stuck on same page -- aborting (page {})", page_num + 1)
                break
            last_page_html = cur_html

            # Fill the current form page
            result.steps_completed.append(ApplicationStep.FILL_FORM)
            fields_filled = await self.form_filler.fill_current_form(job)
            await asyncio.sleep(1)

            if not fields_filled and retry_count < max_retries:
                logger.warning(
                    "Form fill incomplete on page {} -- invoking LLM recovery (retry {})",
                    page_num + 1,
                    retry_count + 1,
                )
                fixed = await self.form_filler.analyze_and_fix_errors(job, {})
                retry_count += 1
                if fixed:
                    await asyncio.sleep(1)
                    continue
                result.errors.append(f"Could not fully fill form on page {page_num + 1}")

            # Always scroll to the modal footer after filling so action buttons
            # become visible even when they are below the fold.
            await self._scroll_easy_apply_modal_to_bottom()
            await asyncio.sleep(0.5)

            # Track consecutive empty pages
            form_fields = await self.form_filler._extract_form_fields()
            if not form_fields:
                consecutive_empty += 1
            else:
                consecutive_empty = 0

            if consecutive_empty >= 3:
                logger.warning("3 consecutive empty pages -- breaking out")
                break

            # Check for errors on the page
            has_errors = await self._check_form_errors()
            if has_errors and retry_count < max_retries:
                logger.warning("Form errors detected -- attempting fix (retry {})", retry_count + 1)
                fixed = await self.form_filler.analyze_and_fix_errors(job, {})
                retry_count += 1
                if fixed:
                    continue
                else:
                    result.errors.append(f"Could not fix form errors on page {page_num + 1}")

            # Look for next/review/submit button
            action = await self._get_form_action()
            try:
                footer_buttons = await self._get_visible_modal_button_labels()
                if footer_buttons:
                    logger.debug("Visible modal buttons: {}", footer_buttons)
            except Exception:
                pass
            logger.debug("Form action: {}", action)

            if action == "submit":
                submitted = await self._click_submit()
                if submitted:
                    result.steps_completed.append(ApplicationStep.SUBMIT)
                    await asyncio.sleep(3)

                    # Diagnostic screenshot
                    try:
                        ss = await self.browser.take_screenshot(f"post_submit_{job.job_id}")
                        result.screenshots.append(str(ss))
                    except Exception:
                        pass

                    # Check for success
                    if await self._check_application_success():
                        await self._dismiss_success_modal()
                        return True
                    if await self._verify_applied_after_submit(job):
                        return True
                    else:
                        # Check for validation errors after submit
                        has_errors = await self._check_form_errors()
                        if has_errors:
                            logger.warning("Post-submit validation errors detected")
                            if retry_count < max_retries:
                                retry_count += 1
                                continue
                        result.errors.append("Submit did not result in success confirmation")
                        return False
                else:
                    result.errors.append("Could not click submit button")
                    return False

            elif action == "next":
                clicked_next = await self._click_next()
                if not clicked_next:
                    logger.warning("Could not click Next button on page {}", page_num + 1)
                    result.errors.append(f"Could not click Next button on page {page_num + 1}")
                    if retry_count < max_retries:
                        retry_count += 1
                        await self._dismiss_blocking_dialogs()
                        await self._scroll_easy_apply_modal_to_bottom()
                        await asyncio.sleep(1)
                        continue
                    return False
                await asyncio.sleep(2)
                last_page_html = ""
                retry_count = 0

            elif action == "review":
                clicked_review = await self._click_review()
                if not clicked_review:
                    logger.warning("Could not click Review button on page {}", page_num + 1)
                    result.errors.append(f"Could not click Review button on page {page_num + 1}")
                    if retry_count < max_retries:
                        retry_count += 1
                        await self._dismiss_blocking_dialogs()
                        await self._scroll_easy_apply_modal_to_bottom()
                        await asyncio.sleep(1)
                        continue
                    return False
                await asyncio.sleep(2)
                last_page_html = ""

            else:
                logger.warning("Unknown form action state on page {}", page_num + 1)
                screenshot = await self.browser.take_screenshot(f"unknown_state_{page_num}")
                result.screenshots.append(str(screenshot))
                found = await self.browser.safe_click(
                    "button[aria-label*='Continue'], "
                    "button[aria-label*='Next'], "
                    "button[aria-label*='Review'], "
                    "button[aria-label*='Submit']"
                )
                if not found:
                    result.errors.append("Could not determine next action")
                    break
                await asyncio.sleep(2)

        # If we exit the loop, close the modal if open
        await self._close_easy_apply_modal()
        return False

    # ── External apply flow ─────────────────────────────────────────────
    async def _do_external_apply(self, job: Job, result: ApplicationResult) -> bool:
        """Handle external application links."""
        page = self.browser.page

        # Find the Apply button (not Easy Apply)
        apply_selectors = [
            "button.jobs-apply-button",
            "a.jobs-apply-button",
            "button[aria-label*='Apply']",
            "a[aria-label*='Apply']",
        ]

        clicked = False
        for sel in apply_selectors:
            locator = page.locator(sel).first
            if await locator.count() > 0 and await locator.is_visible():
                text = await locator.inner_text()
                if "easy" not in text.lower():
                    await self.browser.human_click(locator)
                    clicked = True
                    break

        if not clicked:
            result.errors.append("Could not find external apply button")
            result.status = JobStatus.EXTERNAL
            return False

        await asyncio.sleep(3)

        # Check if a new tab opened
        pages = self.browser.context.pages
        if len(pages) > 1:
            # Switch to the new tab
            new_page = pages[-1]
            logger.info("External application opened in new tab: {}", new_page.url[:80])

            # Take screenshot for reference
            screenshot = await self.browser.take_screenshot("external_apply")
            result.screenshots.append(str(screenshot))

            # Attempt to fill the external form using LLM + page analysis
            try:
                external_filled = await self._try_fill_external_form(new_page, job, result)
            except Exception as e:
                logger.warning("External form fill error: {}", str(e)[:100])
                external_filled = False

            # Close the external tab and go back
            await new_page.close()

            if external_filled:
                result.status = JobStatus.APPLIED
                return True
            else:
                result.status = JobStatus.EXTERNAL
                result.errors.append("External application opened — could not auto-fill completely")
                return False
        else:
            result.status = JobStatus.EXTERNAL
            return False

    async def _try_fill_external_form(self, ext_page, job: Job, result: ApplicationResult) -> bool:
        """Attempt to analyze and fill an external application form."""
        # Wait for the page to load
        await asyncio.sleep(5)

        # Take a screenshot of the external page
        screenshot_path = await self.browser.take_screenshot("external_form")

        # Use LLM to analyze the page
        try:
            analysis = await llm_client.chat_json_with_image(
                system_prompt=PAGE_STATE_SYSTEM,
                user_message=PAGE_STATE_USER.format(
                    action_context=f"apply to {job.title} at {job.company} on their external careers page"
                ),
                image_path=screenshot_path,
            )
            logger.info("External page analysis: {}", analysis)

            if analysis.get("page_type") == "error":
                return False

            # For external sites, we'll note it and move on
            # Full external form filling is complex and site-specific
            result.errors.append(f"External site: {ext_page.url[:80]}")
            return False

        except Exception as e:
            logger.warning("External form analysis failed: {}", str(e)[:100])
            return False

    # ── Button detection and clicking ───────────────────────────────────
    async def _find_easy_apply_button(self) -> bool:
        """Check if the Easy Apply button is present."""
        page = self.browser.page
        selectors = [
            "button.jobs-apply-button:has-text('Easy Apply')",
            "button[aria-label*='Easy Apply']",
            ".jobs-apply-button--top-card:has-text('Easy Apply')",
        ]
        for sel in selectors:
            locator = page.locator(sel).first
            if await locator.count() > 0:
                try:
                    if await locator.is_visible():
                        return True
                except Exception:
                    continue
        return False

    async def _click_easy_apply(self) -> bool:
        """Click the Easy Apply button."""
        page = self.browser.page
        selectors = [
            "button.jobs-apply-button:has-text('Easy Apply')",
            "button[aria-label*='Easy Apply']",
            ".jobs-apply-button--top-card:has-text('Easy Apply')",
        ]
        for sel in selectors:
            locator = page.locator(sel).first
            try:
                if await locator.count() > 0 and await locator.is_visible():
                    await self.browser.human_click(locator)
                    logger.info("Clicked Easy Apply button")
                    return True
            except Exception:
                continue

        logger.warning("Could not find Easy Apply button")
        return False

    async def _get_form_action(self) -> str:
        """Determine the next action on the Easy Apply form: next, review, or submit."""
        page = self.browser.page

        if await self._is_safety_reminder_modal():
            return "next"

        footer_labels = await self._get_visible_modal_button_labels()
        footer_lower = [label.lower() for label in footer_labels]
        if any("submit" in label or "send application" in label for label in footer_lower):
            return "submit"
        if any("review" in label for label in footer_lower):
            return "review"
        if any(
            "continue applying" in label or "continue" in label or label == "next" or "next step" in label
            for label in footer_lower
        ):
            return "next"

        async def detect_action() -> str:
            # Check for Submit button
            submit_selectors = [
                "button[aria-label*='Submit application']",
                "button:has-text('Submit application')",
                "button:has-text('Submit')",
                "button:has-text('Send application')",
                "footer button[aria-label*='Submit']",
            ]
            for sel in submit_selectors:
                try:
                    locator = page.locator(sel).first
                    if await locator.count() > 0 and await locator.is_visible():
                        return "submit"
                except Exception:
                    continue

            # Check for Review button
            review_selectors = [
                "button[aria-label*='Review']",
                "button:has-text('Review')",
                "footer button:has-text('Review')",
            ]
            for sel in review_selectors:
                try:
                    locator = page.locator(sel).first
                    if await locator.count() > 0 and await locator.is_visible():
                        return "review"
                except Exception:
                    continue

            # Check for Next button
            next_selectors = [
                "button[aria-label*='Continue applying']",
                "button:has-text('Continue applying')",
                "button[aria-label*='Continue']",
                "button[aria-label*='Next']",
                "button:has-text('Next')",
                "footer button:has-text('Next')",
            ]
            for sel in next_selectors:
                try:
                    locator = page.locator(sel).first
                    if await locator.count() > 0 and await locator.is_visible():
                        return "next"
                except Exception:
                    continue

            return "unknown"

        await self._dismiss_blocking_dialogs()
        action = await detect_action()
        if action != "unknown":
            return action

        # If action buttons are below the fold, scroll the modal and retry.
        await self._scroll_easy_apply_modal_to_bottom()
        await asyncio.sleep(0.5)
        await self._dismiss_blocking_dialogs()
        return await detect_action()

    async def _click_next(self) -> bool:
        """Click the Next button in Easy Apply form."""
        if await self._click_modal_action(["Continue", "Next", "Next step"]):
            logger.debug("Clicked Next button via modal action")
            return True

        if await self._is_safety_reminder_modal():
            if await self.browser.safe_click("button:has-text('Continue applying')"):
                logger.debug("Clicked Continue applying on safety reminder")
                return True

        selectors = [
            "button[aria-label*='Continue applying']",
            "button:has-text('Continue applying')",
            "button[aria-label*='Continue']",
            "button[aria-label*='Next']",
            "button:has-text('Next')",
            "footer button:has-text('Next')",
        ]
        await self._dismiss_blocking_dialogs()
        for sel in selectors:
            if await self.browser.safe_click(sel):
                logger.debug("Clicked Next button")
                return True
        await self._scroll_easy_apply_modal_to_bottom()
        await self._dismiss_blocking_dialogs()
        for sel in selectors:
            if await self.browser.safe_click(sel):
                logger.debug("Clicked Next button after scrolling")
                return True
        return False

    async def _click_review(self) -> bool:
        """Click the Review button."""
        if await self._click_modal_action(["Review"]):
            logger.debug("Clicked Review button via modal action")
            return True

        selectors = [
            "button[aria-label*='Review']",
            "button:has-text('Review')",
            "footer button:has-text('Review')",
        ]
        await self._dismiss_blocking_dialogs()
        for sel in selectors:
            if await self.browser.safe_click(sel):
                logger.debug("Clicked Review button")
                return True
        await self._scroll_easy_apply_modal_to_bottom()
        await self._dismiss_blocking_dialogs()
        for sel in selectors:
            if await self.browser.safe_click(sel):
                logger.debug("Clicked Review button after scrolling")
                return True
        return False

    async def _click_submit(self) -> bool:
        """Click the Submit button."""
        if await self._click_modal_action(["Submit application", "Send application", "Submit"]):
            logger.info("Clicked Submit button via modal action")
            return True

        selectors = [
            "button[aria-label*='Submit application']",
            "button:has-text('Submit application')",
            "button:has-text('Submit')",
            "button:has-text('Send application')",
            "footer button[aria-label*='Submit']",
        ]
        await self._dismiss_blocking_dialogs()
        for sel in selectors:
            if await self.browser.safe_click(sel):
                logger.info("Clicked Submit button")
                return True
        await self._scroll_easy_apply_modal_to_bottom()
        await self._dismiss_blocking_dialogs()
        for sel in selectors:
            if await self.browser.safe_click(sel):
                logger.info("Clicked Submit button after scrolling")
                return True
        return False

    async def _scroll_easy_apply_modal_to_bottom(self) -> None:
        """Scroll the active Easy Apply modal so footer buttons become visible."""
        page = self.browser.page
        selectors = [
            ".jobs-easy-apply-modal",
            ".jobs-easy-apply-modal .jobs-easy-apply-content",
            ".jobs-easy-apply-modal .artdeco-modal__content",
            ".jobs-easy-apply-content",
            ".artdeco-modal__content",
        ]
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.scroll_into_view_if_needed()

                    # Try direct JS scrolling on the element.
                    await locator.evaluate(
                        """
                        el => {
                            el.scrollTop = el.scrollHeight;
                            if (el.parentElement) el.parentElement.scrollTop = el.parentElement.scrollHeight;
                            let p = el.parentElement;
                            let depth = 0;
                            while (p && depth < 4) {
                                if (p.scrollHeight > p.clientHeight) {
                                    p.scrollTop = p.scrollHeight;
                                }
                                p = p.parentElement;
                                depth += 1;
                            }
                        }
                        """
                    )

                    # Then wheel-scroll inside the modal a few times.
                    box = await locator.bounding_box()
                    if box:
                        cx = box["x"] + box["width"] / 2
                        cy = box["y"] + min(box["height"] * 0.75, box["height"] - 10)
                        await page.mouse.move(cx, cy)
                        for _ in range(4):
                            await page.mouse.wheel(0, 900)
                            await asyncio.sleep(0.15)

                    # Finally, focus the modal and press End/PageDown.
                    try:
                        await self.browser.human_click(locator)
                        await asyncio.sleep(0.1)
                        await page.keyboard.press("End")
                        await asyncio.sleep(0.1)
                        await page.keyboard.press("PageDown")
                    except Exception:
                        pass

                    logger.debug("Scrolled modal content to bottom via {}", sel)
                    return
            except Exception:
                continue

    async def _dismiss_blocking_dialogs(self) -> bool:
        """Dismiss transient dialogs like 'Save this application?' without closing the main form."""
        page = self.browser.page
        dialog_selectors = [
            ".artdeco-modal:has-text('Save this application')",
            ".artdeco-modal:has-text('Save this application?')",
        ]
        for dialog_sel in dialog_selectors:
            try:
                dialog = page.locator(dialog_sel).first
                if await dialog.count() > 0 and await dialog.is_visible():
                    for close_sel in [
                        "button[aria-label='Dismiss']",
                        ".artdeco-modal__dismiss",
                        "button[aria-label='Close']",
                    ]:
                        close_btn = dialog.locator(close_sel).first
                        if await close_btn.count() > 0 and await close_btn.is_visible():
                            await self.browser.human_click(close_btn)
                            logger.info("Dismissed blocking save-application dialog")
                            await asyncio.sleep(0.5)
                            return True
            except Exception:
                continue
        return False

    async def _handle_easy_apply_intro_dialog(self) -> bool:
        """Advance through LinkedIn's Easy Apply intro/safety reminder modal."""
        if not await self._is_safety_reminder_modal():
            return False

        for sel in [
            "button[aria-label*='Continue applying']",
            "button:has-text('Continue applying')",
            "button:has-text('Continue')",
        ]:
            if await self.browser.safe_click(sel, timeout=3000):
                logger.info("Dismissed Easy Apply safety reminder")
                return True
        return False

    async def _is_safety_reminder_modal(self) -> bool:
        """Detect the pre-application job safety reminder dialog."""
        page = self.browser.page
        try:
            modal_text = await page.locator(".jobs-easy-apply-modal, .artdeco-modal").first.inner_text()
        except Exception:
            return False
        text = modal_text.lower()
        return "job search safety reminder" in text and "continue applying" in text

    async def _get_visible_modal_button_labels(self) -> list[str]:
        """Return visible button labels from the active Easy Apply modal."""
        page = self.browser.page
        try:
            labels = await page.locator("body").evaluate(
                """
                el => {
                    const modal = document.querySelector('.jobs-easy-apply-modal, .artdeco-modal');
                    if (!modal) return [];
                    const candidates = Array.from(modal.querySelectorAll(
                        'footer button, .artdeco-modal__actionbar button, button'
                    ));
                    const visible = candidates.filter(btn => {
                        const style = window.getComputedStyle(btn);
                        const rect = btn.getBoundingClientRect();
                        return !btn.disabled && style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                    });
                    return visible
                        .map(btn => (btn.innerText || btn.getAttribute('aria-label') || '').trim())
                        .filter(Boolean)
                        .slice(0, 12);
                }
                """
            )
            return labels if isinstance(labels, list) else []
        except Exception:
            return []

    async def _click_modal_action(self, labels: list[str]) -> bool:
        """Scroll the Easy Apply modal until a target action button can be clicked."""
        page = self.browser.page
        wanted_json = json.dumps([label.lower() for label in labels])
        try:
            clicked = await page.locator("body").evaluate(
                f"""
                el => {{
                    const wanted = {wanted_json};
                    const modal = document.querySelector('.jobs-easy-apply-modal, .artdeco-modal');
                    if (!modal) return false;

                    const getButtons = () => Array.from(modal.querySelectorAll(
                        'footer button, .artdeco-modal__actionbar button, button'
                    ));
                    const isVisible = btn => {{
                        const style = window.getComputedStyle(btn);
                        const rect = btn.getBoundingClientRect();
                        return !btn.disabled && style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                    }};
                    const textOf = btn => ((btn.innerText || btn.getAttribute('aria-label') || '').trim()).toLowerCase();
                    const isTarget = btn => {{
                        const text = textOf(btn);
                        return text && wanted.some(label => text === label || text.includes(label));
                    }};
                    const scrollContainers = [
                        modal.querySelector('.jobs-easy-apply-content'),
                        modal.querySelector('.artdeco-modal__content'),
                        modal,
                    ].filter(Boolean);

                    for (let attempt = 0; attempt < 8; attempt++) {{
                        const target = getButtons().find(isTarget);
                        if (target) {{
                            target.scrollIntoView({{ block: 'center', inline: 'nearest' }});
                            let parent = target.parentElement;
                            let depth = 0;
                            while (parent && depth < 6) {{
                                if (parent.scrollHeight > parent.clientHeight) {{
                                    const desiredTop = Math.max(0, target.offsetTop - 220);
                                    parent.scrollTop = Math.min(parent.scrollHeight, desiredTop);
                                }}
                                parent = parent.parentElement;
                                depth += 1;
                            }}

                            if (isVisible(target)) {{
                                target.click();
                                target.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }}));
                                return true;
                            }}
                        }}

                        for (const container of scrollContainers) {{
                            if (container.scrollHeight > container.clientHeight) {{
                                container.scrollTop = Math.min(
                                    container.scrollHeight,
                                    container.scrollTop + Math.max(500, container.clientHeight * 0.8)
                                );
                            }}
                        }}
                    }}

                    return false;
                }}
                """
            )
            return bool(clicked)
        except Exception:
            return False

    async def _check_form_errors(self) -> bool:
        """Check if there are any visible error messages on the form."""
        page = self.browser.page
        error_selectors = [
            ".artdeco-inline-feedback--error",
            "[class*='error']",
            ".fb-form-element__error-text",
            "[role='alert']",
        ]
        for sel in error_selectors:
            count = await page.locator(sel).count()
            if count > 0:
                for i in range(count):
                    try:
                        el = page.locator(sel).nth(i)
                        if await el.is_visible():
                            text = await el.inner_text()
                            if text.strip():
                                logger.debug("Form error found: {}", text.strip()[:100])
                                return True
                    except Exception:
                        continue
        return False

    async def _check_application_success(self) -> bool:
        """Check if the application was submitted successfully."""
        page = self.browser.page

        # Combined selector -- wait for ANY success indicator in one shot
        combined = ", ".join([
            "h2:has-text('Application sent')",
            "h3:has-text('Application sent')",
            "h2:has-text('Your application was sent')",
            ".artdeco-modal h2:has-text('sent')",
            ".artdeco-modal h3:has-text('sent')",
            ".post-apply-timeline",
            "[data-test-modal-id='post-apply-modal']",
            ".jpac-modal-header",
        ])
        try:
            await page.wait_for_selector(combined, timeout=8000)
            logger.info("Application success confirmed (combined wait)")
            return True
        except Exception:
            pass

        # Secondary wait -- buttons that only appear after successful submit
        btn_combined = ", ".join([
            "button:has-text('Done')",
            "button:has-text('Not now')",
        ])
        try:
            await page.wait_for_selector(btn_combined, timeout=3000)
            logger.info("Application success confirmed (Done/Not now button appeared)")
            return True
        except Exception:
            pass

        # Fallback locator checks
        success_indicators = [
            "text='Application sent'",
            "text='Your application was sent'",
            "[class*='artdeco-modal'] h2:has-text('submitted')",
            "[class*='artdeco-modal'] h2:has-text('sent')",
            "text='application has been submitted'",
            ".artdeco-inline-feedback--success",
            "[data-test-artdeco-toast]:has-text('sent')",
            "[data-test-artdeco-toast]:has-text('submitted')",
            ".artdeco-modal svg[data-test-icon='check-circle']",
        ]
        for sel in success_indicators:
            try:
                locator = page.locator(sel).first
                if await locator.count() > 0 and await locator.is_visible():
                    logger.info("Application success confirmed (indicator: {})", sel[:40])
                    return True
            except Exception:
                continue

        # Check page text
        try:
            body_text = await page.inner_text("body")
            body_lower = body_text.lower()
            if any(phrase in body_lower for phrase in [
                "application sent", "application submitted",
                "your application was sent", "successfully applied",
                "you applied", "your application has been submitted",
                "application was sent to",
            ]):
                logger.info("Application success confirmed via page text!")
                return True
        except Exception:
            pass

        # Log what IS visible to help debug
        try:
            modal_text = await page.locator(".artdeco-modal, .jobs-easy-apply-modal").first.inner_text()
            logger.warning("Post-submit modal text: {}", modal_text[:300])
        except Exception:
            pass

        logger.warning("Could not confirm application success")
        return False

    async def _verify_applied_after_submit(self, job: Job) -> bool:
        """Re-open the job and require LinkedIn to show an applied state."""
        try:
            await asyncio.sleep(2)
            reopened = await self._open_job_in_search_context(job)
            if not reopened:
                return False
            await asyncio.sleep(2)
            if await self._is_already_applied():
                logger.info("Application success confirmed by reopened job state")
                return True
        except Exception as e:
            logger.debug("Applied-state verification failed: {}", str(e)[:120])
        return False

    async def _dismiss_success_modal(self) -> None:
        """Close the success confirmation modal."""
        dismiss_selectors = [
            "button[aria-label='Dismiss']",
            "button:has-text('Done')",
            ".artdeco-modal__dismiss",
            "button:has-text('Not now')",
        ]
        for sel in dismiss_selectors:
            try:
                if await self.browser.safe_click(sel, timeout=3000):
                    logger.debug("Dismissed success modal")
                    return
            except Exception:
                continue

    async def _close_easy_apply_modal(self) -> None:
        """Close the Easy Apply modal if open (for cleanup/abort)."""
        page = self.browser.page
        try:
            # Click the X button
            close = await self.browser.safe_click(
                "button[aria-label='Dismiss'], .artdeco-modal__dismiss", timeout=3000
            )
            if close:
                await asyncio.sleep(1)
                # Confirm discard if prompted
                await self.browser.safe_click(
                    "button[data-test-dialog-primary-btn], "
                    "button:has-text('Discard'), "
                    "button:has-text('Yes')",
                    timeout=3000,
                )
        except Exception:
            pass

    async def _is_already_applied(self) -> bool:
        """Check if we already applied to this job."""
        page = self.browser.page
        try:
            applied_indicators = [
                "text='Applied'",
                ".jobs-apply-button:has-text('Applied')",
                "[class*='applied']",
            ]
            for sel in applied_indicators:
                locator = page.locator(sel).first
                if await locator.count() > 0 and await locator.is_visible():
                    return True
        except Exception:
            pass
        return False

    # ── Job relevance check ─────────────────────────────────────────────
    async def _check_relevance(self, job: Job) -> bool:
        """Use LLM to check if the job is relevant for the applicant.
        
        A score >= 50 is considered relevant regardless of the LLM's
        ``should_apply`` flag (models sometimes disagree with their own score).
        """
        try:
            user_msg = JOB_RELEVANCE_USER.format(
                target_roles=", ".join(PROFILE["target_roles"]),
                skills=", ".join(PROFILE["primary_skills"]),
                job_title=job.title,
                company=job.company,
                location=job.location,
                job_description=job.description[:3000] if job.description else "Not available",
            )
            result = await llm_client.chat_json(
                system_prompt=JOB_RELEVANCE_SYSTEM,
                user_message=user_msg,
            )
            should_apply = result.get("should_apply", True)
            score = result.get("relevance_score", 50)
            reason = result.get("reason", "")

            # Override: if the score is decent, apply anyway
            if score >= 50 and not should_apply:
                logger.info(
                    "Relevance override for '{}': score={} >= 50 → forcing apply (LLM said no: {})",
                    job.title, score, reason[:60],
                )
                should_apply = True

            logger.info(
                "Relevance check for '{}': score={}, apply={}, reason={}",
                job.title, score, should_apply, reason[:80],
            )
            return should_apply
        except Exception as e:
            logger.warning("Relevance check failed: {} — defaulting to apply", str(e)[:100])
            return True  # Apply by default if LLM check fails
