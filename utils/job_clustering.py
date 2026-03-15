"""
Job Clustering — group jobs by similarity using Agglomerative or DBSCAN.

Assigns cluster IDs to jobs and provides cluster retrieval and summary.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from loguru import logger
from sklearn.cluster import AgglomerativeClustering, DBSCAN

from utils.vector_db import VectorDBManager


def _cosine_to_distance_matrix(embeddings: list[list[float]]) -> np.ndarray:
    """Convert embeddings to pairwise cosine distance matrix."""
    X = np.array(embeddings, dtype=np.float32)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1e-10
    X_norm = X / norms
    sim = np.dot(X_norm, X_norm.T)
    # Cosine distance = 1 - cosine_similarity
    return 1 - np.clip(sim, -1, 1)


class JobClustering:
    """
    Cluster jobs by embedding similarity.

    Uses Agglomerative Clustering (default) or DBSCAN.
    Assigns cluster_id to each job and stores in vector DB metadata.
    """

    def __init__(
        self,
        vector_db: VectorDBManager | None = None,
        similarity_threshold: float = 0.7,
        min_cluster_size: int = 2,
        algorithm: str = "agglomerative",
    ) -> None:
        self._vector_db = vector_db or VectorDBManager()
        self._similarity_threshold = similarity_threshold
        self._min_cluster_size = min_cluster_size
        self._algorithm = algorithm
        self._cluster_map: dict[str, int] = {}
        self._cluster_summaries: dict[int, dict[str, Any]] = {}

    def cluster_jobs(
        self,
        ids: list[str] | None = None,
        embeddings: list[list[float]] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
    ) -> dict[str, int]:
        """
        Cluster jobs and assign cluster IDs.

        If ids/embeddings/metadatas not provided, fetches all from vector DB.
        Returns mapping of job_id -> cluster_id.
        """
        if ids is None or embeddings is None:
            ids, embeddings, metadatas = self._vector_db.get_all_for_clustering()
        if not ids or not embeddings:
            logger.warning("No jobs to cluster")
            return {}

        X = np.array(embeddings, dtype=np.float32)
        n = len(ids)

        if n < 2:
            self._cluster_map = {ids[0]: 0} if ids else {}
            self._update_vector_db_metadata(ids, [0] if ids else [])
            return self._cluster_map.copy()

        # Distance matrix for clustering
        dist_matrix = _cosine_to_distance_matrix(embeddings)

        if self._algorithm == "dbscan":
            # DBSCAN: eps = max distance to be in same cluster
            # similarity 0.7 -> distance 0.3
            eps = 1.0 - self._similarity_threshold
            clustering = DBSCAN(
                eps=eps,
                min_samples=self._min_cluster_size,
                metric="precomputed",
            )
            labels = clustering.fit_predict(dist_matrix)
        else:
            # Agglomerative: distance_threshold = max distance to merge
            distance_threshold = 1.0 - self._similarity_threshold
            clustering = AgglomerativeClustering(
                n_clusters=None,
                distance_threshold=distance_threshold,
                metric="precomputed",
                linkage="average",
            )
            labels = clustering.fit_predict(dist_matrix)

        # Map -1 (noise in DBSCAN) to unique cluster ids
        unique = sorted(set(labels))
        if -1 in unique:
            unique.remove(-1)
            noise_id = max(unique) + 1 if unique else 0
            for i, l in enumerate(labels):
                if l == -1:
                    labels[i] = noise_id
                    unique.append(noise_id)

        self._cluster_map = {jid: int(labels[i]) for i, jid in enumerate(ids)}
        self._update_vector_db_metadata(ids, list(labels))
        self._build_summaries(ids, list(labels), metadatas or [])
        logger.info(
            "Clustered {} jobs into {} clusters",
            n,
            len(set(self._cluster_map.values())),
        )
        return self._cluster_map.copy()

    def _update_vector_db_metadata(
        self, ids: list[str], labels: list[int]
    ) -> None:
        """Write cluster_id to vector DB metadata for each job."""
        for jid, label in zip(ids, labels):
            self._vector_db.update_metadata(jid, {"cluster_id": str(label)})

    def _build_summaries(
        self,
        ids: list[str],
        labels: list[int],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Build cluster summary statistics."""
        from collections import defaultdict

        by_cluster: dict[int, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for i, jid in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            by_cluster[labels[i]].append((jid, meta))

        self._cluster_summaries = {}
        for cid, items in by_cluster.items():
            titles = [m.get("title", "") for _, m in items if m.get("title")]
            companies = list({m.get("company", "") for _, m in items if m.get("company")})
            self._cluster_summaries[cid] = {
                "cluster_id": cid,
                "size": len(items),
                "job_ids": [x[0] for x in items],
                "sample_titles": titles[:5],
                "companies": companies[:10],
            }

    def get_jobs_in_cluster(self, cluster_id: int) -> list[str]:
        """Return job IDs in the given cluster."""
        return [jid for jid, cid in self._cluster_map.items() if cid == cluster_id]

    def get_cluster_summary(self, cluster_id: int) -> dict[str, Any]:
        """Return summary for a cluster."""
        return self._cluster_summaries.get(cluster_id, {}).copy()

    def get_all_cluster_ids(self) -> list[int]:
        """Return all cluster IDs."""
        return sorted(set(self._cluster_map.values()))

    def get_cluster_for_job(self, job_id: str) -> int | None:
        """Return cluster ID for a job, or None if not clustered."""
        return self._cluster_map.get(job_id)
