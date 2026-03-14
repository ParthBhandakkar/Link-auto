from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

profile_spec = importlib.util.spec_from_file_location("profile", PROJECT_ROOT / "profile.py")
if profile_spec and profile_spec.loader:
    profile_module = importlib.util.module_from_spec(profile_spec)
    profile_spec.loader.exec_module(profile_module)
    sys.modules["profile"] = profile_module

from orchestrator import Orchestrator
from profile import PROFILE
from models.schemas import ReferralContact, ReferralReachoutStatus


async def main() -> None:
    orch = Orchestrator()
    report_path = Path("data") / f"external_job_connect_test_{datetime.now():%Y%m%d_%H%M%S}.json"
    report: dict = {
        "job": None,
        "people": [],
        "selected_contact": None,
        "reachout_result": None,
        "error": None,
    }

    try:
        await orch.start()
        if not await orch.auth.login():
            report["error"] = "linkedin_login_failed"
            report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            print(report_path)
            return

        try:
            orch.sheets_exporter._connect()
            orch.sheets_exporter._ensure_header()
            existing_links = orch.sheets_exporter._existing_job_links()
        except Exception:
            existing_links = set()

        selected_job = None
        searched_keywords: list[str] = []
        for keyword in PROFILE.get("job_search_keywords", []):
            searched_keywords.append(keyword)
            jobs = await orch.searcher.search_jobs(
                keyword=keyword,
                location="Remote",
                max_pages=5,
                min_jobs=40,
                fetch_details=False,
            )
            for job in jobs:
                if job.is_easy_apply:
                    continue
                if not job.url or job.url in existing_links:
                    continue
                selected_job = job
                break
            if selected_job:
                break

        report["searched_keywords"] = searched_keywords

        if not selected_job:
            report["error"] = "no_new_external_job_found"
            report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            print(report_path)
            return

        selected_job = await orch.searcher.get_job_details(selected_job)
        orch.sheets_exporter.export_jobs([selected_job])
        report["job"] = selected_job.model_dump(mode="json")

        people = await orch.people_scraper.search_company_people(selected_job.company, limit=5)
        orch.sheets_exporter.update_company_people(selected_job.company, people)
        report["people"] = people

        if not people:
            report["error"] = "no_people_found_for_company"
            report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            print(report_path)
            return

        person = people[0]
        contact = ReferralContact(
            job_url=selected_job.url,
            apply_link=selected_job.apply_link or selected_job.url,
            job_title=selected_job.title,
            company=selected_job.company,
            location=selected_job.location,
            person_index=1,
            person_name=person.get("name", ""),
            person_designation=person.get("designation", ""),
            linkedin_profile_url=person.get("profile_url", ""),
            resume_link=selected_job.resume_link,
        )
        report["selected_contact"] = contact.model_dump(mode="json")

        result = await orch.referral_messenger.reach_out_to_contact(contact)
        report["reachout_result"] = result.model_dump(mode="json")

        connection_status = ""
        connection_requested_at = ""
        message_status = ""
        message_sent_at = ""
        timestamp = result.sent_at.strftime("%Y-%m-%d %H:%M:%S") if result.sent_at else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if result.status == ReferralReachoutStatus.CONNECT_REQUESTED:
            connection_status = "connect_requested"
            connection_requested_at = timestamp
        elif result.status == ReferralReachoutStatus.CONNECTED:
            connection_status = "connected"
        elif result.status == ReferralReachoutStatus.MESSAGE_SENT:
            connection_status = "connected"
            message_status = "message_sent"
            message_sent_at = timestamp

        orch.sheets_exporter.update_referral_contact_outreach(
            selected_job.url,
            1,
            connection_status=connection_status,
            connection_requested_at=connection_requested_at,
            message_status=message_status,
            message_sent_at=message_sent_at,
            notes=result.notes or ("; ".join(result.errors) if result.errors else ""),
        )

        report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(report_path)
    except Exception as exc:
        report["error"] = str(exc)
        report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(report_path)
        raise
    finally:
        try:
            await orch.stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
