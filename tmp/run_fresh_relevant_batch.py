from __future__ import annotations

import asyncio
import importlib.util
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

from config import settings
from orchestrator import Orchestrator
from profile import PROFILE
from utils.csv_exporter import export_jobs_to_csv


async def main() -> None:
    limit = 5
    settings.max_applications_per_session = limit
    settings.application_delay_min = 1
    settings.application_delay_max = 2

    orch = Orchestrator()
    try:
        await orch.start()

        print("Logging into LinkedIn...")
        logged_in = await orch.auth.login()
        if not logged_in:
            print("Login failed")
            return

        print("Clearing Google Sheet...")
        if not orch.sheets_exporter.clear_all_jobs():
            print("Failed to clear Google Sheet")
            return

        print("Scraping fresh jobs...")
        all_scraped_jobs = []
        relevant_jobs = []
        relevant_easy_apply_jobs = []
        selected_jobs = []

        for keyword in PROFILE.get("job_search_keywords", []):
            if len(selected_jobs) >= limit:
                break

            jobs = await orch.searcher.search_jobs(
                keyword=keyword,
                location="Remote",
                max_pages=3,
                min_jobs=5,
                fetch_details=True,
            )
            if not jobs:
                continue

            all_scraped_jobs.extend(jobs)
            filtered_jobs = await orch._filter_relevant_jobs(jobs)
            relevant_jobs.extend(filtered_jobs)

            for job in filtered_jobs:
                if not job.is_easy_apply:
                    continue
                relevant_easy_apply_jobs.append(job)
                if len(selected_jobs) >= limit:
                    continue
                try:
                    opened = await orch.applier._open_job_in_search_context(job)
                    if not opened:
                        continue
                    if await orch.applier._find_easy_apply_button():
                        selected_jobs.append(job)
                except Exception:
                    continue

        print(f"Scraped jobs: {len(all_scraped_jobs)}")
        if not all_scraped_jobs:
            print("No jobs scraped")
            return

        print(f"Relevant jobs: {len(relevant_jobs)}")
        print(f"Relevant Easy Apply jobs: {len(relevant_easy_apply_jobs)}")
        print(f"Prevalidated Easy Apply jobs: {len(selected_jobs)}")
        print(f"Selected jobs for batch: {len(selected_jobs)}")
        for idx, job in enumerate(selected_jobs, start=1):
            print(f"{idx}. {job.title} | {job.company} | {job.url}")

        if not selected_jobs:
            print("No relevant Easy Apply jobs available")
            return

        orch.stats.total_found = len(selected_jobs)

        csv_path, csv_count = export_jobs_to_csv(selected_jobs)
        print(f"CSV export: {csv_count} -> {csv_path}")

        sheet_count = orch.sheets_exporter.export_jobs(selected_jobs)
        print(f"Sheet export: {sheet_count}")

        await orch._apply_jobs(selected_jobs, update_sheet=True)
        orch.stats.session_end = __import__("datetime").datetime.now()
        await orch._save_report()

        print("Final stats:")
        print(f"Applied: {orch.stats.total_applied}")
        print(f"Skipped: {orch.stats.total_skipped}")
        print(f"Failed: {orch.stats.total_failed}")
        print(f"External: {orch.stats.total_external}")
    finally:
        await orch.stop()


if __name__ == "__main__":
    asyncio.run(main())
