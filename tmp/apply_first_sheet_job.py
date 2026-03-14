from __future__ import annotations

import asyncio
import sys
from pathlib import Path
import importlib.util

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
from linkedin.apply import LinkedInApply
from linkedin.form_filler import FormFiller
from utils.sheets_exporter import GoogleSheetsExporter


def build_first_job(exporter: GoogleSheetsExporter):
    jobs = exporter.read_unapplied_jobs(easy_apply_only=True)
    return jobs[0] if jobs else None


async def main() -> None:
    exporter = GoogleSheetsExporter()
    job = build_first_job(exporter)
    if not job:
        print("No eligible job found in the sheet.")
        return

    print(f"Selected first sheet job: {job.title} | {job.company} | {job.url}")

    browser = BrowserEngine()
    await browser.start()
    try:
        auth = LinkedInAuth(browser)
        logged_in = await auth.login()
        if not logged_in:
            print("Login failed.")
            return

        form_filler = FormFiller(browser)
        applier = LinkedInApply(browser, form_filler)
        applier._check_relevance = lambda _job: asyncio.sleep(0, result=True)
        result = await applier.apply_to_job(job)

        notes = "" if result.status.value == "applied" else "; ".join(result.errors) if result.errors else ""
        exporter.update_job_status(job.url, result.status.value, notes)

        print(f"Result: {result.status.value}")
        if result.errors:
            print("Errors:")
            for err in result.errors:
                print(f"- {err}")
    finally:
        await browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
