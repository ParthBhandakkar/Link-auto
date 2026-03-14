"""Google Sheets exporter -- pushes scraped jobs to a Google Spreadsheet,
reads jobs back for the apply-from-sheet pipeline, and updates status
after applications.

Requires a service-account ``credentials.json`` in the project root and
the target sheet shared with that service-account email.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from config import settings, BASE_DIR

if TYPE_CHECKING:
    from models.schemas import Job

# Column order in the Google Sheet (must match the header row)
BASE_SHEET_COLUMNS = [
    "scraped_timestamp",
    "job_title",
    "company",
    "company_link",
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

PEOPLE_COLUMNS = [
    f"company_person_{idx}_{field}"
    for idx in range(1, 6)
    for field in ("name", "designation", "profile_url")
]

PEOPLE_OUTREACH_COLUMNS = [
    f"company_person_{idx}_{field}"
    for idx in range(1, 6)
    for field in (
        "connection_status",
        "connection_requested_at",
        "message_status",
        "message_sent_at",
        "reachout_notes",
    )
]

SHEET_COLUMNS = BASE_SHEET_COLUMNS + PEOPLE_COLUMNS + PEOPLE_OUTREACH_COLUMNS

EASY_APPLY_SHEET_TITLE = "Easy-apply"
EXTERNAL_APPLY_SHEET_TITLE = "External-apply"

VC_SHEET_COLUMNS = [
    "scraped_timestamp",
    "startup_name",
    "investor_name",
    "headline",
    "firm_name",
    "investor_type",
    "location",
    "linkedin_profile_url",
    "search_query",
    "sectors_matched",
    "stages_matched",
    "geography_match",
    "relevance_score",
    "why_fit",
    "source",
    "outreach_status",
    "outreach_action",
    "connected_status",
    "last_contacted_at",
    "message_text",
    "outreach_notes",
]


def _safe(val) -> str:
    """Return a str, replacing None with empty string."""
    if val is None:
        return ""
    return str(val)


def _job_to_row(job: "Job") -> list[str]:
    """Convert a Job model instance to a flat row matching SHEET_COLUMNS."""
    row = [
        _safe(job.scraped_timestamp),
        _safe(job.title),
        _safe(job.company),
        _safe(getattr(job, "company_link", "")),
        _safe(job.location),
        "Yes" if job.status and job.status.value == "applied" else "No",
        _safe(job.description_preview),
        _safe(job.url),
        _safe(job.apply_link) or _safe(job.url),
        _safe(job.apply_method),
        _safe(job.experience_required),
        _safe(job.keywords_matched),
        _safe(job.search_location),
        _safe(job.company_followers),
        _safe(job.company_industry),
        _safe(job.company_description),
        _safe(job.description),              # full_job_description
        _safe(job.application_status) or (_safe(job.status.value) if job.status else ""),
        _safe(job.resume_link),
        str(job.job_score or 0),
        _safe(job.application_notes),
    ]
    row.extend([""] * (len(PEOPLE_COLUMNS) + len(PEOPLE_OUTREACH_COLUMNS)))
    return row


def _job_to_record(job: "Job") -> dict[str, str]:
    """Convert a Job model to a column-name keyed record."""
    record = {
        "scraped_timestamp": _safe(job.scraped_timestamp),
        "job_title": _safe(job.title),
        "company": _safe(job.company),
        "company_link": _safe(getattr(job, "company_link", "")),
        "location": _safe(job.location),
        "applied": "Yes" if job.status and job.status.value == "applied" else "No",
        "description_preview": _safe(job.description_preview),
        "job_link": _safe(job.url),
        "apply_link": _safe(job.apply_link) or _safe(job.url),
        "apply_method": _safe(job.apply_method),
        "experience_required": _safe(job.experience_required),
        "keywords_matched": _safe(job.keywords_matched),
        "search_location": _safe(job.search_location),
        "company_followers": _safe(job.company_followers),
        "company_industry": _safe(job.company_industry),
        "company_description": _safe(job.company_description),
        "full_job_description": _safe(job.description),
        "application_status": _safe(job.application_status) or (_safe(job.status.value) if job.status else ""),
        "resume_link": _safe(job.resume_link),
        "job_score": str(job.job_score or 0),
        "application_notes": _safe(job.application_notes),
    }

    for col in PEOPLE_COLUMNS + PEOPLE_OUTREACH_COLUMNS:
        record.setdefault(col, "")
    return record


def _record_to_row(record: dict[str, str], columns: list[str]) -> list[str]:
    """Render a row in the order of worksheet headers."""
    return [_safe(record.get(col, "")) for col in columns]


def _investor_to_row(investor) -> list[str]:
    """Convert an InvestorLead instance to a flat row for the VCs worksheet."""
    return [
        _safe(investor.scraped_timestamp),
        _safe(investor.startup_name),
        _safe(investor.investor_name),
        _safe(investor.headline),
        _safe(investor.firm_name),
        _safe(investor.investor_type),
        _safe(investor.location),
        _safe(investor.linkedin_profile_url),
        _safe(investor.search_query),
        _safe(investor.sectors_matched),
        _safe(investor.stages_matched),
        _safe(investor.geography_match),
        str(investor.relevance_score or 0),
        _safe(investor.why_fit),
        _safe(investor.source),
        _safe(investor.outreach_status),
        _safe(investor.outreach_action),
        _safe(investor.connected_status),
        _safe(investor.last_contacted_at),
        _safe(investor.message_text),
        _safe(investor.outreach_notes),
    ]


def _column_letter(col_num: int) -> str:
    """Convert a 1-based column index to Excel/Sheets letters."""
    letters = ""
    while col_num > 0:
        col_num, rem = divmod(col_num - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


class GoogleSheetsExporter:
    """
    Append job rows to a Google Sheet.

    Requires:
      - ``gspread`` and ``google-auth`` installed
      - A service-account JSON key file (path in settings.google_credentials_file)
      - The target sheet shared with the service-account email (Editor role)
    """

    def __init__(self) -> None:
        self._client = None
        self._spreadsheet = None
        self._sheet = None
        self._easy_apply_sheet = None
        self._external_apply_sheet = None

    def _connect(self) -> None:
        """Lazily connect to Google Sheets via service-account credentials."""
        if self._client is not None:
            return

        import gspread
        from google.oauth2.service_account import Credentials

        creds_path = Path(settings.google_credentials_file)
        if not creds_path.is_absolute():
            creds_path = BASE_DIR / creds_path

        if not creds_path.exists():
            raise FileNotFoundError(
                f"Google credentials file not found: {creds_path}\n"
                "Download a service-account JSON key from Google Cloud Console "
                "and place it in the project root."
            )

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(str(creds_path), scopes=scopes)
        self._client = gspread.authorize(creds)

        spreadsheet = self._client.open_by_key(settings.google_sheet_id)
        self._spreadsheet = spreadsheet
        self._sheet = spreadsheet.sheet1
        logger.info("Connected to Google Sheet: {}", spreadsheet.title)

    def _ensure_header_for_sheet(self, sheet, columns: list[str]) -> None:
        """Write the header row if missing and append any required columns."""
        existing = sheet.row_values(1)
        if not existing or existing[0] == "":
            sheet.update("A1", [columns])
            logger.info("Wrote header row to Google Sheet worksheet '{}'", sheet.title)
            return

        missing = [col for col in columns if col not in existing]
        if missing:
            updated = existing + missing
            end_col = _column_letter(len(updated))
            sheet.update(f"A1:{end_col}1", [updated])
            logger.info(
                "Added {} missing Google Sheet columns to worksheet '{}'",
                len(missing),
                sheet.title,
            )

    def _get_or_create_worksheet(self, title: str, columns: list[str]):
        """Return a worksheet by title, creating it when required."""
        self._connect()

        if self._spreadsheet is None:
            raise RuntimeError("Google spreadsheet connection is not initialised")

        try:
            worksheet = self._spreadsheet.worksheet(title)
        except Exception:
            worksheet = self._spreadsheet.add_worksheet(
                title=title,
                rows=max(100, settings.vc_target_results + 10),
                cols=max(20, len(columns) + 5),
            )
            logger.info("Created Google Sheet worksheet: {}", title)

        self._ensure_header_for_sheet(worksheet, columns)
        return worksheet

    def _ensure_header(self) -> None:
        """Write the header row if missing and append any required columns."""
        self._ensure_header_for_sheet(self._sheet, SHEET_COLUMNS)

    def _get_easy_apply_sheet(self):
        if self._easy_apply_sheet is None:
            self._easy_apply_sheet = self._get_or_create_worksheet(EASY_APPLY_SHEET_TITLE, SHEET_COLUMNS)
        return self._easy_apply_sheet

    def _get_external_apply_sheet(self):
        if self._external_apply_sheet is None:
            self._external_apply_sheet = self._get_or_create_worksheet(EXTERNAL_APPLY_SHEET_TITLE, SHEET_COLUMNS)
        return self._external_apply_sheet

    def _get_job_sheets(self):
        self._connect()
        easy_sheet = self._get_easy_apply_sheet()
        external_sheet = self._get_external_apply_sheet()
        return [easy_sheet, external_sheet]

    def _sheet_for_job(self, job: "Job"):
        method = _safe(job.apply_method).strip().lower()
        if method == "easy apply" or job.is_easy_apply:
            return self._get_easy_apply_sheet()
        return self._get_external_apply_sheet()

    def _existing_values_for_sheet(self, sheet, column_name: str, columns: list[str]) -> set[str]:
        """Return the existing values for a specific column in the given worksheet."""
        try:
            col_index = columns.index(column_name) + 1  # 1-based
            values = sheet.col_values(col_index)
            return set(values[1:]) if len(values) > 1 else set()
        except Exception as e:
            logger.warning("Could not read existing '{}' values: {}", column_name, e)
            return set()

    def _existing_job_links(self) -> set[str]:
        """Return the set of job_link values already in the sheet."""
        self._connect()
        links = self._existing_values_for_sheet(self._get_easy_apply_sheet(), "job_link", SHEET_COLUMNS)
        links.update(self._existing_values_for_sheet(self._get_external_apply_sheet(), "job_link", SHEET_COLUMNS))
        return links

    def export_jobs(self, jobs: list["Job"]) -> int:
        """
        Append a batch of jobs to the Google Sheet.
        Skips jobs whose ``url`` (job_link) already exists in the sheet.
        Returns the number of new rows written.
        """
        try:
            self._connect()
            easy_sheet = self._get_easy_apply_sheet()
            external_sheet = self._get_external_apply_sheet()
            self._ensure_header_for_sheet(easy_sheet, SHEET_COLUMNS)
            self._ensure_header_for_sheet(external_sheet, SHEET_COLUMNS)
        except FileNotFoundError as e:
            logger.warning("Skipping Google Sheets export: {}", e)
            return 0
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return 0

        existing = self._existing_values_for_sheet(easy_sheet, "job_link", SHEET_COLUMNS)
        existing.update(self._existing_values_for_sheet(external_sheet, "job_link", SHEET_COLUMNS))

        rows_easy: list[list[str]] = []
        rows_external: list[list[str]] = []
        easy_columns = easy_sheet.row_values(1) or SHEET_COLUMNS
        external_columns = external_sheet.row_values(1) or SHEET_COLUMNS
        for job in jobs:
            if job.url in existing:
                continue
            target = self._sheet_for_job(job)
            record = _job_to_record(job)
            if target.title == EASY_APPLY_SHEET_TITLE:
                rows_easy.append(_record_to_row(record, easy_columns))
            else:
                rows_external.append(_record_to_row(record, external_columns))
            existing.add(job.url)

        if not rows_easy and not rows_external:
            logger.info("No new jobs to write to Google Sheet (all {} already exist)", len(jobs))
            return 0

        written = 0
        try:
            if rows_easy:
                easy_sheet.append_rows(rows_easy, value_input_option="USER_ENTERED")
                written += len(rows_easy)
            if rows_external:
                external_sheet.append_rows(rows_external, value_input_option="USER_ENTERED")
                written += len(rows_external)
            logger.info(
                "Wrote {} new rows to Google Sheet ({} Easy-apply, {} External-apply, skipped {} duplicates)",
                written,
                len(rows_easy),
                len(rows_external),
                len(jobs) - written,
            )
            return written
        except Exception as e:
            logger.error("Failed to write rows to Google Sheet: {}", e)
            return 0

    def export_investors(self, investors: list["InvestorLead"], worksheet_title: str | None = None) -> int:
        """Append investor/VC leads into a dedicated worksheet, skipping duplicates."""
        if TYPE_CHECKING:
            from models.schemas import InvestorLead  # pragma: no cover

        sheet_name = worksheet_title or settings.google_vc_sheet_name

        try:
            worksheet = self._get_or_create_worksheet(sheet_name, VC_SHEET_COLUMNS)
        except FileNotFoundError as e:
            logger.warning("Skipping Google Sheets export: {}", e)
            return 0
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return 0

        existing_urls = self._existing_values_for_sheet(
            worksheet,
            "linkedin_profile_url",
            VC_SHEET_COLUMNS,
        )

        new_rows: list[list[str]] = []
        for investor in investors:
            profile_url = _safe(investor.linkedin_profile_url)
            if profile_url and profile_url in existing_urls:
                continue
            new_rows.append(_investor_to_row(investor))

        if not new_rows:
            logger.info(
                "No new investor leads to write to '{}' worksheet (all {} already exist)",
                sheet_name,
                len(investors),
            )
            return 0

        try:
            worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
            logger.info(
                "Wrote {} new investor rows to '{}' worksheet (skipped {} duplicates)",
                len(new_rows),
                sheet_name,
                len(investors) - len(new_rows),
            )
            return len(new_rows)
        except Exception as e:
            logger.error("Failed to write investor rows to Google Sheet: {}", e)
            return 0

    def read_pending_investors(self, limit: int | None = None, worksheet_title: str | None = None) -> list["InvestorLead"]:
        """Read investors from the VCs worksheet that have not been contacted yet."""
        from models.schemas import InvestorLead

        sheet_name = worksheet_title or settings.google_vc_sheet_name
        try:
            worksheet = self._get_or_create_worksheet(sheet_name, VC_SHEET_COLUMNS)
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return []

        try:
            records = worksheet.get_all_records()
        except Exception as e:
            logger.error("Failed to read investor rows from '{}': {}", sheet_name, e)
            return []

        investors: list[InvestorLead] = []
        completed_statuses = {"message_sent", "connected"}
        for row in records:
            profile_url = str(row.get("linkedin_profile_url", "")).strip()
            if not profile_url:
                continue

            outreach_status = str(row.get("outreach_status", "")).strip().lower()
            if outreach_status in completed_statuses:
                continue

            investor = InvestorLead(
                scraped_timestamp=str(row.get("scraped_timestamp", "")),
                startup_name=str(row.get("startup_name", "")),
                investor_name=str(row.get("investor_name", "")),
                headline=str(row.get("headline", "")),
                firm_name=str(row.get("firm_name", "")),
                investor_type=str(row.get("investor_type", "")),
                location=str(row.get("location", "")),
                linkedin_profile_url=profile_url,
                search_query=str(row.get("search_query", "")),
                sectors_matched=str(row.get("sectors_matched", "")),
                stages_matched=str(row.get("stages_matched", "")),
                geography_match=str(row.get("geography_match", "")),
                relevance_score=int(row.get("relevance_score", 0) or 0),
                why_fit=str(row.get("why_fit", "")),
                source=str(row.get("source", "LinkedIn")),
                outreach_status=str(row.get("outreach_status", "")),
                outreach_action=str(row.get("outreach_action", "")),
                connected_status=str(row.get("connected_status", "")),
                last_contacted_at=str(row.get("last_contacted_at", "")),
                message_text=str(row.get("message_text", "")),
                outreach_notes=str(row.get("outreach_notes", "")),
            )
            investors.append(investor)
            if limit is not None and len(investors) >= limit:
                break

        logger.info("Read {} pending investors from '{}' worksheet", len(investors), sheet_name)
        return investors

    def update_investor_outreach(
        self,
        linkedin_profile_url: str,
        *,
        status: str,
        action: str = "",
        connected_status: str = "",
        contacted_at: str = "",
        message_text: str = "",
        notes: str = "",
        worksheet_title: str | None = None,
    ) -> bool:
        """Update outreach tracking columns for a VC lead identified by profile URL."""
        sheet_name = worksheet_title or settings.google_vc_sheet_name
        try:
            worksheet = self._get_or_create_worksheet(sheet_name, VC_SHEET_COLUMNS)
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return False

        try:
            header = worksheet.row_values(1)
            url_col = header.index("linkedin_profile_url") + 1
            start_col = header.index("outreach_status") + 1
            end_col = header.index("outreach_notes") + 1
            cell = worksheet.find(linkedin_profile_url, in_column=url_col)
            if cell is None:
                logger.warning("Investor profile URL not found in '{}' worksheet: {}", sheet_name, linkedin_profile_url)
                return False

            row_num = cell.row
            values = [[status, action, connected_status, contacted_at, message_text, notes]]
            worksheet.update(
                f"{_column_letter(start_col)}{row_num}:{_column_letter(end_col)}{row_num}",
                values,
                value_input_option="USER_ENTERED",
            )
            logger.info("Updated outreach status for investor row {} in '{}' worksheet", row_num, sheet_name)
            return True
        except Exception as e:
            logger.error("Failed to update investor outreach for {}: {}", linkedin_profile_url[:80], e)
            return False

    def read_company_names(self) -> list[str]:
        """Read unique company names from the Google Sheet in row order."""
        try:
            self._connect()
            sheets = self._get_job_sheets()
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return []

        companies: list[str] = []
        seen: set[str] = set()
        try:
            for sheet in sheets:
                records = sheet.get_all_records()
                for row in records:
                    company = str(row.get("company", "")).strip()
                    key = company.lower()
                    if not company or key in seen:
                        continue
                    seen.add(key)
                    companies.append(company)
        except Exception as e:
            logger.error("Failed to read company names from sheet: {}", e)
            return []

        logger.info("Read {} unique companies from Google Sheet", len(companies))
        return companies

    def read_external_company_names(self, only_missing_people: bool = False) -> list[str]:
        """Read unique company names for rows marked as External apply."""
        try:
            self._connect()
            external_sheet = self._get_external_apply_sheet()
            self._ensure_header_for_sheet(external_sheet, SHEET_COLUMNS)
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return []

        companies: list[str] = []
        seen: set[str] = set()
        try:
            records = external_sheet.get_all_records()
            for row in records:
                company = str(row.get("company", "")).strip()
                if not company:
                    continue
                if only_missing_people:
                    has_people = any(
                        str(row.get(f"company_person_{idx}_profile_url", "")).strip()
                        for idx in range(1, 6)
                    )
                    if has_people:
                        continue
                key = company.lower()
                if key in seen:
                    continue
                seen.add(key)
                companies.append(company)
        except Exception as e:
            logger.error("Failed to read external company names from sheet: {}", e)
            return []

        logger.info("Read {} external companies from Google Sheet", len(companies))
        return companies

    def update_company_people(self, company: str, people: list[dict[str, str]]) -> int:
        """Write up to 5 scraped people to every row matching the given company."""
        try:
            self._connect()
            external_sheet = self._get_external_apply_sheet()
            self._ensure_header_for_sheet(external_sheet, SHEET_COLUMNS)
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return 0

        try:
            header = external_sheet.row_values(1)
            company_col = header.index("company")
        except ValueError:
            logger.error("Company column missing from Google Sheet header")
            return 0

        person_values = [""] * len(PEOPLE_COLUMNS)
        for idx in range(5):
            person = people[idx] if idx < len(people) else {}
            base = idx * 3
            person_values[base] = _safe(person.get("name", ""))
            person_values[base + 1] = _safe(person.get("designation", ""))
            person_values[base + 2] = _safe(person.get("profile_url", ""))

        updated_rows = 0
        all_values = external_sheet.get_all_values()
        start_col = header.index(PEOPLE_COLUMNS[0]) + 1
        end_col = start_col + len(PEOPLE_COLUMNS) - 1
        range_suffix = f"{_column_letter(start_col)}{{row}}:{_column_letter(end_col)}{{row}}"

        for row_num, row in enumerate(all_values[1:], start=2):
            current_company = row[company_col].strip() if company_col < len(row) else ""
            if current_company.lower() != company.strip().lower():
                continue
            external_sheet.update(
                range_suffix.format(row=row_num),
                [person_values],
                value_input_option="USER_ENTERED",
            )
            updated_rows += 1

        logger.info(
            "Updated {} sheet rows with {} scraped people for company '{}'",
            updated_rows,
            min(len(people), 5),
            company,
        )
        return updated_rows

    def read_pending_referral_contacts(self, limit: int | None = None) -> list["ReferralContact"]:
        """Read external-apply job contacts that still need connection/message actions."""
        from models.schemas import ReferralContact

        try:
            self._connect()
            external_sheet = self._get_external_apply_sheet()
            self._ensure_header_for_sheet(external_sheet, SHEET_COLUMNS)
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return []

        try:
            records = external_sheet.get_all_records()
        except Exception as e:
            logger.error("Failed to read sheet rows for referral reachout: {}", e)
            return []

        contacts: list[ReferralContact] = []
        for row in records:
            applied_val = str(row.get("applied", "")).strip().lower()
            if applied_val == "yes":
                continue

            for idx in range(1, 6):
                profile_url = str(row.get(f"company_person_{idx}_profile_url", "")).strip()
                if not profile_url:
                    continue

                message_status = str(row.get(f"company_person_{idx}_message_status", "")).strip().lower()
                if message_status == "message_sent":
                    continue

                contact = ReferralContact(
                    job_url=str(row.get("job_link", "")).strip(),
                    apply_link=str(row.get("apply_link", "")).strip(),
                    job_title=str(row.get("job_title", "")).strip(),
                    company=str(row.get("company", "")).strip(),
                    location=str(row.get("location", "")).strip(),
                    person_index=idx,
                    person_name=str(row.get(f"company_person_{idx}_name", "")).strip(),
                    person_designation=str(row.get(f"company_person_{idx}_designation", "")).strip(),
                    linkedin_profile_url=profile_url,
                    connection_status=str(row.get(f"company_person_{idx}_connection_status", "")).strip(),
                    connection_requested_at=str(row.get(f"company_person_{idx}_connection_requested_at", "")).strip(),
                    message_status=str(row.get(f"company_person_{idx}_message_status", "")).strip(),
                    message_sent_at=str(row.get(f"company_person_{idx}_message_sent_at", "")).strip(),
                    reachout_notes=str(row.get(f"company_person_{idx}_reachout_notes", "")).strip(),
                    resume_link=str(row.get("resume_link", "")).strip(),
                )
                contacts.append(contact)

        priority = {
            "connected": 0,
            "first_degree": 0,
            "pending": 1,
            "requested": 1,
            "connect_requested": 1,
            "": 2,
        }
        contacts.sort(key=lambda c: (priority.get(c.connection_status.lower(), 3), c.company.lower(), c.person_index))
        if limit is not None:
            contacts = contacts[:limit]
        logger.info("Read {} pending referral contacts from Google Sheet", len(contacts))
        return contacts

    def update_referral_contact_outreach(
        self,
        job_url: str,
        person_index: int,
        *,
        connection_status: str = "",
        connection_requested_at: str = "",
        message_status: str = "",
        message_sent_at: str = "",
        notes: str = "",
    ) -> bool:
        """Update referral outreach tracking for one person slot on a job row."""
        try:
            self._connect()
            external_sheet = self._get_external_apply_sheet()
            self._ensure_header_for_sheet(external_sheet, SHEET_COLUMNS)
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return False

        try:
            header = external_sheet.row_values(1)
            link_col = header.index("job_link") + 1
            cell = external_sheet.find(job_url, in_column=link_col)
            if cell is None:
                logger.warning("Job URL not found for referral outreach update: {}", job_url[:80])
                return False

            row_num = cell.row
            fields = {
                f"company_person_{person_index}_connection_status": connection_status,
                f"company_person_{person_index}_connection_requested_at": connection_requested_at,
                f"company_person_{person_index}_message_status": message_status,
                f"company_person_{person_index}_message_sent_at": message_sent_at,
                f"company_person_{person_index}_reachout_notes": notes,
            }
            for column_name, value in fields.items():
                col_num = header.index(column_name) + 1
                external_sheet.update_cell(row_num, col_num, value)

            logger.info(
                "Updated referral outreach columns for row {} person {}",
                row_num,
                person_index,
            )
            return True
        except Exception as e:
            logger.error("Failed to update referral outreach for {}: {}", job_url[:80], e)
            return False

    # ── Read jobs from Google Sheet ─────────────────────────────────────
    def read_unapplied_jobs(self, easy_apply_only: bool = False) -> list["Job"]:
        """
        Read all rows from the Google Sheet where the *applied* column is
        not 'Yes' and *application_status* is not already 'skipped' or 'failed'.
        Returns a list of Job model instances, sorted so Easy Apply jobs
        come first.
        """
        from models.schemas import Job, JobStatus

        try:
            self._connect()
            sheets = self._get_job_sheets()
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return []

        jobs: list[Job] = []
        already_processed = 0
        for sheet in sheets:
            records = sheet.get_all_records()
            if not records:
                continue

            for row in records:
                applied_val = str(row.get("applied", "")).strip().lower()
                if applied_val == "yes":
                    already_processed += 1
                    continue

                # Skip rows we've already applied to or explicitly skipped
                app_status = str(row.get("application_status", "")).strip().lower()
                if app_status == "skipped":
                    already_processed += 1
                    continue
                # Allow retrying "failed" jobs (fixes may have been applied)

                job_link = str(row.get("job_link", "")).strip()
                if not job_link:
                    continue

                apply_method = str(row.get("apply_method", "")).strip()
                is_easy = apply_method.lower() == "easy apply"

                if easy_apply_only and not is_easy:
                    continue

                # Extract job_id from URL
                m = re.search(r"/jobs/view/(\d+)", job_link)
                job_id = m.group(1) if m else ""

                job = Job(
                    job_id=job_id,
                    title=str(row.get("job_title", "")),
                    company=str(row.get("company", "")),
                    company_link=str(row.get("company_link", "")),
                    location=str(row.get("location", "")),
                    url=job_link,
                    description=str(row.get("full_job_description", "")),
                    is_easy_apply=is_easy,
                    status=JobStatus.FOUND,
                    scraped_timestamp=str(row.get("scraped_timestamp", "")),
                    description_preview=str(row.get("description_preview", "")),
                    apply_link=str(row.get("apply_link", "")),
                    apply_method=apply_method,
                    experience_required=str(row.get("experience_required", "")),
                    keywords_matched=str(row.get("keywords_matched", "")),
                    search_keyword=str(row.get("keywords_matched", "")),
                    search_location=str(row.get("search_location", "")),
                    company_followers=str(row.get("company_followers", "")),
                    company_industry=str(row.get("company_industry", "")),
                    company_description=str(row.get("company_description", "")),
                    application_status=str(row.get("application_status", "")),
                    resume_link=str(row.get("resume_link", "")),
                    application_notes=str(row.get("application_notes", "")),
                )
                jobs.append(job)

        if not jobs:
            logger.info("Sheets are empty -- nothing to import")
            return []

        # Sort so Easy Apply jobs come before External
        jobs.sort(key=lambda j: (0 if j.is_easy_apply else 1))

        easy_count = sum(1 for j in jobs if j.is_easy_apply)
        logger.info(
            "Read {} unapplied jobs from sheet ({} Easy Apply, {} External, {} already processed)",
            len(jobs), easy_count, len(jobs) - easy_count, already_processed,
        )
        return jobs

    def update_job_status(self, job_url: str, status: str, notes: str = "") -> bool:
        """
        Update the *applied* and *application_status* columns for a job
        identified by its URL (job_link column).
        Returns True on success.
        """
        try:
            self._connect()
            sheets = self._get_job_sheets()
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return False

        try:
            link_col = SHEET_COLUMNS.index("job_link") + 1  # 1-based
            applied_col = SHEET_COLUMNS.index("applied") + 1
            status_col = SHEET_COLUMNS.index("application_status") + 1
            notes_col = SHEET_COLUMNS.index("application_notes") + 1

            cell = None
            target_sheet = None
            for sheet in sheets:
                found = sheet.find(job_url, in_column=link_col)
                if found is not None:
                    cell = found
                    target_sheet = sheet
                    break
            if cell is None or target_sheet is None:
                logger.warning("Job URL not found in job worksheets: {}", job_url[:80])
                return False

            row_num = cell.row
            is_applied = status.lower() in ("applied", "yes")

            target_sheet.update_cell(row_num, applied_col, "Yes" if is_applied else "No")
            target_sheet.update_cell(row_num, status_col, status)
            if notes:
                target_sheet.update_cell(row_num, notes_col, notes)

            logger.info("Updated sheet row {} -- applied={}, status={}",
                        row_num, is_applied, status)
            return True

        except Exception as e:
            logger.error("Failed to update sheet status for {}: {}", job_url[:60], e)
            return False

    def clear_all_jobs(self) -> bool:
        """Remove all job rows from the sheet while preserving the header row."""
        try:
            self._connect()
            sheets = self._get_job_sheets()
        except Exception as e:
            logger.error("Google Sheets connection error: {}", e)
            return False

        try:
            total_cleared = 0
            for sheet in sheets:
                total_rows = len(sheet.get_all_values())
                if total_rows <= 1:
                    continue
                last_col = _column_letter(len(sheet.row_values(1) or SHEET_COLUMNS))
                sheet.batch_clear([f"A2:{last_col}{total_rows}"])
                total_cleared += total_rows - 1
            if total_cleared == 0:
                logger.info("Google job sheets already empty (header only)")
                return True
            logger.info("Cleared {} job rows from Google job sheets", total_cleared)
            return True
        except Exception as e:
            logger.error("Failed to clear Google Sheet rows: {}", e)
            return False
