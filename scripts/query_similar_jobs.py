"""
CLI to find similar jobs by job_id or free-text query.

Usage:
  python scripts/query_similar_jobs.py --job-id 4381380387 --top-k 10
  python scripts/query_similar_jobs.py --query "Python ML Engineer" --top-k 5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.vector_db import VectorDBManager
from utils.similarity_engine import SimilarityEngine


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find similar jobs by job ID or free-text query",
    )
    parser.add_argument(
        "--job-id",
        type=str,
        help="LinkedIn job ID (from URL) to find similar jobs",
    )
    parser.add_argument(
        "--query",
        type=str,
        help="Free-text query (e.g. job title or description)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of similar jobs to return (default: 10)",
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.2,
        help="Minimum similarity score 0-1 (default: 0.2)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    args = parser.parse_args()

    if not args.job_id and not args.query:
        parser.error("Provide either --job-id or --query")

    vector_db = VectorDBManager()
    similarity_engine = SimilarityEngine(vector_db=vector_db)

    if args.job_id:
        results = similarity_engine.find_similar_jobs_by_id(
            args.job_id,
            top_k=args.top_k,
            min_similarity=args.min_similarity,
        )
        if args.json:
            out = [
                {
                    "job_id": r[0],
                    "metadata": r[1],
                    "score": round(r[2], 4),
                    "components": {k: round(v, 4) for k, v in r[3].items()},
                }
                for r in results
            ]
            print(json.dumps({"query_type": "job_id", "query": args.job_id, "results": out}))
        else:
            print(f"Similar jobs to job_id={args.job_id} (top {len(results)}):\n")
            for jid, meta, score, comp in results:
                print(f"  {meta.get('title', 'N/A')} @ {meta.get('company', 'N/A')}")
                print(f"    job_id={jid} score={score:.3f} {comp}")
                print()

    else:
        raw = vector_db.find_similar(query_text=args.query, top_k=args.top_k)
        if args.json:
            out = [
                {"job_id": jid, "metadata": meta, "distance": round(dist, 4)}
                for jid, dist, meta in raw
            ]
            print(json.dumps({"query_type": "text", "query": args.query, "results": out}))
        else:
            print(f"Jobs similar to query='{args.query}' (top {len(raw)}):\n")
            for jid, dist, meta in raw:
                print(f"  {meta.get('title', 'N/A')} @ {meta.get('company', 'N/A')}")
                print(f"    job_id={jid} distance={dist:.4f}")
                print()


if __name__ == "__main__":
    main()
