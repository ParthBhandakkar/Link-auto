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
from utils.vector_db import VectorDBManager
from utils.similarity_engine import SimilarityEngine
from utils.job_clustering import JobClustering

# ── Global orchestrator instance ────────────────────────────────────────
_orchestrator: Orchestrator | None = None
_run_task: asyncio.Task | None = None
_vector_db: VectorDBManager | None = None
_similarity_engine: SimilarityEngine | None = None
_job_clustering: JobClustering | None = None


# ── Lifespan ────────────────────────────────────────────────────────────
def _get_vector_services():
    """Lazily initialize vector DB services for dashboard endpoints."""
    global _vector_db, _similarity_engine, _job_clustering
    if _vector_db is None:
        _vector_db = VectorDBManager()
        _similarity_engine = SimilarityEngine(vector_db=_vector_db)
        _job_clustering = JobClustering(vector_db=_vector_db)
    return _vector_db, _similarity_engine, _job_clustering


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator
    _orchestrator = Orchestrator()
    _get_vector_services()
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
    description="Automated LinkedIn job application bot powered by agent-browser & Kimi K2.5 LLM",
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


# ── Vector DB / Dashboard endpoints ─────────────────────────────────────
def _safe_vector_services():
    """Get vector services; on failure return (None, None, None)."""
    try:
        return _get_vector_services()
    except (ImportError, Exception) as e:
        logger.warning("Vector DB unavailable: {}", e)
        return None, None, None


@app.get("/jobs/similar")
async def get_similar_jobs(
    job_id: str | None = None,
    query: str | None = None,
    top_k: int = 10,
):
    """
    Find similar jobs by job_id or free-text query.
    Returns list of {job_id, metadata, score, components}.
    """
    if not job_id and not query:
        raise HTTPException(
            status_code=400,
            detail="Provide either job_id or query parameter",
        )
    try:
        vector_db, similarity_engine, _ = _get_vector_services()
    except (ImportError, Exception) as e:
        logger.warning("Vector DB unavailable: {}", e)
        return {"count": 0, "results": [], "error": "Vector DB not available. Install: pip install chromadb sentence-transformers"}
    try:
        if job_id:
            results = similarity_engine.find_similar_jobs_by_id(
                job_id, top_k=top_k, min_similarity=0.2
            )
            return {
                "query_type": "job_id",
                "query": job_id,
                "count": len(results),
                "results": [
                    {
                        "job_id": r[0],
                        "metadata": r[1],
                        "score": round(r[2], 4),
                        "components": {k: round(v, 4) for k, v in r[3].items()},
                    }
                    for r in results
                ],
            }
        raw = vector_db.find_similar(query_text=query, top_k=top_k)
        return {
            "query_type": "text",
            "query": query,
            "count": len(raw),
            "results": [
                {
                    "job_id": jid,
                    "metadata": meta,
                    "distance": round(dist, 4),
                }
                for jid, dist, meta in raw
            ],
        }
    except (ImportError, Exception) as e:
        logger.exception("Similar jobs failed")
        return {"count": 0, "results": [], "error": str(e)[:200]}


@app.get("/clusters")
async def get_clusters():
    """List all clusters. Run clustering first if needed."""
    vector_db, _, job_clustering = _safe_vector_services()
    if not vector_db or not job_clustering:
        return {"clusters": [], "total_jobs": 0, "cluster_count": 0, "error": "Vector DB not available"}
    try:
        ids, _, _ = vector_db.get_all_for_clustering()
    except Exception as e:
        logger.exception("Clusters failed")
        return {"clusters": [], "total_jobs": 0, "cluster_count": 0, "error": str(e)[:200]}
    if not ids:
        return {"clusters": [], "total_jobs": 0}
    cluster_map = job_clustering.cluster_jobs()
    cluster_ids = sorted(set(cluster_map.values()))
    clusters = []
    for cid in cluster_ids:
        summary = job_clustering.get_cluster_summary(cid)
        clusters.append(summary)
    return {
        "clusters": clusters,
        "total_jobs": len(ids),
        "cluster_count": len(cluster_ids),
    }


@app.get("/clusters/{cluster_id:int}")
async def get_cluster_jobs(cluster_id: int):
    """Get jobs in a specific cluster."""
    vector_db, _, job_clustering = _safe_vector_services()
    if not vector_db or not job_clustering:
        raise HTTPException(status_code=503, detail="Vector DB not available")
    try:
        ids, _, metadatas = vector_db.get_all_for_clustering()
    except Exception as e:
        logger.exception("Cluster jobs failed")
        raise HTTPException(status_code=503, detail=str(e)[:200])
    if not ids:
        raise HTTPException(status_code=404, detail="No jobs in vector DB")
    cluster_map = job_clustering.cluster_jobs()
    job_ids = job_clustering.get_jobs_in_cluster(cluster_id)
    jobs = []
    for jid in job_ids:
        meta = vector_db.get_job(jid)
        if meta:
            jobs.append({"job_id": jid, **meta})
    return {
        "cluster_id": cluster_id,
        "size": len(jobs),
        "jobs": jobs,
    }


@app.get("/vector-db/stats")
async def get_vector_db_stats():
    """Get vector database statistics."""
    try:
        vector_db, _, _ = _get_vector_services()
        return {"job_count": vector_db.count()}
    except ImportError as e:
        logger.warning("Vector DB unavailable: {}", e)
        return {"job_count": 0, "error": "Vector DB not installed. Run: pip install chromadb sentence-transformers"}
    except Exception as e:
        logger.exception("Vector DB stats failed")
        return {"job_count": 0, "error": str(e)[:200]}
