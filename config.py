"""
LinkedIn Auto-Apply Bot — Configuration Management
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings
from pydantic import Field

# ─── Paths ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RESUME_DIR = DATA_DIR / "resume"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
VECTORS_DIR = DATA_DIR / "vectors"
LOG_DIR = BASE_DIR / "logs"
BROWSER_DATA_DIR = BASE_DIR / "browser_data"

# Make sure all required dirs exist
for d in (DATA_DIR, RESUME_DIR, SCREENSHOT_DIR, VECTORS_DIR, LOG_DIR, BROWSER_DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Loads settings from .env automatically."""

    # ── LinkedIn ────────────────────────────────────────────────────────
    linkedin_email: str = ""
    linkedin_password: str = ""

    # ── LLM ─────────────────────────────────────────────────────────────
    llm_provider: Literal["kimi", "ollama", "openai"] = "kimi"

    kimi_api_key: str = ""
    kimi_base_url: str = "https://api.moonshot.cn/v1"
    kimi_model: str = "kimi-k2.5"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "kimi-k2.5:cloud"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    # ── Server ──────────────────────────────────────────────────────────
    server_host: str = "0.0.0.0"
    server_port: int = 8081
    dashboard_port: int = 8502

    # ── Browser ─────────────────────────────────────────────────────────
    headless: bool = False
    slow_mo: int = 50
    browser_timeout: int = 60_000
    # LinkedIn Jobs two-pane layout needs vertical room; small viewports look “cut off” vs the OS window.
    browser_viewport_width: int = Field(default=1600, ge=800, le=3840)
    browser_viewport_height: int = Field(default=1024, ge=600, le=2160)

    # ── Job Search ──────────────────────────────────────────────────────
    max_applications_per_session: int = 50
    search_delay_min: int = 3
    search_delay_max: int = 8
    application_delay_min: int = 5
    application_delay_max: int = 15
    min_jobs_per_keyword: int = 5

    # ── Google Sheets ───────────────────────────────────────────────────
    google_sheet_id: str = "15GN43sNJi_l2ExORjz60mB4qcDxXRW_kOYuDciUtV-s"
    google_credentials_file: str = "credentials.json"
    google_vc_sheet_name: str = "VCs"

    # ── Outreach / Investor Discovery ──────────────────────────────────
    vc_target_results: int = 60
    vc_reachout_batch_size: int = 10

    # ── Vector Database ────────────────────────────────────────────────
    vector_db_path: str = ""
    embedding_model: str = "all-MiniLM-L6-v2"

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"


settings = Settings()
