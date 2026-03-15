"""
Vector Database Manager — ChromaDB-backed storage for job embeddings.

Stores job embeddings for similarity search and clustering. Uses
SentenceTransformer for embedding generation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from config import settings, VECTORS_DIR
from models.schemas import Job
from utils.job_similarity import JobSimilarity


def _get_db_path() -> Path:
    """Resolve vector DB persistence path."""
    if settings.vector_db_path:
        return Path(settings.vector_db_path)
    return VECTORS_DIR / "chromadb"


class VectorDBManager:
    """
    Manages job embeddings in ChromaDB with SentenceTransformer.

    Provides add_job, get_job, delete_job, and find_similar operations.
    """

    COLLECTION_NAME = "jobs"

    def __init__(self) -> None:
        self._client = None
        self._collection = None
        self._model = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Lazily initialize ChromaDB client and embedding model."""
        if self._initialized:
            return
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            from sentence_transformers import SentenceTransformer

            db_path = _get_db_path()
            db_path.mkdir(parents=True, exist_ok=True)

            self._client = chromadb.PersistentClient(
                path=str(db_path),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            model_name = settings.embedding_model or "all-MiniLM-L6-v2"
            self._model = SentenceTransformer(model_name)
            self._collection = self._client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            self._initialized = True
            logger.info(
                "VectorDBManager initialized — path={}, model={}",
                db_path,
                model_name,
            )
        except ImportError as e:
            raise ImportError(
                "Vector DB requires chromadb and sentence-transformers. "
                "Install with: pip install chromadb sentence-transformers"
            ) from e

    def add_job(self, job: Job) -> bool:
        """
        Add a job to the vector database.

        Generates embedding from job text and stores with metadata.
        Returns True on success.
        """
        self._ensure_initialized()
        job_id = job.job_id or job.url or str(hash(job.title + job.company))
        if not job_id:
            logger.warning("Job has no id/url — skipping vector DB add")
            return False

        js = JobSimilarity(job)
        text = js.embedding_text
        if not text.strip():
            logger.warning("Empty embedding text for job {} — skipping", job_id)
            return False

        try:
            embedding = self._model.encode(text, convert_to_numpy=True)
            meta = {
                "title": job.title[:500] if job.title else "",
                "company": job.company[:200] if job.company else "",
                "location": job.location[:200] if job.location else "",
                "url": job.url[:500] if job.url else "",
                "skills": json.dumps(js.skills),
                "experience": js.experience[:100],
                "industry": js.industry[:200],
            }
            self._collection.upsert(
                ids=[job_id],
                embeddings=[embedding.tolist()],
                documents=[text[:10000]],
                metadatas=[meta],
            )
            logger.debug("Added job to vector DB: {}", job_id)
            return True
        except Exception as e:
            logger.error("Failed to add job {} to vector DB: {}", job_id, str(e)[:200])
            return False

    def add_jobs_batch(self, jobs: list[Job]) -> int:
        """Add multiple jobs in batch. Returns count of successfully added jobs."""
        self._ensure_initialized()
        added = 0
        ids: list[str] = []
        embeddings_list: list[list[float]] = []
        documents_list: list[str] = []
        metadatas_list: list[dict[str, Any]] = []

        for job in jobs:
            job_id = job.job_id or job.url or str(hash(job.title + job.company))
            if not job_id:
                continue
            js = JobSimilarity(job)
            text = js.embedding_text
            if not text.strip():
                continue
            try:
                embedding = self._model.encode(text, convert_to_numpy=True)
                meta = {
                    "title": job.title[:500] if job.title else "",
                    "company": job.company[:200] if job.company else "",
                    "location": job.location[:200] if job.location else "",
                    "url": job.url[:500] if job.url else "",
                    "skills": json.dumps(js.skills),
                    "experience": js.experience[:100],
                    "industry": js.industry[:200],
                }
                ids.append(job_id)
                embeddings_list.append(embedding.tolist())
                documents_list.append(text[:10000])
                metadatas_list.append(meta)
                added += 1
            except Exception as e:
                logger.warning("Skip job {}: {}", job_id, str(e)[:100])

        if ids:
            try:
                self._collection.upsert(
                    ids=ids,
                    embeddings=embeddings_list,
                    documents=documents_list,
                    metadatas=metadatas_list,
                )
                logger.info("Batch added {} jobs to vector DB", added)
            except Exception as e:
                logger.error("Batch add failed: {}", str(e)[:200])
                return 0
        return added

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """Retrieve job metadata by ID. Returns None if not found."""
        self._ensure_initialized()
        try:
            result = self._collection.get(ids=[job_id], include=["metadatas"])
            if result and result["ids"]:
                return result["metadatas"][0]
        except Exception as e:
            logger.debug("get_job {} failed: {}", job_id, str(e)[:80])
        return None

    def delete_job(self, job_id: str) -> bool:
        """Remove a job from the vector database. Returns True on success."""
        self._ensure_initialized()
        try:
            self._collection.delete(ids=[job_id])
            return True
        except Exception as e:
            logger.warning("delete_job {} failed: {}", job_id, str(e)[:80])
            return False

    def find_similar(
        self,
        job_id: str | None = None,
        query_text: str | None = None,
        top_k: int = 10,
        exclude_ids: set[str] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """
        Find similar jobs by job_id or query text.

        Returns list of (job_id, distance, metadata). Lower distance = more similar.
        """
        self._ensure_initialized()
        exclude_ids = exclude_ids or set()

        if job_id:
            try:
                result = self._collection.get(
                    ids=[job_id], include=["embeddings", "metadatas"]
                )
                if not result or not result["ids"]:
                    return []
                query_embedding = result["embeddings"][0]
            except Exception:
                return []
        elif query_text:
            query_embedding = self._model.encode(
                query_text, convert_to_numpy=True
            ).tolist()
        else:
            return []

        try:
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k + len(exclude_ids) + 1, 100),
                include=["metadatas", "distances"],
            )
        except Exception as e:
            logger.error("find_similar failed: {}", str(e)[:200])
            return []

        out: list[tuple[str, float, dict[str, Any]]] = []
        ids = results.get("ids", [[]])[0] or []
        distances = results.get("distances", [[]])[0] or []
        metadatas = results.get("metadatas", [[]])[0] or []

        for i, jid in enumerate(ids):
            if jid in exclude_ids or jid == job_id:
                continue
            dist = distances[i] if i < len(distances) else 1.0
            meta = metadatas[i] if i < len(metadatas) else {}
            out.append((jid, float(dist), meta))
            if len(out) >= top_k:
                break
        return out

    def count(self) -> int:
        """Return the number of jobs in the collection."""
        self._ensure_initialized()
        return self._collection.count()

    def get_all_for_clustering(
        self,
    ) -> tuple[list[str], list[list[float]], list[dict[str, Any]]]:
        """
        Get all job IDs, embeddings, and metadatas for clustering.

        Returns (ids, embeddings, metadatas). Use with care on large collections.
        """
        self._ensure_initialized()
        n = self._collection.count()
        if n == 0:
            return [], [], []
        result = self._collection.get(
            include=["embeddings", "metadatas"],
        )
        ids = result.get("ids", [])
        embeddings = result.get("embeddings", [])
        metadatas = result.get("metadatas", [])
        return ids, embeddings or [], metadatas or []

    def update_metadata(self, job_id: str, metadata: dict[str, Any]) -> bool:
        """
        Update metadata for an existing job. Merges with existing metadata.
        Returns True on success.
        """
        self._ensure_initialized()
        try:
            existing = self._collection.get(ids=[job_id], include=["metadatas"])
            if existing and existing.get("metadatas"):
                merged = {**existing["metadatas"][0], **metadata}
            else:
                merged = metadata
            self._collection.update(ids=[job_id], metadatas=[merged])
            return True
        except Exception as e:
            logger.warning("update_metadata {} failed: {}", job_id, str(e)[:80])
            return False
