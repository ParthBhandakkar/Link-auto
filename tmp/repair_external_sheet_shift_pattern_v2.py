from __future__ import annotations

from utils.sheets_exporter import GoogleSheetsExporter, _record_to_row, _column_letter


def _s(row: dict, key: str) -> str:
    return str(row.get(key, "") or "").strip()


def main() -> None:
    exporter = GoogleSheetsExporter()
    exporter._connect()
    sheet = exporter._get_external_apply_sheet()
    headers = sheet.row_values(1)
    rows = sheet.get_all_records()

    fixed = 0
    skipped = 0

    for row_idx, row in enumerate(rows, start=2):
        apply_method = _s(row, "apply_method").lower()
        exp = _s(row, "experience_required")
        kw = _s(row, "keywords_matched")

        broken = (
            apply_method in {"external", "easy apply"}
            and "/jobs/view/" in exp
            and kw.lower() in {"external", "easy apply"}
        )
        if not broken:
            skipped += 1
            continue

        rec = {h: _s(row, h) for h in headers}

        # Recover location from wrongly shifted description_preview
        if not rec.get("location", "").strip() and rec.get("description_preview", "").strip():
            rec["location"] = rec["description_preview"].strip()
            rec["description_preview"] = ""

        # Undo the specific right-shift artifacts seen in old rows
        rec["experience_required"] = ""
        rec["keywords_matched"] = rec.get("search_location", "")
        rec["search_location"] = rec.get("company_industry", "")
        rec["company_followers"] = ""
        rec["company_industry"] = ""

        # Normalize status/defaults
        if rec.get("job_score", "").strip().lower() == "found":
            rec["job_score"] = "0"
        if not rec.get("applied", "").strip():
            rec["applied"] = "No"

        row_values = _record_to_row(rec, headers)
        end_col = _column_letter(len(headers))
        sheet.update(f"A{row_idx}:{end_col}{row_idx}", [row_values], value_input_option="USER_ENTERED")
        fixed += 1

    print({"fixed_rows": fixed, "skipped_rows": skipped, "total_rows": len(rows)})


if __name__ == "__main__":
    main()
