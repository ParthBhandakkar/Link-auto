"""
FastAPI Server — provides HTTP API to control the LinkedIn auto-apply bot.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from config import settings
from orchestrator import Orchestrator

# ── Global orchestrator instance ────────────────────────────────────────
_orchestrator: Orchestrator | None = None
_run_task: asyncio.Task | None = None


# ── Lifespan ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator
    _orchestrator = Orchestrator()
    logger.info("Server started on {}:{}", settings.server_host, settings.server_port)
    yield
    if _orchestrator:
        try:
            await _orchestrator.stop()
        except Exception:
            pass
    logger.info("Server shut down.")


# ── App ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="LinkedIn Auto-Apply Bot",
    version="1.0.0",
    description="Automated LinkedIn job application bot powered by Playwright & Kimi K2.5 LLM",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ──────────────────────────────────────────────────────
class RunConfig(BaseModel):
    max_applications: int | None = None
    keywords: list[str] | None = None
    headless: bool | None = None


# ── Endpoints ───────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "name": "LinkedIn Auto-Apply Bot",
        "version": "1.0.0",
        "status": _orchestrator.state.value if _orchestrator else "not_initialized",
    }


@app.get("/status")
async def get_status():
    """Get the current bot status and session statistics."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    return _orchestrator.get_status()


@app.post("/run")
async def run_pipeline(config: RunConfig | None = None):
    """
    Start the full job-application pipeline.
    Runs in the background so the API returns immediately.
    """
    global _run_task

    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Bot not initialized")

    if _run_task and not _run_task.done():
        raise HTTPException(status_code=409, detail="Pipeline already running")

    # Apply optional overrides
    if config:
        if config.max_applications is not None:
            settings.max_applications_per_session = config.max_applications
        if config.headless is not None:
            settings.headless = config.headless

    # Launch in background
    _run_task = asyncio.create_task(_run_pipeline_task())

    return {
        "message": "Pipeline started",
        "max_applications": settings.max_applications_per_session,
    }


async def _run_pipeline_task():
    """Background task wrapper."""
    global _orchestrator
    try:
        _orchestrator = Orchestrator()
        stats = await _orchestrator.run_full_pipeline()
        logger.info("Pipeline completed: {} applied", stats.total_applied)
    except Exception as e:
        logger.error("Pipeline crashed: {}", e)


@app.post("/scrape")
async def scrape_jobs(config: RunConfig | None = None):
    """
    Scrape-only mode: search → collect details → export to Google Sheets + CSV.
    Does NOT apply to any jobs. Runs in the background.
    """
    global _run_task

    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Bot not initialized")

    if _run_task and not _run_task.done():
        raise HTTPException(status_code=409, detail="A pipeline is already running")

    if config and config.headless is not None:
        settings.headless = config.headless

    _run_task = asyncio.create_task(_run_scrape_task())

    return {"message": "Scrape pipeline started"}


async def _run_scrape_task():
    """Background task wrapper for scrape-only pipeline."""
    global _orchestrator
    try:
        _orchestrator = Orchestrator()
        jobs = await _orchestrator.run_scrape_pipeline()
        logger.info("Scrape pipeline completed: {} jobs collected", len(jobs))
    except Exception as e:
        logger.error("Scrape pipeline crashed: {}", e)


@app.post("/stop")
async def stop_pipeline():
    """Request the pipeline to stop after the current job."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    _orchestrator.request_stop()
    return {"message": "Stop requested"}


@app.post("/pause")
async def pause_pipeline():
    """Pause the pipeline."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    _orchestrator.request_pause()
    return {"message": "Paused"}


@app.post("/resume")
async def resume_pipeline():
    """Resume the pipeline after pause."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Bot not initialized")
    _orchestrator.request_resume()
    return {"message": "Resumed"}


@app.get("/results")
async def get_results():
    """Get the list of application results from the current/last session."""
    if not _orchestrator:
        raise HTTPException(status_code=503, detail="Bot not initialized")

    results = []
    for r in _orchestrator.stats.results:
        results.append({
            "title": r.job.title,
            "company": r.job.company,
            "location": r.job.location,
            "url": r.job.url,
            "status": r.status.value,
            "errors": r.errors,
            "duration": r.duration_seconds,
        })
    return {"count": len(results), "results": results}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
