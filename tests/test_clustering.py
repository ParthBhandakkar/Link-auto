"""
Unit tests for JobClustering — cluster assignment and summary.
"""
from __future__ import annotations

import pytest

from models.schemas import Job
from utils.vector_db import VectorDBManager
from utils.job_clustering import JobClustering


@pytest.fixture
def vdb_with_jobs(temp_vector_db_path) -> VectorDBManager:
    """Vector DB with several jobs."""
    vdb = VectorDBManager()
    for i, (title, desc) in enumerate([
        ("Python Dev", "Python Django PostgreSQL"),
        ("Python Engineer", "Python Flask AWS"),
        ("React Dev", "React JavaScript TypeScript"),
        ("ML Engineer", "Python TensorFlow ML"),
    ]):
        vdb.add_job(Job(
            job_id=f"job-{i}",
            title=title,
            company=f"Co{i}",
            description=desc,
        ))
    return vdb


def test_cluster_jobs(vdb_with_jobs: VectorDBManager) -> None:
    """cluster_jobs assigns cluster IDs."""
    clustering = JobClustering(vector_db=vdb_with_jobs, similarity_threshold=0.5)
    cluster_map = clustering.cluster_jobs()
    assert len(cluster_map) == 4
    assert all(isinstance(cid, int) for cid in cluster_map.values())


def test_get_jobs_in_cluster(vdb_with_jobs: VectorDBManager) -> None:
    """get_jobs_in_cluster returns job IDs for a cluster."""
    clustering = JobClustering(vector_db=vdb_with_jobs, similarity_threshold=0.5)
    clustering.cluster_jobs()
    cluster_ids = clustering.get_all_cluster_ids()
    assert len(cluster_ids) >= 1
    for cid in cluster_ids:
        jobs = clustering.get_jobs_in_cluster(cid)
        assert len(jobs) >= 1


def test_get_cluster_summary(vdb_with_jobs: VectorDBManager) -> None:
    """get_cluster_summary returns size and sample titles."""
    clustering = JobClustering(vector_db=vdb_with_jobs, similarity_threshold=0.5)
    clustering.cluster_jobs()
    for cid in clustering.get_all_cluster_ids():
        summary = clustering.get_cluster_summary(cid)
        assert "cluster_id" in summary
        assert "size" in summary
        assert "sample_titles" in summary
        assert summary["cluster_id"] == cid
        assert summary["size"] >= 1
