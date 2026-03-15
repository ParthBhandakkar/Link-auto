"""
Integration test: Job -> VectorDB -> Similarity -> Clustering flow.
"""
from __future__ import annotations

import pytest

from models.schemas import Job
from utils.vector_db import VectorDBManager
from utils.similarity_engine import SimilarityEngine
from utils.job_clustering import JobClustering


def test_full_flow(temp_vector_db_path) -> None:
    """Add jobs, find similar, cluster — end-to-end."""
    jobs = [
        Job(
            job_id="int-1",
            title="Backend Python Developer",
            company="A",
            description="Python, FastAPI, PostgreSQL, 3+ years.",
        ),
        Job(
            job_id="int-2",
            title="Python API Engineer",
            company="B",
            description="Python, Django, REST, 5 years.",
        ),
        Job(
            job_id="int-3",
            title="Data Scientist",
            company="C",
            description="Python, scikit-learn, pandas, ML.",
        ),
    ]
    vdb = VectorDBManager()
    for j in jobs:
        vdb.add_job(j)

    assert vdb.count() == 3

    engine = SimilarityEngine(vector_db=vdb, min_similarity_threshold=0.15)
    similar = engine.find_similar_jobs_by_id("int-1", top_k=3)
    assert len(similar) >= 1
    assert all(r[0] != "int-1" for r in similar)

    clustering = JobClustering(vector_db=vdb, similarity_threshold=0.6)
    cluster_map = clustering.cluster_jobs()
    assert len(cluster_map) == 3
    assert len(set(cluster_map.values())) >= 1
