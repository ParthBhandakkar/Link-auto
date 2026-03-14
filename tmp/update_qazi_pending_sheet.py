from __future__ import annotations

from datetime import datetime

from utils.sheets_exporter import GoogleSheetsExporter


def main() -> None:
    job_url = "https://www.linkedin.com/jobs/view/4382041930/"
    person_index = 1

    exporter = GoogleSheetsExporter()
    ok = exporter.update_referral_contact_outreach(
        job_url,
        person_index,
        connection_status="pending",
        connection_requested_at=datetime.now().isoformat(),
        message_status="",
        message_sent_at="",
        notes="Pending confirmed on LinkedIn profile (More > Connect flow)",
    )

    print({"updated": ok, "job_url": job_url, "person_index": person_index})


if __name__ == "__main__":
    main()
