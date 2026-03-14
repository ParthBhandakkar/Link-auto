from __future__ import annotations

from utils.sheets_exporter import GoogleSheetsExporter


def _v(row: dict, key: str) -> str:
    return str(row.get(key, "") or "").strip()


def main() -> None:
    exporter = GoogleSheetsExporter()
    exporter._connect()
    sheet = exporter._get_external_apply_sheet()
    rows = sheet.get_all_records()

    misaligned = []
    missing_job_link = []
    bad_apply_method = []

    for idx, row in enumerate(rows, start=2):
        job_link = _v(row, "job_link")
        apply_method = _v(row, "apply_method")
        company_link = _v(row, "company_link")

        if ("/jobs/view/" not in job_link) and ("/jobs/view/" in apply_method):
            misaligned.append(idx)

        if "/jobs/view/" not in job_link:
            missing_job_link.append(idx)

        if apply_method and apply_method.lower() not in {"external", "easy apply"}:
            bad_apply_method.append((idx, apply_method[:80]))

    print(
        {
            "rows": len(rows),
            "misaligned_rows": misaligned,
            "missing_job_link_rows": missing_job_link,
            "bad_apply_method_rows": bad_apply_method[:10],
            "company_link_non_empty": sum(1 for r in rows if _v(r, "company_link")),
        }
    )


if __name__ == "__main__":
    main()
