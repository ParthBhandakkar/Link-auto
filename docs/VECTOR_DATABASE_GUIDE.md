# Vector Database Guide

The LinkConnect bot uses a vector database to store job embeddings and enable similarity search and clustering. This guide explains the architecture, usage, and maintenance.

## Overview

- **ChromaDB**: Persistent vector store for job embeddings
- **SentenceTransformer**: `all-MiniLM-L6-v2` for embedding generation
- **SimilarityEngine**: Multi-dimensional scoring (text, skills, tech stack, experience, industry)
- **JobClustering**: Agglomerative or DBSCAN clustering for grouping similar jobs

## Architecture

```
Job (scraped) -> JobSimilarity.embedding_text -> SentenceTransformer -> ChromaDB
                                                      |
                                                      v
                                              find_similar / cluster_jobs
```

## Components

### JobSimilarity (`utils/job_similarity.py`)

Creates embedding text from job data:
- Title, company, location, experience, industry
- Extracted skills and tech stack from description
- Salary range if present

### VectorDBManager (`utils/vector_db.py`)

- `add_job(job)` / `add_jobs_batch(jobs)` — Store jobs with embeddings
- `get_job(job_id)` — Retrieve metadata
- `delete_job(job_id)` — Remove a job
- `find_similar(job_id=..., query_text=..., top_k=10)` — Similarity search
- `get_all_for_clustering()` — Fetch all for clustering
- `update_metadata(job_id, metadata)` — Update cluster_id etc.

### SimilarityEngine (`utils/similarity_engine.py`)

Weighted composite score (default):
- Text (embedding): 40%
- Skills: 25%
- Tech stack: 20%
- Experience: 10%
- Industry: 5%

### JobClustering (`utils/job_clustering.py`)

- **Agglomerative** (default): Distance-threshold based
- **DBSCAN**: Density-based, handles noise
- Assigns `cluster_id` to each job in vector DB metadata

## Storage

- **Path**: `data/vectors/chromadb/` (or `VECTOR_DB_PATH` env)
- **Collection**: `jobs`
- Add `data/vectors/` to `.gitignore` — do not commit embeddings

## Configuration

`config/similarity_config.json`:

```json
{
  "weights": {
    "text": 0.40,
    "skills": 0.25,
    "tech_stack": 0.20,
    "experience": 0.10,
    "industry": 0.05
  },
  "min_similarity_threshold": 0.3,
  "clustering": {
    "similarity_threshold": 0.7,
    "min_cluster_size": 2,
    "algorithm": "agglomerative"
  }
}
```

## Integration

The orchestrator automatically:
1. Adds scraped jobs to the vector DB after filtering
2. Runs clustering
3. Populates `similarity_cluster_id`, `similar_jobs`, `similarity_score` on jobs
4. Exports these fields to Google Sheets

## CLI Scripts

| Script | Purpose |
|--------|---------|
| `scripts/query_similar_jobs.py` | Find similar jobs by job_id or free-text query |
| `scripts/analyze_clusters.py` | Cluster statistics and export to JSON |
| `scripts/maintain_vector_db.py` | Stats, backup, recluster |

## Testing

```bash
pytest tests/ -v
```

Tests use a temporary ChromaDB path to avoid polluting the main database.
