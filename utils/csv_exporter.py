"""
CSV exporter — writes scraped jobs to a local CSV file in the data/ folder.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from config import DATA_DIR

if TYPE_CHECKING:
    from models.schemas import Job

# Same column order as Google Sheets for consistency
CSV_COLUMNS = [
    "scraped_timestamp",
    "job_title",
    "company",
    "location",
    "applied",
    "description_preview",
    "job_link",
    "apply_link",
    "apply_method",
    "experience_required",
    "keywords_matched",
    "search_location",
    "company_followers",
    "company_industry",
    "company_description",
    "full_job_description",
    "application_status",
    "resume_link",
    "job_score",
    "application_notes",
]


def _job_to_dict(job: "Job") -> dict[str, str]:
    """Convert a Job to a dict matching CSV_COLUMNS."""
    return {
        "scraped_timestamp": job.scraped_timestamp,
        "job_title": job.title,
        "company": job.company,
        "location": job.location,
        "applied": "Yes" if job.status and job.status.value == "applied" else "No",
        "description_preview": job.description_preview,
        "job_link": job.url,
        "apply_link": job.apply_link or job.url,
        "apply_method": job.apply_method,
        "experience_required": job.experience_required,
        "keywords_matched": job.keywords_matched,
        "search_location": job.search_location,
        "company_followers": job.company_followers,
        "company_industry": job.company_industry,
        "company_description": job.company_description,
        "full_job_description": job.description,
        "application_status": job.application_status or (job.status.value if job.status else ""),
        "resume_link": job.resume_link,
        "job_score": str(job.job_score),
        "application_notes": job.application_notes,
    }


def _load_existing_links(csv_path: Path) -> set[str]:
    """Return set of job_link values already written to the CSV."""
    links: set[str] = set()
    if not csv_path.exists():
        return links
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                link = row.get("job_link", "").strip()
                if link:
                    links.add(link)
    except Exception as e:
        logger.warning("Could not read existing CSV links: {}", e)
    return links


def export_jobs_to_csv(
    jobs: list["Job"],
    filename: str | None = None,
) -> tuple[Path, int]:
    """
    Append jobs to a CSV file.  Skips jobs whose job_link already exists.

    Parameters
    ----------
    jobs : list[Job]
        The scraped job list.
    filename : str, optional
        Custom CSV filename.  Defaults to ``jobs_YYYYMMDD.csv``.

    Returns
    -------
    (Path, int)
        The CSV file path and the number of new rows written.
    """
    if filename is None:
        filename = f"jobs_{datetime.now():%Y%m%d}.csv"

    csv_path = DATA_DIR / filename

    existing_links = _load_existing_links(csv_path)
    new_jobs = [j for j in jobs if j.url not in existing_links]

    if not new_jobs:
        logger.info("No new jobs to write to CSV (all {} already exist)", len(jobs))
        return csv_path, 0

    file_existed = csv_path.exists()

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_existed:
            writer.writeheader()
        for job in new_jobs:
            writer.writerow(_job_to_dict(job))

    logger.info(
        "Wrote {} new rows to {} (skipped {} duplicates)",
        len(new_jobs), csv_path.name, len(jobs) - len(new_jobs),
    )
    return csv_path, len(new_jobs)
