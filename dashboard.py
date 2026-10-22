"""
ConstructPulse — AI Construction Law, Supply Chain & Material Price Forecaster
Executive Overview of the Dual-Layer Legal & Economic System
"""

import os
import json
import logging
from pathlib import Path
import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import plotly.express as px
import networkx as nx
from dotenv import load_dotenv

from src.config import get_settings

from src.storage.dashboard_data import (
    get_graph_store,
    get_system_kpis,
    get_construction_outlook,
    load_latest_forecast,
    prediction_record_from_forecast,
    get_target_history,
    get_price_events,
    get_forecast_surprises,
    load_projections,
    load_horizon,
    model_name,
)

load_dotenv()

# Streamlit Page Setup
st.set_page_config(
    page_title="ConstructPulse: Construction Law & Material Price Intelligence",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling for polished executive presentation
st.markdown("""
<style>
    .kpi-card {
        background-color: #0E1117;
        border: 1px solid #262730;
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 12px;
        text-align: center;
    }
    .kpi-title {
        color: #9CA3AF;
        font-size: 0.85rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .kpi-value {
        color: #F9FAFB;
        font-size: 2.2rem;
        font-weight: 700;
        margin-top: 4px;
    }
    .hero-box {
        background: linear-gradient(135deg, rgba(30, 58, 138, 0.45) 0%, rgba(15, 23, 42, 0.85) 100%);
        border: 1px solid #2563EB;
        border-radius: 14px;
        padding: 24px;
        margin-bottom: 24px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
    }
    .badge-layer {
        display: inline-block;
        padding: 5px 12px;
        border-radius: 8px;
        font-size: 0.78rem;
        font-weight: 700;
        margin-right: 8px;
    }
    .badge-layer-1 { background-color: #1E3A8A; color: #93C5FD; border: 1px solid #3B82F6; }
    .badge-layer-2 { background-color: #064E3B; color: #6EE7B7; border: 1px solid #10B981; }
    .badge-layer-3 { background-color: #4C1D95; color: #C4B5FD; border: 1px solid #8B5CF6; }
</style>
""", unsafe_allow_html=True)


# --- DATA INITIALIZATION ---
@st.cache_data(show_spinner=False)
def _load_dashboard_data(storage_dir: str, generation: int, forecast_mtime: float):
    # Keyed on the store generation and the forecast file, so it only recomputes when either changes.
    graph_store = get_graph_store(storage_dir)
    forecast = load_latest_forecast()  # written offline by scripts.ops.backtest_target; never computed here
    return (
        get_system_kpis(storage_dir),
        get_construction_outlook(store=graph_store.canonical_store,
                                 prediction_record=prediction_record_from_forecast(forecast)),
        graph_store.get_history_df(),
        forecast,
        get_target_history(graph_store.canonical_store),
        get_forecast_surprises(forecast),
    )


try:
    store = get_graph_store()
    forecast_file = Path(get_settings().data_dir) / "reports" / "forecast-latest.json"
    kpis, outlook, history_df, forecast, target_history, surprises = _load_dashboard_data(
        str(store.storage_dir), store._generation,
        forecast_file.stat().st_mtime if forecast_file.exists() else 0.0,
    )
except Exception as e:
    st.error(f"Error loading system telemetry: {e}")
    st.stop()


# --- HERO BANNER ---
st.markdown("""
<div class="hero-box">
    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
        <div>
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 6px;">
                <h1 style="margin: 0; color: #FFFFFF; font-size: 2.3rem; font-weight: 800; letter-spacing: -0.02em;">
                    🏗️ ConstructPulse
                </h1>
                <span style="background: rgba(16, 185, 129, 0.2); color: #34D399; border: 1px solid rgba(16, 185, 129, 0.35); padding: 3px 10px; border-radius: 9999px; font-size: 0.75rem; font-weight: 700;">
                    CONSTRUCTION & BUILDING MATERIALS INTELLIGENCE
                </span>
            </div>
            <p style="margin: 4px 0 16px 0; color: #94A3B8; font-size: 1.05rem; max-width: 950px; line-height: 1.5;">
                AI-driven forecasting engine linking <strong>European Construction Directives</strong> (CPR, EPBD, CBAM, EU ETS) 
                to <strong>physical commodity price spikes</strong> (Rebar, Portland Cement, Ready-Mix, Mass Timber) and 
                <strong>contractor equity margins</strong>. Powered by a Dual-Layer Topological Knowledge Graph and Spatio-Temporal GNN.
            </p>
            <div>
                <span class="badge-layer badge-layer-1">TOPOLOGICAL GRAPH</span>
                <span class="badge-layer badge-layer-2">BEHIND-THE-GRAPH TABULAR LEDGER</span>
                <span class="badge-layer badge-layer-3">GNN IMPULSE RESPONSE & SHOCK FORECASTER</span>
            </div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


# --- 1. EXECUTIVE KPI METRICS BAR ---
st.markdown("### 📊 Dual-Layer System Telemetry & KPIs")

col1, col2, col3, col4, col5, col6 = st.columns(6)

with col1:
    st.metric(
        label="📜 EU Regulations",
        value=kpis.get("total_laws", 0),
        delta="Statutory Mandates",
        delta_color="normal"
    )

with col2:
    st.metric(
        label="⚡ Mandated Actions",
        value=kpis.get("total_actions", 0),
        delta="Compliance Steps",
        delta_color="normal"
    )

with col3:
    st.metric(
        label="🎯 Market Reactions",
        value=kpis.get("total_reactions", 0),
        delta="Economic Shocks",
        delta_color="normal"
    )

with col4:
    st.metric(
        label="🔗 Topological Edges",
        value=kpis.get("total_edges", 0),
        delta="Causal Relations",
        delta_color="normal"
    )

with col5:
    st.metric(
        label="⏱️ Temporal Entries",
        value=kpis.get("total_temporal_entries", 0),
        delta="Timestamped States",
        delta_color="normal"
    )

with col6:
    gamma_val = kpis.get("gamma")
    st.metric(
        label="📐 Power-Law (γ)",
        value=f"{gamma_val:.2f}" if gamma_val and kpis.get("total_edges") else "Unavailable",
        delta="Estimated from stored graph" if kpis.get("total_edges") else "Insufficient graph data",
        delta_color="normal"
    )

st.divider()

# --- 2. OFFICIAL GERMAN CONSTRUCTION OUTLOOK & EVIDENCE (TASK 11) ---
st.markdown("### 🏗️ German Construction Price & Regulatory Outlook")
st.caption(
    "Primary Target: Germany Producer Price Index for Residential Buildings (New Work), 2021=100 (Destatis 61261-0002). "
    "Point-in-time public availability with separate observed, forecast, reported, and regulatory context."
)

try:
    # 1. Target & Forecast Summary Bar
    oc1, oc2, oc3, oc4 = st.columns(4)
    with oc1:
        latest = outlook.get("latest_observation")
        if latest and latest.get("value") is not None:
            st.metric(
                label=f"Observed PPI ({latest.get('period', 'N/A')})",
                value=f"{latest['value']:.2f}",
                delta=f"Status: {latest.get('status', 'AVAILABLE')} (Published: {str(latest.get('published_at', 'N/A'))[:10]})",
                delta_color="normal"
            )
        else:
            st.metric(
                label="Observed PPI",
                value="Unavailable",
                delta="No verified observations as of cutoff",
                delta_color="off"
            )

    with oc2:
        pt_fc = outlook.get("point_forecast")
        t_period = outlook.get("target_period", "Next Q")
        if pt_fc is not None:
            st.metric(
                label=f"Forecast PPI ({t_period})",
                value=f"{pt_fc:.2f}",
                delta=f"{outlook['point_growth_pct']:+.2f}% vs previous quarter",
                delta_color="normal"
            )
        else:
            st.metric(
                label=f"Forecast PPI ({t_period})",
                value="Unavailable",
                delta="Insufficient history",
                delta_color="off"
            )

    with oc3:
        interval = outlook.get("interval", {})
        cal_status = interval.get("calibration_status", "unavailable")
        if cal_status == "calibrated" and interval.get("lower") is not None and interval.get("upper") is not None:
            st.metric(
                label="90% Conformal Interval",
                value=f"[{interval['lower']:.2f}, {interval['upper']:.2f}]",
                delta=f"{interval['lower_growth_pct']:+.2f}% to {interval['upper_growth_pct']:+.2f}% q/q"
                      if interval.get("lower_growth_pct") is not None else "Calibrated (≥20 historical quarters)",
                delta_color="normal"
            )
        else:
            st.metric(
                label="90% Conformal Interval",
                value="Uncalibrated",
                delta="<20 calibration quarters" if cal_status == "insufficient_calibration_history" else "Unavailable",
                delta_color="off"
            )

    with oc4:
        st.metric(
            label="Target Benchmark",
            value="Destatis 61261-0002",
            delta="Strict Anti-Proxy (No Financial Substitution)",
            delta_color="normal"
        )

    # Measured track record of the same forecast setup (rolling origin, data public at each origin)
    if forecast and forecast.get("track_record"):
        record = forecast["track_record"]
        rows = [{"Model": model_name(name), "Origins": m["n"], "MAE (pp q/q)": round(m["mae"], 3),
                 "RMSE (pp q/q)": round(m["rmse"], 3),
                 "90% interval coverage": None if m["interval"]["coverage"] is None else f"{m['interval']['coverage']:.0%}",
                 "Mean half-width (pp)": None if m["interval"]["mean_half_width"] is None else round(m["interval"]["mean_half_width"], 2)}
                for name, m in record.items()]
        st.markdown(f"**Forecast track record** — next-release growth, measured on past releases "
                    f"(record generated {forecast['generated_at'][:10]})")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        refs = {k: v for k, v in (forecast.get("reference_forecasts_growth_pct") or {}).items() if v is not None}
        if refs:
            st.caption("Other models for the same quarter: " + "; ".join(f"{model_name(k)} {v:+.2f}%" for k, v in refs.items()))
        for note in forecast.get("limitations", []):
            st.caption(f"• {note}")

    # Price changes over time, with dated events (timing only, not proof of cause)
    if target_history:
        st.markdown("**Quarterly price change and major events**")
        growth = pd.DataFrame([h for h in target_history if h["growth_pct"] is not None])
        growth["quarter"] = pd.to_datetime(growth["period_start"])
        events = pd.DataFrame(get_price_events())
        events["when"] = pd.to_datetime(events["date"])
        bars = alt.Chart(growth).mark_bar(color="#3B82F6").encode(
            x=alt.X("quarter:T", title=None),
            y=alt.Y("growth_pct:Q", title="Change vs previous quarter (%)"),
            tooltip=[alt.Tooltip("period_start:N", title="Quarter starting"), alt.Tooltip("value:Q", title="Index", format=".1f"),
                     alt.Tooltip("growth_pct:Q", title="Change %", format="+.2f")])
        rules = alt.Chart(events).mark_rule(color="#F59E0B", strokeDash=[4, 3]).encode(
            x="when:T", tooltip=[alt.Tooltip("date:N", title="Date"), alt.Tooltip("title:N", title="Event")])
        st.altair_chart((bars + rules).properties(height=260), width="stretch")
        st.caption("Orange lines are dated events (hover for details). Lining up in time is not proof that the event caused the move.")
    if surprises:
        st.markdown("**Biggest past forecast misses and what happened in the six months before**")
        st.dataframe(pd.DataFrame([{"Quarter": r["quarter"], "Actual change %": round(r["actual_pct"], 2),
                                    "Forecast %": round(r["forecast_pct"], 2), "Miss (points)": round(r["miss_pp"], 2),
                                    "Events": "; ".join(r["events"]) or "none on the list"} for r in surprises]),
                     width="stretch", hide_index=True)

    # 2. Tabs for Context, Gaps & Evidence
    out_tab1, out_tab2, out_tab3, out_tab4 = st.tabs([
        "📜 Regulatory Context (Active & Forthcoming)",
        "🧱 Supply Context (Measured vs Reported)",
        "⚠️ Data Gaps & Source Status",
        "⚖️ Evidence Links & Limitations"
    ])

    with out_tab1:
        reg_ctx = outlook.get("regulatory_context", [])
        if reg_ctx:
            reg_df = pd.DataFrame(reg_ctx)
            cols = [c for c in ["label", "status", "valid_from", "law_id", "rule_id"] if c in reg_df.columns]
            st.dataframe(reg_df[cols], use_container_width=True, hide_index=True)
        else:
            st.info("No active or forthcoming legal obligations recorded as of current cutoff.")

    with out_tab2:
        supply_ctx = outlook.get("supply_context", [])
        if supply_ctx:
            sup_df = pd.DataFrame(supply_ctx)
            cols = [c for c in ["kind", "claim_status", "label", "value", "unit", "period"] if c in sup_df.columns]
            st.dataframe(sup_df[cols], use_container_width=True, hide_index=True)
            st.caption("Note: 'REPORTED' denotes unverified news claims/signals; 'MEASURED' denotes official statistical observations.")
        else:
            st.info("No physical or reported supply context items recorded.")

    with out_tab3:
        gaps = outlook.get("source_gaps", [])
        if gaps:
            for g in gaps:
                st.warning(f"⚠️ {g}")
        else:
            st.success("No active data gaps or staleness detected for primary target series.")

    with out_tab4:
        ev_links = outlook.get("evidence", [])
        if ev_links:
            st.markdown("##### 🔗 Primary Evidence Links")
            for ev in ev_links:
                url = ev.get("url")
                pub = ev.get("publisher", "Official Publisher")
                doc_id = ev.get("document_id", "Document")
                if url:
                    st.markdown(f"- **[{pub} — {doc_id}]({url})**")
                else:
                    st.markdown(f"- **{pub}** (`{doc_id}`)")

        st.markdown("##### 🛡️ Causal & Anti-Proxy Limitations")
        for lim in outlook.get("limitations", []):
            st.caption(f"• {lim}")

except Exception as e:
    st.info(f"Construction outlook panel initializing: {e}")

st.divider()

# --- 2b. NEXT-QUARTER OUTLOOK FOR MATERIALS, ENERGY AND COSTS ---
projections = load_projections()
if projections and projections["projections"]:
    st.markdown("### 🧱 Building materials, energy and costs: next quarter")
    st.caption("Change of next quarter's average price over the latest complete quarter. Each forecast was tested on "
               "2018 onward with only data public at the time; where it was not more accurate than assuming no change, "
               "the table says so, and \"flat\" is the better guess.")
    rows = []
    for p in sorted(projections["projections"], key=lambda p: -(p["growth_pct"] or 0)):
        test, interval = p["test"], p["interval_growth_pct"]
        better = test["mae"] < test["mae_no_change"]
        rows.append({"Series": p["name"], "Quarter": p["target_period_start"][:7],
                     "Forecast %": round(p["growth_pct"], 2) if p["growth_pct"] is not None else None,
                     "Likely range %": f"{interval[0]:+.1f} to {interval[1]:+.1f}" if interval else "",
                     "Typical miss (pts)": round(test["mae"], 2), "No-change miss": round(test["mae_no_change"], 2),
                     "Better than no change?": "yes" if better else "no"})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                 column_config={"Forecast %": st.column_config.NumberColumn(format="%+.2f")})
    beat = sum(r["Better than no change?"] == "yes" for r in rows)
    st.caption(f"{beat} of {len(rows)} forecasts beat \"no change\" in the test. Ranges held about "
               f"{np.mean([p['test']['coverage'] for p in projections['projections'] if p['test']['coverage'] is not None]):.0%} "
               "of the time (target 90%). Generated " + projections["generated_at"][:10] + " by scripts.ops.project_series.")
    st.divider()

# --- 2c. NEXT 1-3 YEARS ---
horizon = load_horizon()
if horizon and horizon["forecasts"]:
    from src.config import primary_target_series_id
    st.markdown("### 📅 Construction prices over the next 1-3 years")
    st.caption("Cumulative change from the latest released quarter. Models were chosen on 1998-2012 and then scored "
               "once on 2013 onward (a period that includes the 2021-23 price surge); the errors and ranges below are "
               "from that held-out test.")
    rows = []
    for h in (4, 8, 12):
        key = f"h{h}"
        target = next((f for f in horizon["forecasts"] if f["series_id"] == primary_target_series_id()
                       and f["horizon_quarters"] == h), None)
        test = horizon["target"].get(key, {}).get("holdout", {})
        trades = [f["growth_pct"] for f in horizon["forecasts"] if f["series_id"] != primary_target_series_id()
                  and f["horizon_quarters"] == h]
        if not target:
            continue
        interval = target["interval_growth_pct"]
        rows.append({"Horizon": f"{h // 4} year{'s' if h > 4 else ''}", "To quarter": target["target_period_start"][:7],
                     "Forecast %": round(target["growth_pct"], 1),
                     "Likely range %": f"{interval[0]:+.1f} to {interval[1]:+.1f}" if interval else "",
                     "Typical miss (pts)": round(test["mae"][target["model"]], 1) if test.get("mae") else None,
                     "Trend-only miss": round(test["mae"]["drift_10y"], 1) if test.get("mae") else None,
                     "Range held": f"{test['history_range_coverage']:.0%}" if test.get("history_range_coverage") is not None else "",
                     "Trades, median %": round(float(np.median(trades)), 1) if trades else None})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.caption("\"Trend-only\" assumes the last ten years' average growth continues. Where it missed less, the model "
               "adds nothing over the trend at that horizon. The range is what happened over the same span in 90% of "
               "all periods since 1968 (including the 1970s and 2021-23), not a band around the forecast; in the test "
               "it held less often than 90% at 3 years. Generated " + horizon["generated_at"][:10] + " by scripts.ops.horizon_forecast.")
    st.divider()

# --- 3. INTERACTIVE CONSTRUCTION PRICE & COMMODITY TELEMETRY ---
st.markdown("### 📈 Live Construction Commodity Price Trajectories & Market Spreads")
st.caption("Historical price benchmarks and equity indices extracted from the Behind-The-Graph Tabular Ledger (LanceDB).")

if not history_df.empty and "metric_value" in history_df.columns:
    # Filter to physical commodities and stocks
    plot_df = history_df.dropna(subset=["metric_value", "timestamp"]).copy()
    plot_df["timestamp"] = pd.to_datetime(plot_df["timestamp"])

    chart_tab1, chart_tab2 = st.tabs(["🧱 Physical Construction Commodities", "🏢 Contractor & Producer Equities"])

    with chart_tab1:
        comm_ids = [
            "COMM_REBAR_STEEL", "COMM_PORTLAND_CEMENT", "COMM_READY_MIX_CONCRETE",
            "COMM_CLT_TIMBER", "COMM_RECYCLED_AGGREGATES", "CO2_ALLOWANCE"
        ]
        comm_df = plot_df[plot_df["node_id"].isin(comm_ids)].sort_values("timestamp")
        if not comm_df.empty:
            fig_comm = px.line(
                comm_df,
                x="timestamp",
                y="metric_value",
                color="node_id",
                markers=True,
                labels={"metric_value": "Market Price / Benchmark Value", "timestamp": "Date", "node_id": "Commodity"},
                title="EU Construction Material Price Indices (2022 — 2026)",
                color_discrete_map={
                    "COMM_REBAR_STEEL": "#EF4444",
                    "COMM_PORTLAND_CEMENT": "#3B82F6",
                    "COMM_READY_MIX_CONCRETE": "#10B981",
                    "COMM_CLT_TIMBER": "#F59E0B",
                    "COMM_RECYCLED_AGGREGATES": "#8B5CF6",
                    "CO2_ALLOWANCE": "#06B6D4"
                }
            )
            fig_comm.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(15,23,42,0.6)",
                font=dict(color="#E2E8F0"),
                margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_comm, use_container_width=True)
        else:
            st.info("No commodity time-series points available.")

    with chart_tab2:
        stock_ids = [
            "STOCK_HEIDELBERG_MATERIALS", "STOCK_HOLCIM", "STOCK_SAINT_GOBAIN",
            "STOCK_VINCI", "STOCK_SKANSKA", "STOCK_ARCELORMITTAL"
        ]
        stock_df = plot_df[plot_df["node_id"].isin(stock_ids)].sort_values("timestamp")
        if not stock_df.empty:
            fig_stock = px.line(
                stock_df,
                x="timestamp",
                y="metric_value",
                color="node_id",
                markers=True,
                labels={"metric_value": "Share Price / Index", "timestamp": "Date", "node_id": "Company"},
                title="Major European Building Material Producers & General Contractors",
                color_discrete_sequence=px.colors.qualitative.Bold
            )
            fig_stock.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(15,23,42,0.6)",
                font=dict(color="#E2E8F0"),
                margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_stock, use_container_width=True)
        else:
            st.info("No equity time-series points available.")

st.divider()

# --- 4. SYSTEM ARCHITECTURE PIPELINE ---
st.markdown("### 🏛️ Dual-Layer Architecture: Statutory Authority to Market Impact")
st.markdown("""
<div style="background: rgba(15, 23, 42, 0.7); border: 1px solid #1E293B; border-radius: 12px; padding: 20px; margin-bottom: 20px;">
    <div style="display: flex; flex-wrap: wrap; gap: 12px; justify-content: space-between; align-items: center;">
        <div style="flex: 1; min-width: 160px; background: #1E293B; border: 1px solid #3B82F6; border-radius: 10px; padding: 14px; text-align: center;">
            <div style="color: #60A5FA; font-size: 0.75rem; font-weight: bold; text-transform: uppercase;">1. Statutory Origin</div>
            <div style="color: #FFFFFF; font-size: 1rem; font-weight: bold; margin-top: 4px;">📜 EU Directives</div>
            <div style="color: #94A3B8; font-size: 0.75rem; margin-top: 4px;">CPR 2024, EPBD, CBAM, ETS</div>
        </div>
        <div style="color: #60A5FA; font-size: 1.2rem;">➔</div>
        <div style="flex: 1; min-width: 160px; background: #1E293B; border: 1px solid #F59E0B; border-radius: 10px; padding: 14px; text-align: center;">
            <div style="color: #FBBF24; font-size: 0.75rem; font-weight: bold; text-transform: uppercase;">2. Mandated Action</div>
            <div style="color: #FFFFFF; font-size: 1rem; font-weight: bold; margin-top: 4px;">⚡ Compliance Acts</div>
            <div style="color: #94A3B8; font-size: 0.75rem; margin-top: 4px;">Digital Passports, GWP Caps</div>
        </div>
        <div style="color: #F59E0B; font-size: 1.2rem;">➔</div>
        <div style="flex: 1; min-width: 160px; background: #1E293B; border: 1px solid #EF4444; border-radius: 10px; padding: 14px; text-align: center;">
            <div style="color: #F87171; font-size: 0.75rem; font-weight: bold; text-transform: uppercase;">3. Market Reaction</div>
            <div style="color: #FFFFFF; font-size: 1rem; font-weight: bold; margin-top: 4px;">🎯 Supply Shocks</div>
            <div style="color: #94A3B8; font-size: 0.75rem; margin-top: 4px;">Scrap Deficit, Green Premium</div>
        </div>
        <div style="color: #EF4444; font-size: 1.2rem;">➔</div>
        <div style="flex: 1; min-width: 160px; background: #1E293B; border: 1px solid #8B5CF6; border-radius: 10px; padding: 14px; text-align: center;">
            <div style="color: #A78BFA; font-size: 0.75rem; font-weight: bold; text-transform: uppercase;">4. Physical Market</div>
            <div style="color: #FFFFFF; font-size: 1rem; font-weight: bold; margin-top: 4px;">🧱 Commodities</div>
            <div style="color: #94A3B8; font-size: 0.75rem; margin-top: 4px;">Rebar, Cement, CLT Timber</div>
        </div>
        <div style="color: #8B5CF6; font-size: 1.2rem;">➔</div>
        <div style="flex: 1; min-width: 160px; background: #1E293B; border: 1px solid #10B981; border-radius: 10px; padding: 14px; text-align: center;">
            <div style="color: #34D399; font-size: 0.75rem; font-weight: bold; text-transform: uppercase;">5. Economic Impact</div>
            <div style="color: #FFFFFF; font-size: 1rem; font-weight: bold; margin-top: 4px;">🏢 Equities & Margins</div>
            <div style="color: #94A3B8; font-size: 0.75rem; margin-top: 4px;">Contractors, Producers, NACE</div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

st.divider()


# --- 3. DUAL-LAYER DEEP-DIVE & PLATFORM CAPABILITIES ---
st.markdown("### 🔍 The Dual-Layer Paradigm in Practice")

col_left, col_right = st.columns(2)

with col_left:
    st.markdown(r"""
    #### 🌐 Layer 1: Topological Causal Graph
    - **Entity Typed Nodes**: Every regulation is an authoritative origin (`LAW`), producing binding requirements (`ACTION`), which propagate into market shocks (`REACTION`) impacting economic receivers (`SECTOR`, `COMMODITY`).
    - **Edge Semantics**: Typed directed relations (`MANDATES`, `TRIGGERS`, `AFFECTS`, `IMPACTS`, `REGULATES`, `AMENDS`) encode statutory causality rather than mere semantic similarity.
    - **Power-Law Scale-Free Topology**: In-degree and out-degree distributions follow a power law $P(k) \sim k^{-\gamma}$, identifying regulatory hub sectors (such as NACE C24 Basic Metals and D35 Energy Supply) that bear disproportionate compliance gravity.
    """)
    st.info("Explore the interactive network and filter by edge type in **🕸️ Graph Explorer**.")

with col_right:
    st.markdown(r"""
    #### ⏱️ Layer 2: Behind-The-Graph Tabular Engine
    - **Nodal State Machine**: Nodes are not static points. Behind each node resides a structured time-series ledger of state transitions (`PROPOSED` → `ACTIVE` → `ENFORCED` → `CRITICAL`).
    - **Continuous Economic Metrics**: Tracks numerical variables like carbon price (€/tCO2), gas storage fill percentage (%), compliance cost indexes, and shipping freight points across historical timestamps.
    - **GNN-Ready Tensors**: Directly compiles into feature matrices $X \in \mathbb{R}^{N \times D}$ and edge index tensors $E \in \mathbb{Z}^{2 \times M}$ for downstream PyTorch Geometric and GNN simulation.
    """)
    st.success("Inspect historical states and slide across time in **⏱️ Temporal Table**.")

st.divider()


# --- 4. NAVIGATION SUITE ---
st.markdown("### 🧭 Visualization Dashboard Suite")

row1_col1, row1_col2, row1_col3 = st.columns(3)

with row1_col1:
    st.markdown("""
    <div class="kpi-card">
        <div style="font-size: 2rem; margin-bottom: 8px;">📜</div>
        <div class="kpi-title">Law Registry</div>
        <p style="color: #94A3B8; font-size: 0.85rem; margin: 8px 0;">
            Browse EU regulations, inspect extracted IF-THEN logic gates, and explore NACE sector mappings.
        </p>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Open Law Registry 📜", width="stretch"):
        st.switch_page("pages/1_📜_Law_Registry.py")

with row1_col2:
    st.markdown("""
    <div class="kpi-card">
        <div style="font-size: 2rem; margin-bottom: 8px;">📰</div>
        <div class="kpi-title">News Feed</div>
        <p style="color: #94A3B8; font-size: 0.85rem; margin: 8px 0;">
            Real-time trade & commodity event stream with automated Receptor Signal and Action/Reaction extraction.
        </p>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Open News Feed 📰", width="stretch"):
        st.switch_page("pages/2_📰_News_Feed.py")

with row1_col3:
    st.markdown("""
    <div class="kpi-card">
        <div style="font-size: 2rem; margin-bottom: 8px;">🕸️</div>
        <div class="kpi-title">Graph Explorer</div>
        <p style="color: #94A3B8; font-size: 0.85rem; margin: 8px 0;">
            Interactive PyVis / NetworkX graph visualizer color-coded by node types with dynamic edge filters.
        </p>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Open Graph Explorer 🕸️", width="stretch"):
        st.switch_page("pages/3_🕸️_Graph_Explorer.py")

row2_col1, row2_col2, row2_col3 = st.columns(3)

with row2_col1:
    st.markdown("""
    <div class="kpi-card">
        <div style="font-size: 2rem; margin-bottom: 8px;">⏱️</div>
        <div class="kpi-title">Temporal Table</div>
        <p style="color: #94A3B8; font-size: 0.85rem; margin: 8px 0;">
            Time-slider inspection of behind-the-graph node states, time-series metrics, and state transitions.
        </p>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Open Temporal Table ⏱️", width="stretch"):
        st.switch_page("pages/4_⏱️_Temporal_Table.py")

with row2_col2:
    st.markdown("""
    <div class="kpi-card">
        <div style="font-size: 2rem; margin-bottom: 8px;">🤖</div>
        <div class="kpi-title">GNN Modeling Lab</div>
        <p style="color: #94A3B8; font-size: 0.85rem; margin: 8px 0;">
            Compile topological graphs and tabular history into PyG / DGL feature tensors and export datasets.
        </p>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Open GNN Modeling Lab 🤖", width="stretch"):
        st.switch_page("pages/5_🤖_GNN_Modeling_Lab.py")

with row2_col3:
    st.markdown("""
    <div class="kpi-card">
        <div style="font-size: 2rem; margin-bottom: 8px;">⚙️</div>
        <div class="kpi-title">Ingestion Console</div>
        <p style="color: #94A3B8; font-size: 0.85rem; margin: 8px 0;">
            Trigger live EUR-Lex regulation downloads, RSS news scraping feeds, and LanceDB storage management.
        </p>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Open Ingestion Console ⚙️", width="stretch"):
        st.switch_page("pages/6_⚙️_Ingestion_Console.py")

st.divider()


# --- 5. NETWORK HUBS & CENTRALITY SUMMARY ---
st.markdown("### 🏆 Scale-Free Regulatory Hubs (Centrality Profiling)")

hub_col1, hub_col2 = st.columns(2)

with hub_col1:
    st.subheader("📥 Top In-Degree Entities (Highest Regulatory Impact)")
    top_in = kpis.get("top_in_degree", [])
    if top_in:
        in_df = pd.DataFrame(top_in)
        in_df = in_df.rename(columns={"node_id": "Node ID", "label": "Label", "in_degree": "In-Degree"})
        st.dataframe(in_df, width="stretch", hide_index=True)
    else:
        st.info("No in-degree data available.")

with hub_col2:
    st.subheader("📤 Top Out-Degree Drivers (Highest Regulatory Leverage)")
    top_out = kpis.get("top_out_degree", [])
    if top_out:
        out_df = pd.DataFrame(top_out)
        out_df = out_df.rename(columns={"node_id": "Node ID", "label": "Label", "out_degree": "Out-Degree"})
        st.dataframe(out_df, width="stretch", hide_index=True)
    else:
        st.info("No out-degree data available.")


# --- FOOTER ---
st.caption("ConstructPulse v2.0 • AI Construction Law, Supply Chain & Material Price Forecaster • Operating on LanceDB & PyG")
