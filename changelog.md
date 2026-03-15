# Changelog

## 15-Mar-2026 (ports)

- `python main.py --dashboard` now auto-starts API server in background; single command
- Changed default API server port 8080 -> 8081
- Changed default dashboard port 8501 -> 8502
- Added DASHBOARD_PORT and --dashboard-port for dashboard
- Dashboard passes DASHBOARD_API_URL to match server port

## 15-Mar-2026 (later)

- agent-browser setup: installed via npm in project (node_modules/agent-browser)
- Removed separate daemon startup; native agent-browser CLI handles persistence
- Run `npx agent-browser install` to download Chrome (first time)
- Added node_modules/ to .gitignore

## 15-Mar-2026 12:40:00 IST

- Dashboard and Vector DB implementation (LKC-41 through LKC-50)
- Added ChromaDB + SentenceTransformer for job embeddings
- Added JobSimilarity, SimilarityEngine, JobClustering
- Integrated vector DB with orchestrator and Google Sheets
- Added FastAPI endpoints: /jobs/similar, /clusters, /vector-db/stats
- Added Streamlit dashboard (python main.py --dashboard)
- Added scripts: query_similar_jobs.py, analyze_clusters.py, maintain_vector_db.py
- Added config/similarity_config.json
- Added tests: test_job_similarity, test_vector_db, test_similarity_engine, test_clustering, test_integration
- Added docs: VECTOR_DATABASE_GUIDE.md, SIMILARITY_API.md
