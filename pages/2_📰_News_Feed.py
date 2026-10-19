"""
ConstructPulse Construction & Material News Feed
Real-Time Event Stream with Extracted Receptor Signals, Actions, and Reactions
"""

import json
import streamlit as st
import pandas as pd
import altair as alt
import plotly.express as px

from src.storage.dashboard_data import (
    get_all_news_data,
    submit_ingestion_job,
    get_graph_store
)

st.set_page_config(
    page_title="Economic News & Event Feed",
    page_icon="📰",
    layout="wide"
)

# Header
st.title("📰 Real-Time Economic & Trade News Feed")
st.markdown("""
Monitors international trade, energy, and commodity news. Extracts **Receptor Signals** (root-cause economic shocks)
and maps them directly into **Action** and **Reaction** nodes within the Dual-Layer Graph Store.
""")

# Load news
news_items = get_all_news_data()

# --- TOP CONTROLS & SYNC TRIGGER ---
ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([2, 1, 1])

with ctrl_col1:
    search_term = st.text_input("🔍 Search News (Headline, Signal, Asset, Summary)", "")

with ctrl_col2:
    if st.button("🔄 Enqueue News Ingestion", width="stretch", help="Submits asynchronous ingestion job to queue"):
        try:
            job = submit_ingestion_job(source_id="OFFICIAL_NEWS", limit=50)
            st.success(f"News ingestion job `{job['job_id']}` submitted to queue.")
        except Exception as e:
            st.error(f"Submission error: {e}")

with ctrl_col3:
    if st.button("⚡ Sync to Graph Store", width="stretch", help="Sync graph store from committed canonical records"):
        st.info("Direct mutations from UI are disabled. Ingestion jobs process asynchronously via CLI `python -m scripts.ops.ingest --consume-queue`.")

# --- SIDEBAR FILTERS ---
st.sidebar.header("Filter News Stream")

# Source filter
all_sources = sorted(list({n.get("source_name", "Unknown") for n in news_items}))
selected_sources = st.sidebar.multiselect("Source Outlet", all_sources, default=all_sources)

# Direction filter
all_directions = sorted(list({n.get("direction", "STABLE") for n in news_items}))
selected_directions = st.sidebar.multiselect("Impact Direction", all_directions, default=all_directions)

# Target Asset filter
all_assets = sorted(list({n.get("target_asset", "GENERAL") for n in news_items}))
selected_assets = st.sidebar.multiselect("Target Asset / Sector", all_assets, default=[])

# Timeframe filter
all_timeframes = sorted(list({n.get("timeframe", "3_MONTHS") for n in news_items}))
selected_timeframes = st.sidebar.multiselect("Timeframe", all_timeframes, default=all_timeframes)

# Apply filters
filtered_news = []
for n in news_items:
    if n.get("source_name") not in selected_sources:
        continue
    if n.get("direction") not in selected_directions:
        continue
    if selected_assets and n.get("target_asset") not in selected_assets:
        continue
    if n.get("timeframe") not in selected_timeframes:
        continue
    if search_term:
        st_term = search_term.lower()
        searchable = f"{n.get('title', '')} {n.get('summary', '')} {n.get('receptor_signal', '')} {n.get('target_asset', '')}".lower()
        if st_term not in searchable:
            continue
    filtered_news.append(n)


# --- KPI METRICS ---
k1, k2, k3, k4 = st.columns(4)

with k1:
    st.metric("Total Articles", len(filtered_news), delta=f"{len(news_items)} total recorded")

with k2:
    unique_signals = len({n.get("receptor_signal") for n in filtered_news if n.get("receptor_signal")})
    st.metric("Receptor Signals", unique_signals)

with k3:
    inc_count = sum(1 for n in filtered_news if n.get("direction") == "INCREASE")
    st.metric("Bullish / Cost Shock (▲)", inc_count, delta="Cost / Price Surge" if inc_count > 0 else None)

with k4:
    dec_count = sum(1 for n in filtered_news if n.get("direction") == "DECREASE")
    st.metric("Bearish / Easing Shock (▼)", dec_count, delta="Price / Demand Drop" if dec_count > 0 else None, delta_color="inverse")

st.divider()


# --- TABS ---
tab1, tab2, tab3, tab4 = st.tabs([
    "📡 Live Signal Feed (Stream)",
    "📈 Impact Analytics & Charts",
    "🕸️ Graph Store Ingestion Log",
    "📊 Raw News Data Grid"
])


# ============================================================================
# TAB 1: LIVE SIGNAL FEED
# ============================================================================
with tab1:
    st.subheader("📡 Real-Time News Stream & Extracted Signals")

    if not filtered_news:
        st.info("No news articles match your filter criteria.")
    else:
        for idx, item in enumerate(filtered_news):
            title = item.get("title", "Untitled News Event")
            pub_date = item.get("published_date", "N/A")
            source = item.get("source_name", "News Source")
            url = item.get("source_url", "")
            summary = item.get("summary", "")
            receptor = item.get("receptor_signal", "MACRO_EVENT")
            asset = item.get("target_asset", "MARKET")
            metric = item.get("metric", "PRICE")
            direction = item.get("direction")
            raw_delta = item.get("delta_value")
            try:
                delta_val = float(raw_delta) if raw_delta is not None else None
            except (ValueError, TypeError):
                delta_val = None
            delta_unit = item.get("delta_unit") or ""
            delta_text = f"{delta_val:+.1f} {delta_unit}" if delta_val is not None else "Unavailable"
            timeframe = item.get("timeframe") or "Unknown"

            dir_color = "#DC2626" if direction == "INCREASE" else ("#10B981" if direction == "DECREASE" else "#6B7280")
            dir_icon = "▲ INCREASE" if direction == "INCREASE" else ("▼ DECREASE" if direction == "DECREASE" else ("▶ STABLE" if direction == "STABLE" else "UNKNOWN"))

            with st.container():
                st.markdown(f"""
                <div style="background-color: #0E1117; border: 1px solid #262730; border-radius: 8px; padding: 16px; margin-bottom: 14px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 0.85rem; color: #94A3B8; font-weight: 600;">📅 {pub_date} | 📰 {source}</span>
                        <span style="background-color: #1E293B; color: #93C5FD; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; border: 1px solid #3B82F6;">
                            RECEPTOR: {receptor}
                        </span>
                    </div>
                    <h4 style="margin: 4px 0 8px 0; color: #F9FAFB;">{title}</h4>
                    <p style="color: #CBD5E1; font-size: 0.92rem; margin-bottom: 12px; line-height: 1.4;">
                        {summary}
                    </p>
                    <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
                        <span style="background-color: #1E3A8A; color: #BFDBFE; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem;">
                            🎯 Target: <strong>{asset}</strong>
                        </span>
                        <span style="background-color: #374151; color: #F3F4F6; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem;">
                            📊 Metric: <strong>{metric}</strong>
                        </span>
                        <span style="background-color: {dir_color}; color: white; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: bold;">
                            {dir_icon} ({delta_text})
                        </span>
                        <span style="background-color: #111827; color: #9CA3AF; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; border: 1px solid #374151;">
                            ⏱️ Horizon: {timeframe}
                        </span>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                actions_list = item.get("actions", [])
                reactions_list = item.get("reactions", [])

                with st.expander(f"⚡ Extracted Actions ({len(actions_list)}) & Reactions ({len(reactions_list)})", expanded=False):
                    act_col, react_col = st.columns(2)
                    with act_col:
                        st.markdown("**⚡ Extracted Actions:**")
                        if actions_list:
                            for a in actions_list:
                                st.markdown(f"- **{a.get('action_type', 'ACTION')}** by *{a.get('actor', 'N/A')}*: {a.get('description', '')} (`{a.get('magnitude', 0)} {a.get('unit', '')}`)")
                        else:
                            st.caption("No explicit actions extracted.")

                    with react_col:
                        st.markdown("**🎯 Extracted Reactions:**")
                        if reactions_list:
                            for rx in reactions_list:
                                r_dir = rx.get('direction', 'STABLE')
                                r_icon = "▲" if r_dir == "INCREASE" else ("▼" if r_dir == "DECREASE" else "▶")
                                st.markdown(f"- **{rx.get('reaction_type', 'REACTION')}** on `{rx.get('affected_entity', 'N/A')}`: {r_icon} {r_dir} (`{rx.get('magnitude', 0)} {rx.get('unit', '')}`) [{rx.get('timeframe', 'IMMEDIATE')}]")
                        else:
                            st.caption("No explicit reactions extracted.")

                if url and url != "#":
                    st.caption(f"Source Link: [{url}]({url})")


# ============================================================================
# TAB 2: SIGNAL ANALYTICS
# ============================================================================
with tab2:
    st.subheader("📈 Cross-Asset Impact Distribution & Sentiment Analysis")

    if filtered_news:
        df_news = pd.DataFrame(filtered_news)

        col_a1, col_a2 = st.columns(2)

        with col_a1:
            st.markdown("#### 🎯 Shocks by Target Asset")
            asset_counts = df_news["target_asset"].value_counts().reset_index()
            asset_counts.columns = ["Target Asset", "Frequency"]
            chart_asset = alt.Chart(asset_counts).mark_bar(color="#3B82F6", cornerRadius=4).encode(
                x=alt.X("Frequency:Q", title="Number of Impact Events"),
                y=alt.Y("Target Asset:N", sort="-x", title="Asset / Sector"),
                tooltip=["Target Asset", "Frequency"]
            ).properties(height=300)
            st.altair_chart(chart_asset, width="stretch")

        with col_a2:
            st.markdown("#### 🧭 Impact Direction Distribution")
            dir_counts = df_news["direction"].value_counts().reset_index()
            dir_counts.columns = ["Direction", "Count"]
            
            fig = px.pie(
                dir_counts,
                values="Count",
                names="Direction",
                color="Direction",
                color_discrete_map={"INCREASE": "#EF4444", "DECREASE": "#10B981", "STABLE": "#6B7280"},
                hole=0.45
            )
            fig.update_layout(margin=dict(l=20, r=20, t=20, b=20), height=300, paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch")

        st.markdown("---")
        st.markdown("#### ⏱️ Chronological Signal Timeline")
        timeline_df = df_news.sort_values("published_date")
        chart_time = alt.Chart(timeline_df).mark_circle(size=120).encode(
            x=alt.X("published_date:T", title="Publication Date"),
            y=alt.Y("target_asset:N", title="Affected Asset"),
            color=alt.Color("direction:N", scale=alt.Scale(domain=["INCREASE", "DECREASE", "STABLE"], range=["#EF4444", "#10B981", "#6B7280"])),
            tooltip=["published_date", "title", "receptor_signal", "delta_value", "delta_unit"]
        ).properties(height=280)
        st.altair_chart(chart_time, width="stretch")
    else:
        st.info("No data available for analytics.")


# ============================================================================
# TAB 3: GRAPH STORE INGESTION LOG
# ============================================================================
with tab3:
    st.subheader("🕸️ Stored News Evidence References")
    st.markdown("""
    Canonical fact and evidence references are shown when present in the stored record.
    Missing references or measurements remain unknown.
    """)

    mapping_rows = []
    for item in filtered_news:
        nid = item.get("news_id", "")
        raw_val = item.get("delta_value")
        try:
            val_f = float(raw_val) if raw_val is not None else None
            delta_str = f"{val_f:+.1f} {item.get('delta_unit') or ''}" if val_f is not None else None
        except (ValueError, TypeError):
            delta_str = None

        mapping_rows.append({
            "Source News ID": nid,
            "Published": item.get("published_date"),
            "Fact Version IDs": item.get("fact_version_ids", []),
            "Evidence Document IDs": item.get("evidence_document_ids", []),
            "Reported Target": item.get("target_asset"),
            "Reported Change": delta_str,
            "Direction": item.get("direction")
        })

    st.dataframe(pd.DataFrame(mapping_rows), width="stretch", hide_index=True)


# ============================================================================
# TAB 4: RAW DATA GRID & EXPORT
# ============================================================================
with tab4:
    st.subheader("📊 Raw News Corpus")
    df_full = pd.DataFrame(filtered_news)
    st.dataframe(df_full, width="stretch", height=450)

    csv_data = df_full.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Download Scraped News as CSV", csv_data, "constructpulse_economic_news.csv", "text/csv", width="stretch")
