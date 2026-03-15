# LinkedIn Auto-Apply Bot

Automated LinkedIn job application bot powered by **agent-browser** (mouse/keyboard control) and **Kimi K2.5 LLM** for intelligent form filling.

## Features

- **Fully Automated**: Searches LinkedIn jobs, fills forms, and submits applications without human intervention
- **LLM-Powered Form Filling**: Uses Kimi K2.5 to intelligently analyze form fields and generate appropriate answers
- **Screenshot Error Recovery**: Takes screenshots of errors, sends to LLM for analysis, and auto-fixes
- **Human-Like Behavior**: Random delays, mouse movements, and typing patterns to avoid detection
- **Easy Apply + External Apply**: Handles both LinkedIn Easy Apply and external application redirects
- **Job Relevance Filtering**: Uses LLM to evaluate if a job matches your profile before applying
- **Cover Letter Generation**: Auto-generates tailored cover letters when required
- **Session Persistence**: Browser data is saved so you stay logged in across sessions
- **FastAPI Server**: REST API for starting, pausing, stopping, and monitoring the bot
- **Streamlit Dashboard**: Operate the bot, view status, job similarity, and clusters
- **Vector Database**: ChromaDB + SentenceTransformer for job similarity and clustering
- **Detailed Reporting**: JSON reports of every application with status, errors, and timing

## Architecture

```
auto_apply/
├── main.py              # Entry point (server or direct run)
├── server.py            # FastAPI REST API
├── orchestrator.py      # Main pipeline controller
├── config.py            # Configuration management
├── profile.py           # Your personal/professional details
├── browser/
│   ├── engine.py        # Agent-browser wrapper with mouse/keyboard helpers
├── linkedin/
│   ├── auth.py          # Login & session management
│   ├── search.py        # Job search & listing extraction
│   ├── apply.py         # Easy Apply & external application flow
│   └── form_filler.py   # LLM-powered form analysis & filling
├── llm/
│   ├── client.py        # Unified LLM client (Kimi/Ollama/OpenAI)
│   └── prompts.py       # Prompt templates for all LLM tasks
├── models/
│   └── schemas.py       # Pydantic data models
├── utils/
│   ├── logger.py        # Loguru logging setup
│   ├── helpers.py       # Utility functions
│   ├── vector_db.py     # ChromaDB job embeddings
│   ├── job_similarity.py # Embedding text generator
│   ├── similarity_engine.py # Multi-dimensional scoring
│   └── job_clustering.py   # Job clustering (Agglomerative/DBSCAN)
├── dashboard/
│   └── app.py           # Streamlit dashboard
├── scripts/             # CLI: query_similar_jobs, analyze_clusters, maintain_vector_db
└── data/
    ├── resume/          # Your resume file(s)
    └── screenshots/     # Error/debug screenshots
```

## Quick Start

**TL;DR:** Activate venv, run `python main.py --dashboard`, open http://localhost:8502, click "Run Full Pipeline".

### 1. Install Dependencies

**Python:**
```bash
cd Link-auto
python -m venv venv
venv\Scripts\activate   # Windows
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**agent-browser (browser automation):**
```bash
npm install
npx agent-browser install   # Downloads Chrome (first time only)
```

**Vector DB (optional, for job similarity and clustering):**
```bash
pip install chromadb sentence-transformers
```
If not installed, the dashboard will show "Vector DB not available" but the bot still works.

### 2. Configure Environment

```bash
# Copy the template
cp .env.example .env

# Edit .env with your credentials:
# - LINKEDIN_EMAIL
# - LINKEDIN_PASSWORD
# - KIMI_API_KEY (optional — if missing, falls back to Ollama; ensure Ollama is running)
```

### 3. Add Your Resume

Place your resume PDF in either:
- `auto_apply/data/resume/` — OR —
- `details/` (already detected automatically)

### 4. Run

**Option A: Dashboard (recommended)**
```bash
python main.py --dashboard
```
Opens the Streamlit dashboard at http://localhost:8502. The API server (port 8081) starts automatically in the background. Use the dashboard to run the pipeline, scrape, apply, view status, and explore job similarity/clusters.

**Option B: API server only**
```bash
python main.py
```
Starts the FastAPI server on http://localhost:8081. Trigger the pipeline via API:
```bash
curl -X POST http://localhost:8081/run
```

**Option C: Run directly (no server)**
```bash
python main.py --run
```

**Option D: Headless mode**
```bash
python main.py --run --headless
```

**Ports:** API server defaults to 8081, dashboard to 8502. Override with `--port` and `--dashboard-port`, or set `SERVER_PORT` and `DASHBOARD_PORT` in `.env`.

## API Endpoints

| Method | Endpoint    | Description                          |
|--------|-------------|--------------------------------------|
| GET    | `/`         | Bot info and current state           |
| GET    | `/status`   | Detailed status and session stats    |
| POST   | `/run`      | Start the application pipeline       |
| POST   | `/stop`     | Stop after current application       |
| POST   | `/pause`    | Pause the pipeline                   |
| POST   | `/resume`   | Resume after pause                   |
| GET    | `/results`  | List all application results         |
| GET    | `/health`   | Health check                         |
| GET    | `/jobs/similar` | Find similar jobs (job_id or query) |
| GET    | `/clusters` | List job clusters                    |
| GET    | `/clusters/{id}` | Jobs in a cluster               |
| GET    | `/vector-db/stats` | Vector DB job count             |

### POST /run — Body (optional)
```json
{
  "max_applications": 25,
  "headless": false
}
```

## Configuration

All settings are in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `LINKEDIN_EMAIL` | — | Your LinkedIn email |
| `LINKEDIN_PASSWORD` | — | Your LinkedIn password |
| `LLM_PROVIDER` | `kimi` | LLM provider: `kimi`, `ollama`, or `openai` |
| `KIMI_API_KEY` | — | Kimi K2.5 API key (leave empty to use Ollama) |
| `SERVER_PORT` | `8081` | API server port |
| `DASHBOARD_PORT` | `8502` | Streamlit dashboard port |
| `HEADLESS` | `false` | Run browser without window |
| `SLOW_MO` | `50` | Slow down browser actions (ms) |
| `MAX_APPLICATIONS_PER_SESSION` | `50` | Max applications before stopping |
| `APPLICATION_DELAY_MIN` | `5` | Min seconds between applications |
| `APPLICATION_DELAY_MAX` | `15` | Max seconds between applications |

## How It Works

1. **Login**: Opens LinkedIn, types credentials with human-like keystrokes, handles security checkpoints
2. **Search**: Searches for jobs matching your configured keywords (AI/ML Engineer, Data Scientist, Backend Engineer, etc.)
3. **Filter**: For each job, uses Kimi K2.5 to evaluate relevance against your profile
4. **Apply**: 
   - Clicks "Easy Apply" button
   - Extracts all form fields (text, select, radio, checkbox, file upload)
   - Sends field descriptions to Kimi K2.5 with your profile context
   - Fills each field using mouse clicks and keyboard typing
   - Handles multi-page forms with Next/Review/Submit navigation
   - If errors appear, takes a screenshot → sends to LLM → applies the fix
   - For external applications, analyzes the external page and attempts to fill
5. **Report**: Saves a detailed JSON report with every application's status

## LLM Integration

The bot uses the LLM for:
- **Form filling**: Determines the best answer for each field based on your profile
- **Error recovery**: Analyzes screenshot of errors and suggests fixes
- **Job relevance**: Evaluates if a job matches your skills and preferences
- **Cover letters**: Generates tailored cover letters when required
- **Page state analysis**: Understands what's on the screen when automation gets stuck

Supports three providers:
- **Kimi K2.5** (default) — Moonshot AI's model with vision capabilities
- **Ollama** — Local models (llama3.2-vision, llava, etc.)
- **OpenAI** — GPT-4o or any compatible API

## Customization

### Update Your Profile
Edit `profile.py` to update your personal details, skills, experience, and target job preferences.

### Add/Modify Search Keywords
Edit the `job_search_keywords` list in `profile.py`.

### Adjust Anti-Detection
Edit `browser/engine.py` to modify user agent, viewport, or stealth settings.

## Vector Database and Similarity

Jobs are embedded and stored in ChromaDB for similarity search and clustering. Requires `chromadb` and `sentence-transformers` (`pip install chromadb sentence-transformers`).

**Dashboard:** Use the "Job Similarity" and "Clusters" tabs to search similar jobs and view clusters.

**Docs:** [docs/VECTOR_DATABASE_GUIDE.md](docs/VECTOR_DATABASE_GUIDE.md), [docs/SIMILARITY_API.md](docs/SIMILARITY_API.md)

**CLI scripts:**
- `python scripts/query_similar_jobs.py --job-id 4381380387 --top-k 10`
- `python scripts/analyze_clusters.py --export clusters.json`
- `python scripts/maintain_vector_db.py --stats`

## Important Notes

- **LinkedIn Rate Limits**: The bot includes random delays to appear human-like, but excessive use may trigger LinkedIn's anti-automation measures
- **Security Checkpoints**: If LinkedIn asks for verification (CAPTCHA, email code), you'll need to complete it manually in the browser window — the bot will wait up to 120 seconds
- **Session Persistence**: Browser data is stored in `browser_data/` so you stay logged in
- **Responsibility**: Use this tool responsibly and in compliance with LinkedIn's Terms of Service
