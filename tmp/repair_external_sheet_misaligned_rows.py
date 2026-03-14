from __future__ import annotations

import re
from typing import Any

from utils.sheets_exporter import GoogleSheetsExporter, _record_to_row, _column_letter


def _get(row: dict[str, Any], key: str) -> str:
    return str(row.get(key, "") or "").strip()


def _looks_job_url(value: str) -> bool:
    return "/jobs/view/" in value


def _looks_company_url(value: str) -> bool:
    return "/company/" in value


def main() -> None:
    exporter = GoogleSheetsExporter()
    exporter._connect()
    sheet = exporter._get_external_apply_sheet()

    headers = sheet.row_values(1)
    records = sheet.get_all_records()

    fixed = 0
    skipped = 0

    for row_idx, row in enumerate(records, start=2):
        job_link = _get(row, "job_link")
        apply_method = _get(row, "apply_method")

        misaligned = (not _looks_job_url(job_link)) and _looks_job_url(apply_method)
        if not misaligned:
            skipped += 1
            continue

        extracted_job_url = apply_method
        m = re.search(r"https?://www\.linkedin\.com/jobs/view/\d+/?", extracted_job_url)
        if m:
            extracted_job_url = m.group(0)

        repaired: dict[str, str] = {h: _get(row, h) for h in headers}

        repaired["job_link"] = extracted_job_url

        existing_apply_link = _get(row, "apply_link")
        repaired["apply_link"] = existing_apply_link if _looks_job_url(existing_apply_link) else extracted_job_url

        shifted_apply_method = _get(row, "keywords_matched")
        if shifted_apply_method.lower() in {"external", "easy apply"}:
            repaired["apply_method"] = shifted_apply_method

        shifted_keywords = _get(row, "search_location")
        if shifted_keywords:
            repaired["keywords_matched"] = shifted_keywords

        shifted_search_loc = _get(row, "company_followers")
        if shifted_search_loc:
            repaired["search_location"] = shifted_search_loc

        shifted_company_link = _get(row, "experience_required")
        current_company_link = _get(row, "company_link")
        if _looks_company_url(shifted_company_link):
            repaired["company_link"] = shifted_company_link
        elif not current_company_link:
            repaired["company_link"] = current_company_link

        if repaired.get("application_status", "").strip() == "":
            candidate_status = _get(row, "job_score")
            if candidate_status.lower() in {"found", "applied", "skipped", "failed"}:
                repaired["application_status"] = candidate_status

        row_values = _record_to_row(repaired, headers)
        end_col = _column_letter(len(headers))
        sheet.update(f"A{row_idx}:{end_col}{row_idx}", [row_values], value_input_option="USER_ENTERED")

        fixed += 1

    print({"fixed_rows": fixed, "skipped_rows": skipped, "total_rows": len(records)})


if __name__ == "__main__":
    main()
