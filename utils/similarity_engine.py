"""
Similarity Calculation Engine — multi-dimensional job similarity scoring.

Combines text (embedding) similarity, skills overlap, tech stack, experience,
and industry into a weighted composite score.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from models.schemas import Job
from utils.job_similarity import JobSimilarity
from utils.vector_db import VectorDBManager


# Default weights: text 40%, skills 25%, tech 20%, experience 10%, industry 5%
DEFAULT_WEIGHTS = {
    "text": 0.40,
    "skills": 0.25,
    "tech_stack": 0.20,
    "experience": 0.10,
    "industry": 0.05,
}


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _normalize_distance_to_similarity(distance: float) -> float:
    """
    Convert ChromaDB cosine distance to similarity (0-1).

    Cosine distance = 1 - cosine_similarity, so similarity = 1 - distance.
    Clamp to [0, 1].
    """
    sim = 1.0 - distance
    return max(0.0, min(1.0, sim))


def _experience_level_match(exp1: str, exp2: str) -> float:
    """Score experience level compatibility (0-1)."""
    if not exp1 or not exp2:
        return 0.5  # Unknown matches unknown
    e1 = exp1.lower()
    e2 = exp2.lower()
    levels = ["entry", "junior", "mid", "senior", "lead", "principal", "staff"]
    idx1 = next((i for i, l in enumerate(levels) if l in e1), -1)
    idx2 = next((i for i, l in enumerate(levels) if l in e2), -1)
    if idx1 < 0 or idx2 < 0:
        return 0.7  # Partial match
    diff = abs(idx1 - idx2)
    if diff == 0:
        return 1.0
    if diff == 1:
        return 0.8
    if diff == 2:
        return 0.5
    return 0.2


class SimilarityEngine:
    """
    Multi-dimensional similarity scoring for jobs.

    Uses VectorDBManager for text similarity and JobSimilarity for
    skills, tech stack, experience, and industry.
    """

    def __init__(
        self,
        vector_db: VectorDBManager | None = None,
        weights: dict[str, float] | None = None,
        min_similarity_threshold: float = 0.3,
    ) -> None:
        self._vector_db = vector_db or VectorDBManager()
        self._weights = weights or DEFAULT_WEIGHTS.copy()
        self._min_threshold = min_similarity_threshold

    def calculate_similarity(
        self,
        job1: Job,
        job2: Job,
        text_similarity: float | None = None,
    ) -> tuple[float, dict[str, float]]:
        """
        Calculate composite similarity between two jobs.

        Returns (total_score, component_scores).
        If text_similarity is provided (e.g. from vector DB), use it;
        otherwise text component is 0.5 (neutral).
        """
        js1 = JobSimilarity(job1)
        js2 = JobSimilarity(job2)

        skills_sim = _jaccard_similarity(
            set(js1.skills),
            set(js2.skills),
        )
        tech_sim = _jaccard_similarity(
            set(js1.tech_stack),
            set(js2.tech_stack),
        )
        exp_sim = _experience_level_match(js1.experience, js2.experience)

        industry_sim = 0.5
        if js1.industry and js2.industry:
            ind1 = set(js1.industry.lower().split())
            ind2 = set(js2.industry.lower().split())
            if ind1 and ind2:
                industry_sim = _jaccard_similarity(ind1, ind2)

        text_sim = text_similarity if text_similarity is not None else 0.5

        components = {
            "text": text_sim,
            "skills": skills_sim,
            "tech_stack": tech_sim,
            "experience": exp_sim,
            "industry": industry_sim,
        }

        total = sum(
            self._weights.get(k, 0) * v for k, v in components.items()
        )
        return total, components

    def find_similar_jobs(
        self,
        job: Job,
        top_k: int = 10,
        min_similarity: float | None = None,
    ) -> list[tuple[Job, float, dict[str, float]]]:
        """
        Find jobs similar to the given job.

        Returns list of (job, score, components). Requires jobs to be
        in the vector DB. Returns metadata only (not full Job objects)
        since vector DB stores metadata.
        """
        threshold = min_similarity if min_similarity is not None else self._min_threshold
        job_id = job.job_id or job.url
        if not job_id:
            logger.warning("Job has no id/url for similarity search")
            return []

        results = self._vector_db.find_similar(
            job_id=job_id,
            top_k=top_k,
            exclude_ids={job_id},
        )

        out: list[tuple[Job, float, dict[str, float]]] = []
        js_ref = JobSimilarity(job)

        for jid, distance, meta in results:
            text_sim = _normalize_distance_to_similarity(distance)
            # Build minimal Job for comparison
            other = Job(
                job_id=jid,
                title=meta.get("title", ""),
                company=meta.get("company", ""),
                location=meta.get("location", ""),
                url=meta.get("url", ""),
                experience_required=meta.get("experience", ""),
                company_industry=meta.get("industry", ""),
            )
            score, components = self.calculate_similarity(
                job, other, text_similarity=text_sim
            )
            if score >= threshold:
                out.append((other, score, components))
            if len(out) >= top_k:
                break

        return out

    def find_similar_jobs_by_id(
        self,
        job_id: str,
        top_k: int = 10,
        min_similarity: float | None = None,
    ) -> list[tuple[str, dict[str, Any], float, dict[str, float]]]:
        """
        Find similar jobs by job ID (must exist in vector DB).

        Returns list of (job_id, metadata, score, components).
        """
        threshold = min_similarity if min_similarity is not None else self._min_threshold
        meta_ref = self._vector_db.get_job(job_id)
        if not meta_ref:
            logger.warning("Job {} not found in vector DB", job_id)
            return []

        job_ref = Job(
            job_id=job_id,
            title=meta_ref.get("title", ""),
            company=meta_ref.get("company", ""),
            location=meta_ref.get("location", ""),
            url=meta_ref.get("url", ""),
            experience_required=meta_ref.get("experience", ""),
            company_industry=meta_ref.get("industry", ""),
        )

        results = self._vector_db.find_similar(
            job_id=job_id,
            top_k=top_k,
            exclude_ids={job_id},
        )

        out: list[tuple[str, dict[str, Any], float, dict[str, float]]] = []
        for jid, distance, meta in results:
            text_sim = _normalize_distance_to_similarity(distance)
            other = Job(
                job_id=jid,
                title=meta.get("title", ""),
                company=meta.get("company", ""),
                location=meta.get("location", ""),
                url=meta.get("url", ""),
                experience_required=meta.get("experience", ""),
                company_industry=meta.get("industry", ""),
            )
            score, components = self.calculate_similarity(
                job_ref, other, text_similarity=text_sim
            )
            if score >= threshold:
                out.append((jid, meta, score, components))
            if len(out) >= top_k:
                break

        return out
