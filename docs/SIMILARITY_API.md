# Similarity API

REST endpoints for job similarity and clustering. Used by the Streamlit dashboard.

## Base URL

`http://localhost:8080` (FastAPI server)

## Endpoints

### GET /jobs/similar

Find similar jobs by job ID or free-text query.

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `job_id` | string | One of job_id/query | LinkedIn job ID from URL |
| `query` | string | One of job_id/query | Free-text search (e.g. job title) |
| `top_k` | int | No | Number of results (default: 10) |

**Example (by job_id):**

```
GET /jobs/similar?job_id=4381380387&top_k=5
```

**Response (job_id):**

```json
{
  "query_type": "job_id",
  "query": "4381380387",
  "count": 5,
  "results": [
    {
      "job_id": "123456",
      "metadata": {
        "title": "Python ML Engineer",
        "company": "Acme",
        "location": "Remote",
        "url": "https://..."
      },
      "score": 0.85,
      "components": {
        "text": 0.9,
        "skills": 0.8,
        "tech_stack": 0.7,
        "experience": 0.9,
        "industry": 0.5
      }
    }
  ]
}
```

**Example (by query):**

```
GET /jobs/similar?query=Python%20ML%20Engineer&top_k=10
```

**Response (query):**

```json
{
  "query_type": "text",
  "query": "Python ML Engineer",
  "count": 10,
  "results": [
    {
      "job_id": "123456",
      "metadata": { "title": "...", "company": "...", ... },
      "distance": 0.25
    }
  ]
}
```

Note: For text queries, `distance` is ChromaDB cosine distance (lower = more similar).

---

### GET /clusters

List all job clusters.

**Response:**

```json
{
  "clusters": [
    {
      "cluster_id": 0,
      "size": 5,
      "job_ids": ["id1", "id2", ...],
      "sample_titles": ["Title 1", "Title 2", ...],
      "companies": ["Co1", "Co2", ...]
    }
  ],
  "total_jobs": 42,
  "cluster_count": 8
}
```

---

### GET /clusters/{cluster_id}

Get jobs in a specific cluster.

**Response:**

```json
{
  "cluster_id": 0,
  "size": 5,
  "jobs": [
    {
      "job_id": "123",
      "title": "...",
      "company": "...",
      "location": "...",
      "url": "..."
    }
  ]
}
```

---

### GET /vector-db/stats

Vector database statistics.

**Response:**

```json
{
  "job_count": 150
}
```
