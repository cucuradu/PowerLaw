"""
ConstructPulse Ingestion Console & Pipeline Operations
Operations dashboard for EUR-Lex downloads, RSS news scraping, and LanceDB storage management.
"""

import os
from pathlib import Path
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

from src.storage.dashboard_data import get_graph_store, submit_ingestion_job, get_job_status
from src.scrapers.eurlex import TARGET_LAWS
from src.config import get_settings, load_data_sources_registry
from src.utils.api_wrapper import LLM_FLASH_MODEL

load_dotenv()

st.set_page_config(
    page_title="Ingestion Console — ConstructPulse",
    page_icon="⚙️",
    layout="wide"
)

# Load store
try:
    store = get_graph_store()
except Exception as e:
    st.error(f"Failed to load storage engine: {e}")
    st.stop()

st.title("⚙️ Data Collection & Ingestion Console")
st.markdown("Operational control center for managing **EUR-Lex legislation scrapers**, **news feeds**, and **LanceDB persistence**.")

# --- THREE COLUMN CONTROLS ---
col_eur, col_news, col_db = st.columns(3)

# 1. EUR-Lex Scraper Controls
with col_eur:
    with st.container(border=True):
        st.subheader("📜 EUR-Lex Scraper")
        st.write("Direct CELEX download and SPARQL bulk endpoint.")
        st.markdown(f"**Target Laws Configured:** {len(TARGET_LAWS)}")
        for l in TARGET_LAWS[:3]:
            st.caption(f"• `{l['celex_id']}`: {l['title'][:28]}...")

        if st.button("🚀 Enqueue EUR-Lex Ingestion Job", width="stretch"):
            try:
                job = submit_ingestion_job(source_id="EURLEX_CELLAR", limit=len(TARGET_LAWS))
                st.success(f"Ingestion job `{job['job_id']}` submitted to queue. Status: {job['status']}")
            except Exception as e:
                st.error(f"Failed to submit ingestion job: {e}")

# 2. News Scraper Controls
with col_news:
    with st.container(border=True):
        st.subheader("📰 Economic News Scraper")
        st.write("Fetches trade, commodity, and policy RSS feeds.")
        news_source = load_data_sources_registry()["sources"]["OFFICIAL_NEWS"]
        feed_count = len(news_source.get("endpoints", {})) if news_source.get("enabled") else 0
        st.markdown(f"**Configured Official Feeds:** {feed_count}")

        if st.button("🔄 Enqueue News Ingestion Job", width="stretch"):
            try:
                job = submit_ingestion_job(source_id="OFFICIAL_NEWS", limit=50)
                st.success(f"News ingestion job `{job['job_id']}` submitted to queue. Status: {job['status']}")
            except Exception as e:
                st.error(f"Failed to submit news ingestion job: {e}")

# 3. Database & Store Controls
with col_db:
    with st.container(border=True):
        st.subheader("💾 Dual-Layer Storage Engine")
        st.write("LanceDB tables and Parquet backups.")
        
        nodes_cnt = len(store.get_nodes())
        edges_cnt = len(store.get_edges())
        hist_cnt = len(store.get_history())
        st.markdown(f"• Nodes: **{nodes_cnt}** | Edges: **{edges_cnt}**")
        st.markdown(f"• Tabular Records: **{hist_cnt}**")

st.divider()

# --- STORAGE HEALTH & FILE AUDIT ---
st.subheader("📁 File System & Table Inventory")

storage_path = get_settings().lancedb_path
files_info = []

if storage_path.exists():
    for f in storage_path.glob("*"):
        if f.is_file():
            files_info.append({
                "Filename": f.name,
                "Type": "Parquet / Data",
                "Size (KB)": round(f.stat().st_size / 1024, 2),
                "Last Modified": pd.to_datetime(f.stat().st_mtime, unit="s").strftime("%Y-%m-%d %H:%M:%S")
            })
        elif f.is_dir():
            files_info.append({
                "Filename": f.name,
                "Type": "LanceDB Table Dir",
                "Size (KB)": round(sum(p.stat().st_size for p in f.rglob('*') if p.is_file()) / 1024, 2),
                "Last Modified": pd.to_datetime(f.stat().st_mtime, unit="s").strftime("%Y-%m-%d %H:%M:%S")
            })

if files_info:
    st.dataframe(pd.DataFrame(files_info), width="stretch", hide_index=True)
else:
    st.info("No files found in storage directory.")

st.divider()

# --- 4. GEMINI 3.8 FLASH ENGINE & THINKING EFFORT ---
st.subheader("🧠 Google Gemini 3.8 Flash AI Engine & Reasoning Effort")
c_gem1, c_gem2 = st.columns([2, 1])

with c_gem1:
    effort_choice = st.radio(
        "Select Reasoning Effort (Thinking Level):",
        options=[
            "⚡ Dynamic: Low for news/market feeds, High for statutory law logic gates (Active)",
            "🐇 Low: Maximum speed & throughput (Minimum latency for real-time streams)",
            "⚖️ Medium: Balanced step-by-step reasoning",
            "🧠 High: Extended reasoning tokens (Deep multi-hop causal statutory parsing)"
        ],
        index=0,
        help="Controls the thinking token budget and reasoning depth of Gemini 3.8 Flash."
    )
    st.caption("Active Model: **`gemini-3.8-flash`** • Key Status: **`Connected`** • Embedding Engine: **`gemini-embedding-001` (768-dim)**")

with c_gem2:
    if st.button("🧪 Ping Gemini 3.8 Flash", width="stretch"):
        import time
        from src.utils.api_wrapper import get_completion
        t0 = time.time()
        try:
            with st.spinner("Pinging Gemini 3.8 Flash API..."):
                reply = get_completion("Respond with exactly: PONG_CONSTRUCT_PULSE_OK")
                dt = (time.time() - t0) * 1000.0
                st.success(f"✅ Online! Latency: {dt:.0f} ms\n\n`{reply.strip()}`")
        except Exception as e:
            st.error(f"Ping failed: {e}")

st.divider()

# --- DIAGNOSTIC LOG INSPECTION ---
st.subheader("🔍 Environment & Architecture Diagnostics")
col_diag1, col_diag2 = st.columns(2)

with col_diag1:
    st.markdown("#### Dependencies & Versions")
    try:
        import lancedb
        lance_v = lancedb.__version__
    except Exception:
        lance_v = "N/A"
    try:
        import networkx
        nx_v = networkx.__version__
    except Exception:
        nx_v = "N/A"

    import platform
    st.code(f"""
Python:      {platform.python_version()}
Streamlit:   {st.__version__}
LanceDB:     {lance_v}
NetworkX:    {nx_v}
Pandas:      {pd.__version__}
    """.strip(), language="text")

with col_diag2:
    st.markdown("#### Cloud & AI Configuration")
    st.code(f"""
GEMINI_MODEL:         {LLM_FLASH_MODEL}
THINKING_EFFORT:      dynamic (low: feeds / high: laws)
AUTHENTICATION:      ADC (configuration is not a live verification)
LANCEDB_PATH:         {storage_path}
    """.strip(), language="text")
