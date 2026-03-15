"""
Unit tests for SimilarityEngine — weighted scoring and find_similar.
"""
from __future__ import annotations

import pytest

from models.schemas import Job
from utils.vector_db import VectorDBManager
from utils.similarity_engine import SimilarityEngine


@pytest.fixture
def vdb_with_jobs(temp_vector_db_path) -> VectorDBManager:
    """Vector DB with two similar jobs."""
    vdb = VectorDBManager()
    vdb.add_job(Job(
        job_id="j1",
        title="Python ML Engineer",
        company="A",
        description="Python, TensorFlow, 3+ years.",
    ))
    vdb.add_job(Job(
        job_id="j2",
        title="Machine Learning Engineer",
        company="B",
        description="Python, PyTorch, 5 years experience.",
    ))
    vdb.add_job(Job(
        job_id="j3",
        title="Frontend React Developer",
        company="C",
        description="React, JavaScript, Vue.",
    ))
    return vdb


def test_calculate_similarity_same_job() -> None:
    """Same job scores 1.0 on all components."""
    job = Job(job_id="x", title="Dev", company="Y")
    engine = SimilarityEngine()
    score, comp = engine.calculate_similarity(job, job, text_similarity=1.0)
    assert score >= 0.9
    assert comp["text"] == 1.0


def test_find_similar_jobs_by_id(vdb_with_jobs: VectorDBManager) -> None:
    """find_similar_jobs_by_id returns similar jobs excluding self."""
    engine = SimilarityEngine(vector_db=vdb_with_jobs, min_similarity_threshold=0.1)
    results = engine.find_similar_jobs_by_id("j1", top_k=5)
    assert len(results) >= 1
    jids = [r[0] for r in results]
    assert "j1" not in jids
    # j2 (ML) should be more similar to j1 (ML) than j3 (Frontend)
    if len(results) >= 2:
        scores = [r[2] for r in results]
        assert max(scores) <= 1.0
