"""
Cluster statistics and export for job similarity clusters.

Usage:
  python scripts/analyze_clusters.py
  python scripts/analyze_clusters.py --export clusters.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.vector_db import VectorDBManager
from utils.job_clustering import JobClustering


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze job clusters and optionally export to JSON",
    )
    parser.add_argument(
        "--export",
        type=str,
        help="Export cluster summary to this JSON file",
    )
    parser.add_argument(
        "--algorithm",
        choices=["agglomerative", "dbscan"],
        default="agglomerative",
        help="Clustering algorithm (default: agglomerative)",
    )
    args = parser.parse_args()

    vector_db = VectorDBManager()
    clustering = JobClustering(vector_db=vector_db, algorithm=args.algorithm)

    ids, embeddings, metadatas = vector_db.get_all_for_clustering()
    if not ids:
        print("No jobs in vector DB. Run a scrape first.")
        return

    cluster_map = clustering.cluster_jobs(ids=ids, embeddings=embeddings, metadatas=metadatas)
    cluster_ids = sorted(set(cluster_map.values()))
    clusters = []
    for cid in cluster_ids:
        summary = clustering.get_cluster_summary(cid)
        clusters.append(summary)

    summary = {
        "total_jobs": len(ids),
        "cluster_count": len(cluster_ids),
        "clusters": clusters,
    }

    print(f"Total jobs: {summary['total_jobs']}")
    print(f"Clusters: {summary['cluster_count']}")
    print()
    for c in clusters:
        print(f"  Cluster {c['cluster_id']}: {c['size']} jobs")
        print(f"    Sample: {c.get('sample_titles', [])[:3]}")
        print(f"    Companies: {c.get('companies', [])[:5]}")

    if args.export:
        out_path = Path(args.export)
        out_path.write_text(json.dumps(summary, indent=2, default=str))
        print(f"\nExported to {out_path}")


if __name__ == "__main__":
    main()
