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

from browser.engine import BrowserEngine
from linkedin.auth import LinkedInAuth
from linkedin.apply import LinkedInApply
from linkedin.form_filler import FormFiller
from utils.sheets_exporter import GoogleSheetsExporter


async def main() -> None:
    exporter = GoogleSheetsExporter()
    jobs = exporter.read_unapplied_jobs(easy_apply_only=True)
    if not jobs:
        print("No Easy Apply jobs found.")
        return

    job = jobs[0]
    print(f"Debugging job: {job.title} | {job.company} | {job.url}")

    browser = BrowserEngine()
    await browser.start()
    try:
        auth = LinkedInAuth(browser)
        if not await auth.login():
            print("Login failed")
            return

        form_filler = FormFiller(browser)
        applier = LinkedInApply(browser, form_filler)

        opened = await applier._open_job_in_search_context(job)
        print("opened:", opened)
        print("easy apply present:", await applier._find_easy_apply_button())
        print("already applied:", await applier._is_already_applied())
        clicked = await applier._click_easy_apply()
        print("clicked easy apply:", clicked)
        await asyncio.sleep(3)

        page = browser.page
        modal_visible = await browser.is_element_visible('.jobs-easy-apply-modal, .artdeco-modal')
        print("modal visible after open:", modal_visible)
        if modal_visible:
            try:
                modal_text = await page.locator('.jobs-easy-apply-modal, .artdeco-modal').first.inner_text()
                print("modal text before fill:")
                print(modal_text[:2000])
            except Exception as exc:
                print("modal text read failed:", exc)
            print("buttons before fill:", await applier._get_visible_modal_button_labels())
            print("form filled:", await form_filler.fill_current_form(job))
            await asyncio.sleep(1)
            print("buttons after fill:", await applier._get_visible_modal_button_labels())
            print("action:", await applier._get_form_action())
            await browser.take_screenshot(f"debug_modal_before_review_{job.job_id}")
            reviewed = await applier._click_review()
            print("clicked review:", reviewed)
            await asyncio.sleep(3)
            modal_visible_2 = await browser.is_element_visible('.jobs-easy-apply-modal, .artdeco-modal')
            print("modal visible after review:", modal_visible_2)
            print("current url:", await browser.get_current_url())
            print("already applied after review:", await applier._is_already_applied())
            if modal_visible_2:
                try:
                    modal_text_2 = await page.locator('.jobs-easy-apply-modal, .artdeco-modal').first.inner_text()
                    print("modal text after review:")
                    print(modal_text_2[:2000])
                except Exception as exc:
                    print("modal text after review read failed:", exc)
                print("buttons after review:", await applier._get_visible_modal_button_labels())
                await browser.take_screenshot(f"debug_modal_after_review_{job.job_id}")
            else:
                body_text = await browser.get_page_text()
                print("page text after review:")
                print(body_text[:2000])
                await browser.take_screenshot(f"debug_after_review_closed_{job.job_id}")
        else:
            print("Modal did not open")
    finally:
        await browser.stop()


if __name__ == "__main__":
    asyncio.run(main())
