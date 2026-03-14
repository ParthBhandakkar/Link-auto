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
from models.schemas import ReferralContact


async def main() -> None:
    orch = Orchestrator()
    report_path = Path("data") / f"manual_referral_test_{datetime.now():%Y%m%d_%H%M%S}.json"
    payload: dict = {
        "selected_contact": None,
        "result": None,
        "error": None,
    }

    try:
        orch.sheets_exporter._connect()
        orch.sheets_exporter._ensure_header()
        records = orch.sheets_exporter._sheet.get_all_records()

        selected = None
        for row in records:
            profile_url = str(row.get("company_person_1_profile_url", "")).strip()
            if not profile_url:
                continue
            selected = ReferralContact(
                job_url=str(row.get("job_link", "")).strip(),
                apply_link=str(row.get("apply_link", "")).strip(),
                job_title=str(row.get("job_title", "")).strip(),
                company=str(row.get("company", "")).strip(),
                location=str(row.get("location", "")).strip(),
                person_index=1,
                person_name=str(row.get("company_person_1_name", "")).strip(),
                person_designation=str(row.get("company_person_1_designation", "")).strip(),
                linkedin_profile_url=profile_url,
                connection_status=str(row.get("company_person_1_connection_status", "")).strip(),
                connection_requested_at=str(row.get("company_person_1_connection_requested_at", "")).strip(),
                message_status=str(row.get("company_person_1_message_status", "")).strip(),
                message_sent_at=str(row.get("company_person_1_message_sent_at", "")).strip(),
                reachout_notes=str(row.get("company_person_1_reachout_notes", "")).strip(),
                resume_link=str(row.get("resume_link", "")).strip(),
            )
            break

        if not selected:
            payload["error"] = "No sheet row with employee contact data was found"
            report_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            print(report_path)
            return

        payload["selected_contact"] = selected.model_dump()

        await orch.start()
        logged_in = await orch.auth.login()
        if not logged_in:
            payload["error"] = "LinkedIn login failed"
            report_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            print(report_path)
            return

        result = await orch.referral_messenger.reach_out_to_contact(selected)
        payload["result"] = result.model_dump(mode="json")
        report_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(report_path)
    except Exception as exc:
        payload["error"] = str(exc)
        report_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(report_path)
        raise
    finally:
        try:
            await orch.stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
