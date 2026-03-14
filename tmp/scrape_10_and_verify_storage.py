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
from utils.sheets_exporter import EASY_APPLY_SHEET_TITLE, EXTERNAL_APPLY_SHEET_TITLE


TARGET_COUNT = 10


async def main() -> None:
    report = {
        "started_at": datetime.now().isoformat(),
        "target_count": TARGET_COUNT,
        "selected_jobs": [],
        "exported_count": 0,
        "verification": {
            "easy_apply_rows": 0,
            "external_apply_rows": 0,
            "missing_rows": [],
            "missing_company_link": [],
        },
        "errors": [],
    }

    orch = Orchestrator()
    out_path = PROJECT_ROOT / "data" / f"scrape10_verify_{datetime.now():%Y%m%d_%H%M%S}.json"

    try:
        await orch.start()
        if not await orch.auth.login():
            report["errors"].append("linkedin_login_failed")
            return

        existing_links = orch.sheets_exporter._existing_job_links()
        selected = []
        seen_urls = set(existing_links)

        keywords = PROFILE.get("job_search_keywords", [])
        if not keywords:
            report["errors"].append("no_keywords_in_profile")
            return

        for keyword in keywords:
            if len(selected) >= TARGET_COUNT:
                break
            jobs = await orch.searcher.search_jobs(
                keyword=keyword,
                location="Remote",
                max_pages=5,
                min_jobs=40,
                fetch_details=False,
            )
            for job in jobs:
                if len(selected) >= TARGET_COUNT:
                    break
                if not job.url or job.url in seen_urls:
                    continue
                seen_urls.add(job.url)
                detailed = await orch.searcher.get_job_details(job)
                selected.append(detailed)

        report["selected_jobs"] = [
            {
                "title": j.title,
                "company": j.company,
                "url": j.url,
                "apply_method": j.apply_method,
                "company_link": getattr(j, "company_link", ""),
            }
            for j in selected
        ]

        if not selected:
            report["errors"].append("no_new_jobs_found")
            return

        exported = orch.sheets_exporter.export_jobs(selected)
        report["exported_count"] = exported

        easy_sheet = orch.sheets_exporter._get_easy_apply_sheet()
        external_sheet = orch.sheets_exporter._get_external_apply_sheet()

        easy_records = easy_sheet.get_all_records()
        external_records = external_sheet.get_all_records()

        by_url_easy = {str(r.get("job_link", "")).strip(): r for r in easy_records}
        by_url_external = {str(r.get("job_link", "")).strip(): r for r in external_records}

        easy_rows = 0
        external_rows = 0

        for job in selected:
            row = None
            expected_easy = bool(job.is_easy_apply or str(job.apply_method).strip().lower() == "easy apply")
            if expected_easy:
                row = by_url_easy.get(job.url)
                if row:
                    easy_rows += 1
            else:
                row = by_url_external.get(job.url)
                if row:
                    external_rows += 1

            if not row:
                report["verification"]["missing_rows"].append(
                    {"url": job.url, "expected_sheet": EASY_APPLY_SHEET_TITLE if expected_easy else EXTERNAL_APPLY_SHEET_TITLE}
                )
                continue

            company_link = str(row.get("company_link", "")).strip()
            if not company_link:
                report["verification"]["missing_company_link"].append(
                    {"url": job.url, "sheet": EASY_APPLY_SHEET_TITLE if expected_easy else EXTERNAL_APPLY_SHEET_TITLE}
                )

        report["verification"]["easy_apply_rows"] = easy_rows
        report["verification"]["external_apply_rows"] = external_rows

    except Exception as e:
        report["errors"].append(str(e))
    finally:
        report["finished_at"] = datetime.now().isoformat()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(out_path)
        await orch.stop()


if __name__ == "__main__":
    asyncio.run(main())
