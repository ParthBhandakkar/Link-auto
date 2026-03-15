"""
LinkConnect Dashboard — Streamlit UI for LinkedIn Auto-Apply Bot.

Operate the bot, view status, and explore job similarity/clusters.
Run with: streamlit run dashboard/app.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st
import requests

# API base URL (FastAPI server) — configurable via env DASHBOARD_API_URL
API_BASE = os.environ.get("DASHBOARD_API_URL", "http://localhost:8081")

st.set_page_config(
    page_title="LinkConnect Dashboard",
    page_icon="briefcase",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "api_base" not in st.session_state:
    st.session_state["api_base"] = API_BASE

st.title("LinkConnect — LinkedIn Auto-Apply Bot")
st.caption("Operate the bot, view status, and explore job similarity")


def _get_base() -> str:
    """API base URL from session state or default."""
    return st.session_state.get("api_base", API_BASE)


def _api_get(path: str) -> dict | None:
    """GET request to API. Returns None on error."""
    try:
        base = _get_base()
        r = requests.get(f"{base}{path}", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def _api_post(path: str, json_data: dict | None = None) -> dict | None:
    """POST request to API. Returns None on error."""
    try:
        base = _get_base()
        r = requests.post(f"{base}{path}", json=json_data or {}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return None


# ── Sidebar: Pipeline Control ───────────────────────────────────────────
with st.sidebar:
    st.header("Pipeline Control")
    max_apps = st.slider("Max applications", 5, 100, 25, key="max_apps")

    if st.button("Run Full Pipeline", use_container_width=True):
        res = _api_post("/run", {"max_applications": max_apps})
        if res:
            st.success("Pipeline started")

    if st.button("Scrape Only", use_container_width=True):
        res = _api_post("/scrape")
        if res:
            st.success("Scrape started")

    if st.button("Apply from Sheet", use_container_width=True):
        res = _api_post("/run")
        if res:
            st.success("Apply started")

    st.divider()
    if st.button("Stop", use_container_width=True):
        res = _api_post("/stop")
        if res:
            st.warning("Stop requested")

    if st.button("Pause", use_container_width=True):
        res = _api_post("/pause")
        if res:
            st.info("Paused")

    if st.button("Resume", use_container_width=True):
        res = _api_post("/resume")
        if res:
            st.success("Resumed")

    st.divider()
    api_url = st.text_input("API URL", value=st.session_state.get("api_base", API_BASE), key="api_url")
    st.session_state["api_base"] = (api_url or API_BASE).rstrip("/")
    st.caption("Ensure the FastAPI server is running")


# ── Main: Tabs ───────────────────────────────────────────────────────────
tab_status, tab_similarity, tab_clusters, tab_reports = st.tabs([
    "Status",
    "Job Similarity",
    "Clusters",
    "Reports",
])

with tab_status:
    st.subheader("Bot Status")
    data = _api_get("/status")
    if data:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("State", data.get("state", "unknown"))
        with col2:
            stats = data.get("stats", {})
            st.metric("Jobs Found", stats.get("total_found", 0))
        with col3:
            st.metric("Applied", stats.get("total_applied", 0))
        with col4:
            vdb = _api_get("/vector-db/stats")
            if vdb:
                cnt = vdb.get("job_count", 0)
                err = vdb.get("error")
                st.metric("Vector DB Jobs", cnt if err is None else "-")
                if err:
                    st.caption(err)
            else:
                st.metric("Vector DB Jobs", "-")
        with st.expander("Raw status"):
            st.json(data)
    else:
        st.warning("Could not fetch status. Is the server running?")

with tab_similarity:
    st.subheader("Find Similar Jobs")
    col1, col2 = st.columns(2)
    with col1:
        job_id = st.text_input("Job ID (from LinkedIn URL)", placeholder="e.g. 4381380387")
    with col2:
        query_text = st.text_input("Or search by text", placeholder="e.g. Python ML Engineer")
    top_k = st.slider("Top K results", 3, 20, 10)

    if st.button("Search"):
        if job_id:
            data = _api_get(f"/jobs/similar?job_id={job_id}&top_k={top_k}")
        elif query_text:
            data = _api_get(f"/jobs/similar?query={query_text}&top_k={top_k}")
        else:
            st.error("Enter job_id or query text")
            data = None
        if data:
            st.metric("Results", data.get("count", 0))
            for r in data.get("results", []):
                with st.expander(f"{r.get('metadata', {}).get('title', 'Job')} — Score: {r.get('score', r.get('distance', 'N/A'))}"):
                    st.json(r)

with tab_clusters:
    st.subheader("Job Clusters")
    if st.button("Load Clusters"):
        data = _api_get("/clusters")
        if data:
            st.session_state["clusters_data"] = data
        else:
            st.session_state.pop("clusters_data", None)
            st.warning("No clusters or vector DB empty. Run a scrape first.")

    data = st.session_state.get("clusters_data")
    if data:
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Total Jobs", data.get("total_jobs", 0))
        with col2:
            st.metric("Clusters", data.get("cluster_count", 0))
        for c in data.get("clusters", []):
            with st.expander(f"Cluster {c.get('cluster_id')} — {c.get('size', 0)} jobs"):
                st.write("Sample titles:", c.get("sample_titles", []))
                st.write("Companies:", c.get("companies", []))
    elif "clusters_data" not in st.session_state:
        st.info("Click 'Load Clusters' to fetch cluster data from the vector DB.")

with tab_reports:
    st.subheader("Recent Reports")
    data_dir = Path(__file__).resolve().parent.parent / "data"
    if data_dir.exists():
        reports = sorted(data_dir.glob("report_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        for rp in reports:
            with st.expander(rp.name):
                try:
                    content = json.loads(rp.read_text(encoding="utf-8"))
                    st.metric("Applied", content.get("total_applied", 0))
                    st.metric("Failed", content.get("total_failed", 0))
                    st.json(content)
                except Exception:
                    st.code(rp.read_text(encoding="utf-8", errors="replace")[:2000])
    else:
        st.info("No reports yet. Run a pipeline to generate reports.")
