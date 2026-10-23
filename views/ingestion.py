"""Ingestion: the weekly update (schedule and last runs), the configured sources, and what the store holds.

Read-only apart from the schedule toggle: data is ingested only by `python -m scripts.ops.update_forecast`.
"""
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from scripts.ops import schedule_updates
from src.config import get_settings, load_data_sources_registry

st.title("⚙️ Ingestion")
st.caption("Data enters the store only through the weekly update (`scripts/ops/update_forecast.cmd`, also runnable by hand): "
           "it pulls new releases, then rebuilds the forecasts, the projections and the graph snapshot.")

# --- Weekly update ------------------------------------------------------------------------------------------------
st.subheader("Weekly update")
c_day, c_time, c_toggle = st.columns(3)
try:
    enabled = schedule_updates.is_enabled()
except Exception:  # no Windows task scheduler here
    enabled = False
day = c_day.selectbox("Day", ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"], disabled=enabled)
time_of_day = c_time.text_input("Time (local)", value="07:00", disabled=enabled)
wanted = c_toggle.toggle("Automatic updates on", value=enabled)
if wanted != enabled:
    try:
        st.success(schedule_updates.enable(day, time_of_day) if wanted else schedule_updates.disable())
    except Exception as e:
        st.error(f"Could not change the schedule: {e}")

log_file = Path(get_settings().data_dir) / "reports" / "update-log.jsonl"
runs = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()] if log_file.exists() else []
if runs:
    last = runs[-1]
    steps = pd.DataFrame([{"Step": s["step"].replace("scripts.ops.", "").replace("ingest --source ", "ingest ").split(" --since")[0],
                           "Result": "skipped" if "skipped" in s else "ok" if s.get("exit_code") == 0 else "failed",
                           "Started": (s.get("started") or "")[:16].replace("T", " ")} for s in last["steps"]])
    failed = (steps["Result"] != "ok").sum()
    st.markdown(f"**Last run {last['run_at'][:16].replace('T', ' ')} UTC:** "
                + ("all steps OK" if not failed else f"{failed} step(s) did not complete"))
    st.dataframe(steps, hide_index=True, width="stretch",
                 column_config={"Result": st.column_config.TextColumn(help="ok, failed or skipped (skipped when Destatis failed)")})
    if len(runs) > 1:
        st.caption("Earlier runs: " + "; ".join(
            f"{r['run_at'][:10]} {'OK' if all(s.get('exit_code') == 0 for s in r['steps']) else 'with failures'}" for r in runs[-6:-1]))
else:
    st.info("No update has run yet.")

# --- Sources ------------------------------------------------------------------------------------------------------
st.subheader("Sources")
sources = load_data_sources_registry()["sources"]
last_result = {}
for s in (runs[-1]["steps"] if runs else []):
    if s["step"].startswith("scripts.ops.ingest --source "):
        last_result[s["step"].split()[2]] = "ok" if s.get("exit_code") == 0 else "failed"
st.dataframe(pd.DataFrame([{"Source": sid, "What": spec.get("name", ""), "Enabled": "yes" if spec.get("enabled") else "no",
                            "Releases": (spec.get("release_cadence") or "").lower(), "Last weekly run": last_result.get(sid, "")}
                           for sid, spec in sorted(sources.items(), key=lambda kv: (not kv[1].get("enabled"), kv[0]))]),
             hide_index=True, width="stretch")
st.caption("Disabled sources keep their stored data; see docs/agent-memory/decisions.md for why each was turned off.")

# --- Store --------------------------------------------------------------------------------------------------------
st.subheader("Store")
snapshot = Path(get_settings().data_dir) / "reports" / "graph-view-latest.json"
if snapshot.exists():
    inv = json.loads(snapshot.read_text(encoding="utf-8"))["inventory"]
    m = st.columns(4)
    m[0].metric("Series", inv["series"], border=True)
    m[1].metric("Graph nodes", inv["nodes"], border=True)
    m[2].metric("Relations drawn", inv["relations"], border=True)
    m[3].metric("Source documents", inv["documents"], border=True)
tables = []
for f in sorted(get_settings().lancedb_path.glob("*")):
    size = sum(p.stat().st_size for p in f.rglob("*") if p.is_file()) if f.is_dir() else f.stat().st_size
    tables.append({"Table": f.name.replace(".lance", "").replace("canonical_", ""), "MB": size / 1e6})
if tables:
    st.altair_chart(alt.Chart(pd.DataFrame(tables)).mark_bar(color="#2563EB").encode(
        x=alt.X("MB:Q", title="Size on disk (MB)"), y=alt.Y("Table:N", sort="-x", title=None),
        tooltip=["Table:N", alt.Tooltip("MB:Q", format=",.1f")]).properties(height=28 * len(tables)), width="stretch")
    st.caption("Evidence (quotes linking every stored value to its source) is the largest table.")
