"""
Unit tests for VectorDBManager — add, get, delete, find_similar.
"""
from __future__ import annotations

import pytest

from models.schemas import Job
from utils.vector_db import VectorDBManager


@pytest.fixture
def sample_job() -> Job:
    return Job(
        job_id="test-123",
        title="Python Developer",
        company="TestCorp",
        location="Remote",
        description="Python, Django, PostgreSQL. 3+ years experience.",
    )


def test_add_and_get_job(temp_vector_db_path, sample_job: Job) -> None:
    """Add job and retrieve metadata."""
    vdb = VectorDBManager()
    assert vdb.add_job(sample_job) is True
    meta = vdb.get_job("test-123")
    assert meta is not None
    assert meta.get("title") == "Python Developer"
    assert meta.get("company") == "TestCorp"


def test_add_duplicate_upserts(temp_vector_db_path, sample_job: Job) -> None:
    """Adding same job_id twice upserts (no duplicate)."""
    vdb = VectorDBManager()
    vdb.add_job(sample_job)
    sample_job.title = "Senior Python Developer"
    vdb.add_job(sample_job)
    meta = vdb.get_job("test-123")
    assert meta is not None
    assert meta.get("title") == "Senior Python Developer"


def test_delete_job(temp_vector_db_path, sample_job: Job) -> None:
    """Delete removes job from collection."""
    vdb = VectorDBManager()
    vdb.add_job(sample_job)
    assert vdb.get_job("test-123") is not None
    assert vdb.delete_job("test-123") is True
    assert vdb.get_job("test-123") is None


def test_find_similar_by_query(temp_vector_db_path, sample_job: Job) -> None:
    """find_similar with query_text returns relevant jobs."""
    vdb = VectorDBManager()
    vdb.add_job(sample_job)
    results = vdb.find_similar(query_text="Python developer Django", top_k=5)
    assert len(results) >= 1
    jid, dist, meta = results[0]
    assert jid == "test-123"
    assert meta.get("title") == "Python Developer"


def test_count(temp_vector_db_path, sample_job: Job) -> None:
    """count returns number of jobs."""
    vdb = VectorDBManager()
    assert vdb.count() == 0
    vdb.add_job(sample_job)
    assert vdb.count() == 1
    vdb.add_job(Job(job_id="test-456", title="Other", company="X"))
    assert vdb.count() == 2
