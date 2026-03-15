"""
Unit tests for JobSimilarity — embedding text and metadata extraction.
"""
from __future__ import annotations

import pytest

from models.schemas import Job
from utils.job_similarity import JobSimilarity


def test_embedding_text_basic() -> None:
    """Embedding text includes title, company, location."""
    job = Job(
        job_id="123",
        title="Python Developer",
        company="Acme Corp",
        location="Remote",
    )
    js = JobSimilarity(job)
    text = js.embedding_text
    assert "Python Developer" in text
    assert "Acme Corp" in text
    assert "Remote" in text


def test_embedding_text_with_description() -> None:
    """Skills and experience extracted from description."""
    job = Job(
        job_id="456",
        title="ML Engineer",
        company="TechCo",
        description="We need 5+ years Python, TensorFlow, PyTorch. Senior level.",
    )
    js = JobSimilarity(job)
    text = js.embedding_text
    assert "ML Engineer" in text
    assert "python" in js.skills or "tensorflow" in js.skills or "pytorch" in js.skills
    assert js.experience or "5" in text


def test_embedding_text_empty_job() -> None:
    """Empty job produces minimal but non-crashing output."""
    job = Job()
    js = JobSimilarity(job)
    text = js.embedding_text
    assert isinstance(text, str)
    assert len(text) >= 0


def test_skills_extraction() -> None:
    """Tech keywords extracted from description."""
    job = Job(
        description="Looking for React, Node.js, AWS, Docker experience.",
    )
    js = JobSimilarity(job)
    skills = js.skills
    assert len(skills) >= 2
    assert any("react" in s or "node" in s or "aws" in s or "docker" in s for s in skills)
