"""
Orchestrator — ties together search, apply, form-filling, and error handling
into a single automated pipeline.

Supports three modes:
  • **scrape** — search → collect details → export to Google Sheets + CSV
  • **apply**  — read unapplied jobs from Google Sheet → apply → update status
  • **run**    — scrape *then* apply (combined)
    • **people** — read companies from the sheet → scrape up to 5 people/company
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from browser.engine import BrowserEngine
from config import settings, DATA_DIR
from linkedin.auth import LinkedInAuth
from linkedin.search import LinkedInSearch
from linkedin.company_people import LinkedInCompanyPeople
from linkedin.apply import LinkedInApply
from linkedin.form_filler import FormFiller
from Outreach.investor_search import LinkedInInvestorSearch
from Outreach.messenger import LinkedInInvestorMessenger
from Outreach.referral_messenger import LinkedInReferralMessenger
from llm.client import llm_client
from models.schemas import (
    ApplicationResult,
    BotState,
    InvestorLead,
    InvestorOutreachResult,
    InvestorOutreachStatus,
    Job,
    JobStatus,
    ReferralReachoutResult,
    ReferralReachoutStatus,
    SessionStats,
)
from profile import PROFILE
from utils.helpers import human_delay
from utils.csv_exporter import export_jobs_to_csv
from utils.sheets_exporter import GoogleSheetsExporter


class Orchestrator:
    """
    Main automation controller.  Runs the full pipeline:
      login → search → filter → apply → report
    """

    def __init__(self) -> None:
        self.browser = BrowserEngine()
        self.auth: LinkedInAuth | None = None
        self.searcher: LinkedInSearch | None = None
        self.people_scraper: LinkedInCompanyPeople | None = None
        self.investor_searcher: LinkedInInvestorSearch | None = None
        self.investor_messenger: LinkedInInvestorMessenger | None = None
        self.referral_messenger: LinkedInReferralMessenger | None = None
        self.applier: LinkedInApply | None = None
        self.form_filler: FormFiller | None = None
        self.sheets_exporter = GoogleSheetsExporter()

        self.state = BotState.IDLE
        self.stats = SessionStats()
        self._stop_requested = False
        self._pause_requested = False

    # ── Lifecycle ───────────────────────────────────────────────────────
    async def start(self) -> None:
        """Initialise browser and all modules."""
        logger.info("╔══════════════════════════════════════════════╗")
        logger.info("║   LinkedIn Auto-Apply Bot — Starting Up     ║")
        logger.info("╚══════════════════════════════════════════════╝")

        await self.browser.start()
        self.auth = LinkedInAuth(self.browser)
        self.searcher = LinkedInSearch(self.browser)
        self.people_scraper = LinkedInCompanyPeople(self.browser)
        self.investor_searcher = LinkedInInvestorSearch(self.browser)
        self.investor_messenger = LinkedInInvestorMessenger(self.browser)
        self.referral_messenger = LinkedInReferralMessenger(self.browser)
        self.form_filler = FormFiller(self.browser)
        self.applier = LinkedInApply(self.browser, self.form_filler)

        self.stats = SessionStats(session_start=datetime.now())
        self.state = BotState.IDLE
        self._stop_requested = False
        logger.info("All modules initialised.")

    async def stop(self) -> None:
        """Gracefully shut down."""
        logger.info("Shutting down orchestrator…")
        self._stop_requested = True
        self.state = BotState.STOPPED
        self.stats.session_end = datetime.now()

        # Save session report
        await self._save_report()

        await self.browser.stop()
        logger.info("Orchestrator stopped.")

    # ── Main pipeline ───────────────────────────────────────────────────
    async def run_scrape_pipeline(self) -> list[Job]:
        """
        Scrape-only pipeline:
        1. Log in to LinkedIn
        2. Search for jobs + fetch details inline (click each card)
        3. Export results to Google Sheets + CSV
        Returns the list of scraped jobs.
        """
        try:
            await self.start()

            # ── Step 1: Login ───────────────────────────────────────────
            self.state = BotState.LOGGING_IN
            logger.info("Step 1/2 — Logging in to LinkedIn…")
            logged_in = await self.auth.login()
            if not logged_in:
                logger.error("Login failed! Aborting scrape pipeline.")
                self.state = BotState.ERROR
                return []

            # ── Step 2: Search + details (inline) ───────────────────────
            self.state = BotState.SEARCHING
            logger.info("Step 2/2 — Searching for jobs (min {} per keyword)…",
                        settings.min_jobs_per_keyword)
            detailed_jobs = await self.searcher.search_all_keywords(
                max_pages_per_keyword=5,
            )
            self.stats.total_found = len(detailed_jobs)
            logger.info("Found {} total unique jobs with details", len(detailed_jobs))

            if not detailed_jobs:
                logger.warning("No jobs found! Aborting scrape pipeline.")
                return []

            detailed_jobs = await self._filter_relevant_jobs(detailed_jobs)
            logger.info("Relevant jobs retained for export: {}", len(detailed_jobs))

            if not detailed_jobs:
                logger.warning("No relevant jobs found after filtering! Aborting scrape pipeline.")
                return []

            # ── Export to CSV ───────────────────────────────────────────
            csv_path, csv_count = export_jobs_to_csv(detailed_jobs)
            logger.info("CSV export: {} new rows → {}", csv_count, csv_path)

            # ── Export to Google Sheets ─────────────────────────────────
            sheets_count = self.sheets_exporter.export_jobs(detailed_jobs)
            logger.info("Google Sheets export: {} new rows", sheets_count)

            self.state = BotState.IDLE
            self.stats.session_end = datetime.now()
            await self._save_report()
            self._print_summary()

            return detailed_jobs

        except Exception as e:
            logger.error("Scrape pipeline error: {}", str(e))
            self.state = BotState.ERROR
            raise
        finally:
            await self.stop()

    async def run_full_pipeline(self) -> SessionStats:
        """
        Execute the complete job-application pipeline:
        1. Scrape jobs (login → search → details → Sheets/CSV)
        2. Apply to each collected job
        3. Generate report
        """
        try:
            await self.start()

            # ── Step 1: Login ───────────────────────────────────────────
            self.state = BotState.LOGGING_IN
            logger.info("Step 1/3 — Logging in to LinkedIn…")
            logged_in = await self.auth.login()
            if not logged_in:
                logger.error("Login failed! Aborting pipeline.")
                self.state = BotState.ERROR
                return self.stats

            # ── Step 2: Search for jobs + details ───────────────────────
            self.state = BotState.SEARCHING
            logger.info("Step 2/3 — Searching for jobs (min {} per keyword)…",
                        settings.min_jobs_per_keyword)
            detailed_jobs = await self.searcher.search_all_keywords(
                max_pages_per_keyword=5,
            )
            self.stats.total_found = len(detailed_jobs)
            logger.info("Found {} total unique jobs with details", len(detailed_jobs))

            if not detailed_jobs:
                logger.warning("No jobs found! Aborting.")
                return self.stats

            detailed_jobs = await self._filter_relevant_jobs(detailed_jobs)
            logger.info("Relevant jobs retained for export/apply: {}", len(detailed_jobs))

            if not detailed_jobs:
                logger.warning("No relevant jobs found after filtering! Aborting.")
                return self.stats

            # Export scraped data before applying
            csv_path, csv_count = export_jobs_to_csv(detailed_jobs)
            logger.info("CSV export: {} new rows → {}", csv_count, csv_path)
            sheets_count = self.sheets_exporter.export_jobs(detailed_jobs)
            logger.info("Google Sheets export: {} new rows", sheets_count)

            # ── Step 3: Apply to jobs ───────────────────────────────────
            self.state = BotState.APPLYING
            max_apps = settings.max_applications_per_session
            applied_count = 0

            logger.info("Step 3/3 — Applying to {} jobs (max {})",
                        len(detailed_jobs), max_apps)

            applied_count = await self._apply_jobs(detailed_jobs, update_sheet=True)

            self.state = BotState.IDLE
            self.stats.session_end = datetime.now()
            await self._save_report()
            self._print_summary()

            return self.stats

        except Exception as e:
            logger.error("Pipeline error: {}", str(e))
            self.state = BotState.ERROR
            raise
        finally:
            await self.stop()

    # ── Apply-from-sheet pipeline ───────────────────────────────────────
    async def run_apply_from_sheet_pipeline(self, limit: int | None = None) -> SessionStats:
        """
        Read unapplied jobs from Google Sheet → apply → update status in sheet.

        Parameters
        ----------
        limit : int or None
            Maximum number of jobs to attempt.  Defaults to
            ``settings.max_applications_per_session``.
        """
        try:
            await self.start()

            # ── Step 1: Login ───────────────────────────────────────────
            self.state = BotState.LOGGING_IN
            logger.info("Step 1/2 — Logging in to LinkedIn…")
            logged_in = await self.auth.login()
            if not logged_in:
                logger.error("Login failed! Aborting apply pipeline.")
                self.state = BotState.ERROR
                return self.stats

            # ── Step 2: Read unapplied jobs from sheet ──────────────────
            logger.info("Step 2/2 — Reading Easy Apply jobs from Google Sheet…")
            jobs = self.sheets_exporter.read_unapplied_jobs(easy_apply_only=True)
            if not jobs:
                logger.warning("No unapplied Easy Apply jobs in the sheet!")
                return self.stats
            self.stats.total_found = len(jobs)
            logger.info("Loaded {} unapplied Easy Apply jobs from Google Sheet", len(jobs))

            # ── Apply ───────────────────────────────────────────────────
            self.state = BotState.APPLYING
            if limit is not None:
                max_apps = limit
                settings.max_applications_per_session = limit
            else:
                max_apps = settings.max_applications_per_session

            logger.info("Applying to jobs (max {})…", max_apps)
            await self._apply_jobs(jobs, update_sheet=True)

            self.state = BotState.IDLE
            self.stats.session_end = datetime.now()
            await self._save_report()
            self._print_summary()

            return self.stats

        except Exception as e:
            logger.error("Apply-from-sheet pipeline error: {}", str(e))
            self.state = BotState.ERROR
            raise
        finally:
            await self.stop()

    async def run_people_scrape_pipeline(self, limit_per_company: int = 5) -> dict[str, list[dict[str, str]]]:
        """Read companies from the sheet, scrape people, and write them back to the sheet."""
        results: dict[str, list[dict[str, str]]] = {}
        try:
            await self.start()

            self.state = BotState.LOGGING_IN
            logger.info("Step 1/2 — Logging in to LinkedIn…")
            logged_in = await self.auth.login()
            if not logged_in:
                logger.error("Login failed! Aborting people pipeline.")
                self.state = BotState.ERROR
                return results

            self.state = BotState.SEARCHING
            logger.info("Step 2/2 — Reading companies from Google Sheet…")
            companies = self.sheets_exporter.read_company_names()
            if not companies:
                logger.warning("No companies found in the sheet!")
                return results

            logger.info("Scraping up to {} people for {} companies", limit_per_company, len(companies))
            for company in companies:
                if self._stop_requested:
                    break
                try:
                    people = await self.people_scraper.search_company_people(company, limit=limit_per_company)
                except Exception as e:
                    logger.warning("People scrape failed for '{}': {}", company, str(e)[:120])
                    people = []
                results[company] = people
                self.sheets_exporter.update_company_people(company, people)
                await human_delay(1, 2)

            self.stats.session_end = datetime.now()
            await self._save_people_report(results)
            return results

        except Exception as e:
            logger.error("People pipeline error: {}", str(e))
            self.state = BotState.ERROR
            raise
        finally:
            await self.stop()

    async def run_outreach_pipeline(self, limit: int | None = None) -> list[InvestorLead]:
        """Log in to LinkedIn, find investor leads, and export them to the VCs worksheet."""
        leads: list[InvestorLead] = []
        try:
            await self.start()

            self.state = BotState.LOGGING_IN
            logger.info("Step 1/2 — Logging in to LinkedIn…")
            logged_in = await self.auth.login()
            if not logged_in:
                logger.error("Login failed! Aborting outreach pipeline.")
                self.state = BotState.ERROR
                return leads

            self.state = BotState.SEARCHING
            target_results = limit or settings.vc_target_results
            logger.info(
                "Step 2/2 — Searching LinkedIn for investor/VC leads (target {})…",
                target_results,
            )
            leads = await self.investor_searcher.search_investors(max_results=target_results)
            self.stats.total_found = len(leads)

            if not leads:
                logger.warning("No investor leads found! Aborting outreach pipeline.")
                return leads

            exported = self.sheets_exporter.export_investors(leads)
            logger.info(
                "Google Sheets export to '{}' worksheet: {} new rows",
                settings.google_vc_sheet_name,
                exported,
            )

            self.state = BotState.IDLE
            self.stats.session_end = datetime.now()
            await self._save_outreach_report(leads)
            logger.info("Outreach pipeline finished — {} investor leads collected", len(leads))
            return leads

        except Exception as e:
            logger.error("Outreach pipeline error: {}", str(e))
            self.state = BotState.ERROR
            raise
        finally:
            await self.stop()

    async def run_vc_reachout_pipeline(self, limit: int | None = None) -> list[InvestorOutreachResult]:
        """Read VC leads from the sheet and message/connect with them on LinkedIn."""
        results: list[InvestorOutreachResult] = []
        try:
            await self.start()

            self.state = BotState.LOGGING_IN
            logger.info("Step 1/2 — Logging in to LinkedIn…")
            logged_in = await self.auth.login()
            if not logged_in:
                logger.error("Login failed! Aborting VC reachout pipeline.")
                self.state = BotState.ERROR
                return results

            self.state = BotState.APPLYING
            batch_limit = limit or settings.vc_reachout_batch_size
            logger.info("Step 2/2 — Reading VC leads from the '{}' worksheet…", settings.google_vc_sheet_name)
            investors = self.sheets_exporter.read_pending_investors(limit=batch_limit)
            self.stats.total_found = len(investors)
            if not investors:
                logger.warning("No pending investors found in the '{}' worksheet!", settings.google_vc_sheet_name)
                return results

            logger.info("Starting investor reachout for {} leads", len(investors))
            for idx, investor in enumerate(investors, start=1):
                if self._stop_requested:
                    break
                while self._pause_requested:
                    await asyncio.sleep(1)

                logger.info("Reaching out to investor [{}/{}] — {}", idx, len(investors), investor.investor_name)
                result = await self.investor_messenger.reach_out_to_investor(investor)
                results.append(result)

                contacted_at = result.sent_at.strftime("%Y-%m-%d %H:%M:%S") if result.sent_at else ""
                self.sheets_exporter.update_investor_outreach(
                    investor.linkedin_profile_url,
                    status=result.status.value,
                    action=result.action_taken,
                    connected_status=result.connected_status,
                    contacted_at=contacted_at,
                    message_text=result.message_text,
                    notes=result.notes or ("; ".join(result.errors) if result.errors else ""),
                )
                await human_delay(3, 6)

            self.state = BotState.IDLE
            self.stats.session_end = datetime.now()
            await self._save_vc_reachout_report(results)
            logger.info("VC reachout pipeline finished — {} investors processed", len(results))
            return results

        except Exception as e:
            logger.error("VC reachout pipeline error: {}", str(e))
            self.state = BotState.ERROR
            raise
        finally:
            await self.stop()

    async def run_vc_connection_followup_pipeline(self, limit: int | None = None) -> list[InvestorOutreachResult]:
        """Check pending connection requests and send follow-up messages if accepted."""
        results: list[InvestorOutreachResult] = []
        try:
            await self.start()

            self.state = BotState.LOGGING_IN
            logger.info("Step 1/2 — Logging in to LinkedIn…")
            logged_in = await self.auth.login()
            if not logged_in:
                logger.error("Login failed! Aborting VC connection followup pipeline.")
                self.state = BotState.ERROR
                return results

            self.state = BotState.APPLYING
            batch_limit = limit or settings.vc_reachout_batch_size
            logger.info("Step 2/2 — Reading pending connections from the '{}' worksheet…", settings.google_vc_sheet_name)
            investors = self.sheets_exporter.read_pending_connections(limit=batch_limit)
            self.stats.total_found = len(investors)
            if not investors:
                logger.warning("No pending connections found in the '{}' worksheet!", settings.google_vc_sheet_name)
                return results

            logger.info("Checking {} pending connections for acceptance", len(investors))
            for idx, investor in enumerate(investors, start=1):
                if self._stop_requested:
                    break
                while self._pause_requested:
                    await asyncio.sleep(1)

                logger.info("Checking connection [{}/{}] — {}", idx, len(investors), investor.investor_name)
                result = await self.investor_messenger.check_and_message_pending_connection(investor)
                results.append(result)

                contacted_at = result.sent_at.strftime("%Y-%m-%d %H:%M:%S") if result.sent_at else ""
                self.sheets_exporter.update_investor_outreach(
                    investor.linkedin_profile_url,
                    status=result.status.value,
                    action=result.action_taken,
                    connected_status=result.connected_status,
                    contacted_at=contacted_at,
                    message_text=result.message_text,
                    notes=result.notes or ("; ".join(result.errors) if result.errors else ""),
                )
                await human_delay(3, 6)

            self.state = BotState.IDLE
            self.stats.session_end = datetime.now()
            await self._save_vc_reachout_report(results)
            logger.info("VC connection followup pipeline finished — {} connections checked", len(results))
            return results

        except Exception as e:
            logger.error("VC connection followup pipeline error: {}", str(e))
            self.state = BotState.ERROR
            raise
        finally:
            await self.stop()

    async def run_referral_reachout_pipeline(self, limit: int | None = None) -> list[ReferralReachoutResult]:
        """Reach out to employees at companies with external-apply jobs."""
        results: list[ReferralReachoutResult] = []
        try:
            await self.start()

            self.state = BotState.LOGGING_IN
            logger.info("Step 1/3 — Logging in to LinkedIn…")
            logged_in = await self.auth.login()
            if not logged_in:
                logger.error("Login failed! Aborting referral reachout pipeline.")
                self.state = BotState.ERROR
                return results

            self.state = BotState.SEARCHING
            logger.info("Step 2/3 — Backfilling people for external-apply companies missing contacts…")
            missing_companies = self.sheets_exporter.read_external_company_names(only_missing_people=True)
            logger.info("Found {} external companies still missing people", len(missing_companies))
            for company in missing_companies:
                if self._stop_requested:
                    break
                try:
                    people = await self.people_scraper.search_company_people(company, limit=5)
                except Exception as e:
                    logger.warning("People scrape failed for external company '{}': {}", company, str(e)[:120])
                    people = []
                self.sheets_exporter.update_company_people(company, people)
                await human_delay(1, 2)

            self.state = BotState.APPLYING
            batch_limit = limit or settings.vc_reachout_batch_size
            logger.info("Step 3/3 — Reading pending referral contacts from Google Sheet…")
            contacts = self.sheets_exporter.read_pending_referral_contacts(limit=batch_limit)
            self.stats.total_found = len(contacts)
            if not contacts:
                logger.warning("No pending referral contacts found for external-apply jobs!")
                return results

            logger.info("Starting referral reachout for {} contacts", len(contacts))
            for idx, contact in enumerate(contacts, start=1):
                if self._stop_requested:
                    break
                while self._pause_requested:
                    await asyncio.sleep(1)

                logger.info(
                    "Reaching out to employee [{}/{}] — {} at {}",
                    idx,
                    len(contacts),
                    contact.person_name or contact.linkedin_profile_url,
                    contact.company,
                )
                result = await self.referral_messenger.reach_out_to_contact(contact)
                results.append(result)

                timestamp = result.sent_at.strftime("%Y-%m-%d %H:%M:%S") if result.sent_at else ""
                connection_status = contact.connection_status
                connection_requested_at = contact.connection_requested_at
                message_status = contact.message_status
                message_sent_at = contact.message_sent_at

                if result.status == ReferralReachoutStatus.MESSAGE_SENT:
                    connection_status = "connected"
                    message_status = "message_sent"
                    message_sent_at = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                elif result.status == ReferralReachoutStatus.CONNECT_REQUESTED:
                    connection_status = "connect_requested"
                    connection_requested_at = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                elif result.status == ReferralReachoutStatus.CONNECTED:
                    connection_status = "connected"

                self.sheets_exporter.update_referral_contact_outreach(
                    contact.job_url,
                    contact.person_index,
                    connection_status=connection_status,
                    connection_requested_at=connection_requested_at,
                    message_status=message_status,
                    message_sent_at=message_sent_at,
                    notes=result.notes or ("; ".join(result.errors) if result.errors else ""),
                )
                await human_delay(3, 6)

            self.state = BotState.IDLE
            self.stats.session_end = datetime.now()
            await self._save_referral_reachout_report(results)
            logger.info("Referral reachout pipeline finished — {} contacts processed", len(results))
            return results

        except Exception as e:
            logger.error("Referral reachout pipeline error: {}", str(e))
            self.state = BotState.ERROR
            raise
        finally:
            await self.stop()

    async def _filter_relevant_jobs(self, jobs: list[Job]) -> list[Job]:
        """Run relevance filtering before exporting jobs to the sheet."""
        if not jobs:
            return []

        if not self.applier:
            logger.warning("Applier not initialised; skipping relevance pre-filter")
            return jobs

        relevant_jobs: list[Job] = []
        skipped = 0

        for job in jobs:
            if self._stop_requested:
                break

            if not job.description:
                relevant_jobs.append(job)
                continue

            try:
                is_relevant = await self.applier._check_relevance(job)
            except Exception as e:
                logger.warning(
                    "Relevance pre-filter failed for '{}' — keeping job: {}",
                    job.title,
                    str(e)[:100],
                )
                relevant_jobs.append(job)
                continue

            if is_relevant:
                relevant_jobs.append(job)
            else:
                skipped += 1
                job.application_status = JobStatus.SKIPPED.value
                job.application_notes = "Filtered out by relevance check before sheet export"

        logger.info(
            "Relevance pre-filter kept {} jobs and removed {} jobs before sheet export",
            len(relevant_jobs),
            skipped,
        )
        return relevant_jobs

    # ── Shared apply loop ───────────────────────────────────────────────
    async def _apply_jobs(self, jobs: list[Job], *, update_sheet: bool = False) -> int:
        """
        Apply to a list of jobs.  Optionally update the Google Sheet status
        after each application.  Returns the number of jobs successfully applied.
        """
        max_apps = settings.max_applications_per_session
        applied_count = 0
        # Cap total attempts at 5x the target to avoid cycling through the whole list
        max_total_attempts = max_apps * 5

        for idx, job in enumerate(jobs):
            if self._stop_requested or applied_count >= max_apps:
                break
            if idx >= max_total_attempts and applied_count == 0:
                logger.warning(
                    "Stopping after {} attempts with no successful applications",
                    idx,
                )
                break
            while self._pause_requested:
                await asyncio.sleep(1)

            logger.info(
                "╔══ Applying [{}/{}] ══════════════════════════╗",
                idx + 1, len(jobs),
            )
            logger.info("║ Title: {}", job.title[:45])
            logger.info("║ Company: {}", job.company[:45])
            logger.info("║ Location: {}", job.location[:45])
            logger.info("╚══════════════════════════════════════════════╝")

            result = await self.applier.apply_to_job(job)
            self.stats.results.append(result)

            # Update counters
            if result.status == JobStatus.APPLIED:
                self.stats.total_applied += 1
                applied_count += 1
            elif result.status == JobStatus.SKIPPED:
                self.stats.total_skipped += 1
            elif result.status == JobStatus.FAILED:
                self.stats.total_failed += 1
            elif result.status == JobStatus.EXTERNAL:
                self.stats.total_external += 1

            # Update Google Sheet status
            if update_sheet:
                try:
                    notes = "" if result.status == JobStatus.APPLIED else "; ".join(result.errors) if result.errors else ""
                    self.sheets_exporter.update_job_status(
                        job_url=job.url,
                        status=result.status.value,
                        notes=notes,
                    )
                except Exception as e:
                    logger.warning("Failed to update sheet: {}", str(e)[:80])

            logger.info(
                "Result: {} | Total applied: {}/{} | Failed: {} | Skipped: {}",
                result.status.value, applied_count, max_apps,
                self.stats.total_failed, self.stats.total_skipped,
            )

            # Human-like delay between applications
            await human_delay(
                settings.application_delay_min,
                settings.application_delay_max,
            )

        return applied_count

    # ── Control methods (for API) ───────────────────────────────────────
    def request_stop(self) -> None:
        self._stop_requested = True
        logger.info("Stop requested.")

    def request_pause(self) -> None:
        self._pause_requested = True
        self.state = BotState.PAUSED
        logger.info("Pause requested.")

    def request_resume(self) -> None:
        self._pause_requested = False
        self.state = BotState.APPLYING
        logger.info("Resume requested.")

    def get_status(self) -> dict:
        return {
            "state": self.state.value,
            "stats": {
                "total_found": self.stats.total_found,
                "total_applied": self.stats.total_applied,
                "total_skipped": self.stats.total_skipped,
                "total_failed": self.stats.total_failed,
                "total_external": self.stats.total_external,
                "session_start": str(self.stats.session_start) if self.stats.session_start else None,
            },
        }

    # ── Reporting ───────────────────────────────────────────────────────
    async def _save_report(self) -> None:
        """Save the session report to a JSON file."""
        report_path = DATA_DIR / f"report_{datetime.now():%Y%m%d_%H%M%S}.json"
        report = {
            "session_start": str(self.stats.session_start),
            "session_end": str(self.stats.session_end),
            "total_found": self.stats.total_found,
            "total_applied": self.stats.total_applied,
            "total_skipped": self.stats.total_skipped,
            "total_failed": self.stats.total_failed,
            "total_external": self.stats.total_external,
            "results": [],
        }
        for r in self.stats.results:
            report["results"].append({
                "job_title": r.job.title,
                "company": r.job.company,
                "location": r.job.location,
                "url": r.job.url,
                "status": r.status.value,
                "errors": r.errors,
                "duration_seconds": r.duration_seconds,
                "steps": [s.value for s in r.steps_completed],
            })

        report_path.write_text(json.dumps(report, indent=2, default=str))
        logger.info("Session report saved: {}", report_path.name)

    async def _save_people_report(self, results: dict[str, list[dict[str, str]]]) -> None:
        """Save a company-people scrape report to a JSON file."""
        report_path = DATA_DIR / f"people_report_{datetime.now():%Y%m%d_%H%M%S}.json"
        report = {
            "session_start": str(self.stats.session_start),
            "session_end": str(self.stats.session_end),
            "companies": [],
        }
        for company, people in results.items():
            report["companies"].append({
                "company": company,
                "people_found": len(people),
                "people": people,
            })
        report_path.write_text(json.dumps(report, indent=2, default=str))
        logger.info("People report saved: {}", report_path.name)

    async def _save_outreach_report(self, leads: list[InvestorLead]) -> None:
        """Save investor outreach leads to a JSON report file."""
        report_path = DATA_DIR / f"vc_report_{datetime.now():%Y%m%d_%H%M%S}.json"
        report = {
            "session_start": str(self.stats.session_start),
            "session_end": str(self.stats.session_end),
            "leads_found": len(leads),
            "leads": [lead.model_dump() for lead in leads],
        }
        report_path.write_text(json.dumps(report, indent=2, default=str))
        logger.info("Outreach report saved: {}", report_path.name)

    async def _save_vc_reachout_report(self, results: list[InvestorOutreachResult]) -> None:
        """Save investor messaging/connect results to a JSON report file."""
        report_path = DATA_DIR / f"vc_reachout_report_{datetime.now():%Y%m%d_%H%M%S}.json"
        report = {
            "session_start": str(self.stats.session_start),
            "session_end": str(self.stats.session_end),
            "investors_processed": len(results),
            "results": [
                {
                    "investor_name": result.investor.investor_name,
                    "linkedin_profile_url": result.investor.linkedin_profile_url,
                    "status": result.status.value,
                    "action_taken": result.action_taken,
                    "connected_status": result.connected_status,
                    "message_text": result.message_text,
                    "notes": result.notes,
                    "errors": result.errors,
                    "sent_at": str(result.sent_at) if result.sent_at else None,
                }
                for result in results
            ],
        }
        report_path.write_text(json.dumps(report, indent=2, default=str))
        logger.info("VC reachout report saved: {}", report_path.name)

    async def _save_referral_reachout_report(self, results: list[ReferralReachoutResult]) -> None:
        """Save external-job referral reachout results to a JSON report file."""
        report_path = DATA_DIR / f"referral_reachout_report_{datetime.now():%Y%m%d_%H%M%S}.json"
        report = {
            "session_start": str(self.stats.session_start),
            "session_end": str(self.stats.session_end),
            "contacts_processed": len(results),
            "results": [
                {
                    "job_title": result.contact.job_title,
                    "company": result.contact.company,
                    "job_url": result.contact.job_url,
                    "apply_link": result.contact.apply_link,
                    "person_index": result.contact.person_index,
                    "person_name": result.contact.person_name,
                    "person_designation": result.contact.person_designation,
                    "linkedin_profile_url": result.contact.linkedin_profile_url,
                    "status": result.status.value,
                    "action_taken": result.action_taken,
                    "connected_status": result.connected_status,
                    "message_text": result.message_text,
                    "notes": result.notes,
                    "errors": result.errors,
                    "sent_at": str(result.sent_at) if result.sent_at else None,
                }
                for result in results
            ],
        }
        report_path.write_text(json.dumps(report, indent=2, default=str))
        logger.info("Referral reachout report saved: {}", report_path.name)

    def _print_summary(self) -> None:
        """Print a formatted summary to the console."""
        logger.info("╔══════════════════════════════════════════════╗")
        logger.info("║         SESSION SUMMARY                     ║")
        logger.info("╠══════════════════════════════════════════════╣")
        logger.info("║ Jobs Found:     {:>6}                       ║", self.stats.total_found)
        logger.info("║ Applied:        {:>6}                       ║", self.stats.total_applied)
        logger.info("║ Skipped:        {:>6}                       ║", self.stats.total_skipped)
        logger.info("║ Failed:         {:>6}                       ║", self.stats.total_failed)
        logger.info("║ External:       {:>6}                       ║", self.stats.total_external)
        logger.info("╚══════════════════════════════════════════════╝")


# Singleton
orchestrator = Orchestrator()
