"""Data models for the LinkedIn Auto-Apply Bot."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    FOUND = "found"
    APPLYING = "applying"
    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"
    EXTERNAL = "external"


class ApplicationStep(str, Enum):
    LOGIN = "login"
    SEARCH = "search"
    OPEN_JOB = "open_job"
    CLICK_APPLY = "click_apply"
    FILL_FORM = "fill_form"
    SUBMIT = "submit"
    EXTERNAL_APPLY = "external_apply"
    COMPLETE = "complete"
    ERROR = "error"


class Job(BaseModel):
    job_id: str = ""
    title: str = ""
    company: str = ""
    company_link: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    is_easy_apply: bool = False
    is_remote: bool = False
    posted_date: str = ""
    salary: str = ""
    status: JobStatus = JobStatus.FOUND
    applied_at: Optional[datetime] = None
    error_message: str = ""
    search_keyword: str = ""
    # New fields for Google Sheets export
    scraped_timestamp: str = ""
    description_preview: str = ""
    apply_link: str = ""
    apply_method: str = ""
    experience_required: str = ""
    keywords_matched: str = ""
    search_location: str = ""
    company_followers: str = ""
    company_industry: str = ""
    company_description: str = ""
    application_status: str = ""
    resume_link: str = ""
    job_score: int = 0
    application_notes: str = ""
    # Vector DB / similarity fields
    similarity_cluster_id: str = ""
    similar_jobs: str = ""
    similarity_score: str = ""


class ReferralContact(BaseModel):
    job_url: str = ""
    apply_link: str = ""
    job_title: str = ""
    company: str = ""
    location: str = ""
    person_index: int = 0
    person_name: str = ""
    person_designation: str = ""
    linkedin_profile_url: str = ""
    connection_status: str = ""
    connection_requested_at: str = ""
    message_status: str = ""
    message_sent_at: str = ""
    reachout_notes: str = ""
    resume_link: str = ""


class ReferralReachoutStatus(str, Enum):
    MESSAGE_SENT = "message_sent"
    CONNECT_REQUESTED = "connect_requested"
    CONNECTED = "connected"
    SKIPPED = "skipped"
    FAILED = "failed"


class ReferralReachoutResult(BaseModel):
    contact: ReferralContact
    status: ReferralReachoutStatus
    action_taken: str = ""
    message_text: str = ""
    connected_status: str = ""
    notes: str = ""
    errors: list[str] = Field(default_factory=list)
    sent_at: Optional[datetime] = None


class InvestorLead(BaseModel):
    scraped_timestamp: str = ""
    startup_name: str = ""
    investor_name: str = ""
    headline: str = ""
    firm_name: str = ""
    investor_type: str = ""
    location: str = ""
    linkedin_profile_url: str = ""
    search_query: str = ""
    sectors_matched: str = ""
    stages_matched: str = ""
    geography_match: str = ""
    relevance_score: int = 0
    why_fit: str = ""
    source: str = "LinkedIn"
    outreach_status: str = ""
    outreach_action: str = ""
    connected_status: str = ""
    last_contacted_at: str = ""
    message_text: str = ""
    outreach_notes: str = ""


class InvestorOutreachStatus(str, Enum):
    MESSAGE_SENT = "message_sent"
    CONNECT_REQUESTED = "connect_requested"
    CONNECTED = "connected"
    ALREADY_CONTACTED = "already_contacted"
    SKIPPED = "skipped"
    FAILED = "failed"


class InvestorOutreachResult(BaseModel):
    investor: InvestorLead
    status: InvestorOutreachStatus
    action_taken: str = ""
    message_text: str = ""
    connected_status: str = ""
    notes: str = ""
    errors: list[str] = Field(default_factory=list)
    sent_at: Optional[datetime] = None


class FormField(BaseModel):
    """Represents one input field on an application form."""
    field_type: str = ""          # text, textarea, select, radio, checkbox, file
    label: str = ""
    placeholder: str = ""
    name: str = ""
    options: list[str] = Field(default_factory=list)
    is_required: bool = False
    current_value: str = ""
    error_text: str = ""


class FormPage(BaseModel):
    """Represents one page/step of a multi-page application form."""
    page_number: int = 0
    total_pages: int = 0
    fields: list[FormField] = Field(default_factory=list)
    has_next: bool = False
    has_submit: bool = False


class ApplicationResult(BaseModel):
    job: Job
    status: JobStatus
    steps_completed: list[ApplicationStep] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0


class SessionStats(BaseModel):
    total_found: int = 0
    total_applied: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    total_external: int = 0
    session_start: Optional[datetime] = None
    session_end: Optional[datetime] = None
    results: list[ApplicationResult] = Field(default_factory=list)


class BotState(str, Enum):
    IDLE = "idle"
    LOGGING_IN = "logging_in"
    SEARCHING = "searching"
    APPLYING = "applying"
    PAUSED = "paused"
    ERROR = "error"
    STOPPED = "stopped"
