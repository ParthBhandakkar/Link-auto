"""
Vector DB maintenance: rebuild embeddings, cleanup, backup.

Usage:
  python scripts/maintain_vector_db.py --stats
  python scripts/maintain_vector_db.py --backup data/vectors_backup
  python scripts/maintain_vector_db.py --recluster
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import VECTORS_DIR
from utils.vector_db import VectorDBManager
from utils.job_clustering import JobClustering


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Maintain the vector database: stats, backup, recluster",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print vector DB statistics",
    )
    parser.add_argument(
        "--backup",
        type=str,
        metavar="DIR",
        help="Backup vector DB to the given directory",
    )
    parser.add_argument(
        "--recluster",
        action="store_true",
        help="Re-run clustering on all jobs",
    )
    args = parser.parse_args()

    if not any([args.stats, args.backup, args.recluster]):
        parser.print_help()
        return

    db_path = VECTORS_DIR / "chromadb"
    if not db_path.exists():
        print("Vector DB not found. Run a scrape first.")
        return

    if args.stats:
        vdb = VectorDBManager()
        n = vdb.count()
        print(f"Vector DB: {n} jobs")
        print(f"Path: {db_path}")

    if args.backup:
        dest = Path(args.backup)
        dest.mkdir(parents=True, exist_ok=True)
        backup_path = dest / "chromadb_backup"
        if backup_path.exists():
            shutil.rmtree(backup_path)
        shutil.copytree(db_path, backup_path)
        print(f"Backed up to {backup_path}")

    if args.recluster:
        vdb = VectorDBManager()
        clustering = JobClustering(vector_db=vdb)
        ids, embeddings, metadatas = vdb.get_all_for_clustering()
        if not ids:
            print("No jobs to cluster")
            return
        cluster_map = clustering.cluster_jobs(ids=ids, embeddings=embeddings, metadatas=metadatas)
        print(f"Reclustered {len(ids)} jobs into {len(set(cluster_map.values()))} clusters")


if __name__ == "__main__":
    main()
