"""
PowerLaw Behind-The-Graph Tabular Inspector
Time-Slider Historical State Machine, Time-Series Metrics, and GNN Tensor Tensors
"""

import json
import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import plotly.express as px
import plotly.graph_objects as go

from src.storage.dashboard_data import get_graph_store
from src.models.dual_layer_model import NodeType

st.set_page_config(
    page_title="Behind-The-Graph Tabular Inspector",
    page_icon="⏱️",
    layout="wide"
)

# Header
st.title("⏱️ Behind-The-Graph Tabular Inspector & Time Machine")
st.markdown("""
Every node in the topological graph is backed by a **timestamped time-series state machine**.
Scrub across historical dates to observe categorical state transitions, quantitative metric evolutions, 
and inspect the compiled **GNN Feature Tensors**.
""")

# Load store & history
try:
    store = get_graph_store()
    history_df = store.get_history_df()
    nodes = store.get_nodes()
    node_dict = {n.id: n for n in nodes}
except Exception as e:
    st.error(f"Failed to load temporal history: {e}")
    st.stop()

if history_df.empty:
    st.warning("No behind-the-graph tabular history records found.")
    st.stop()


# Ensure timestamp formatting
history_df["timestamp"] = history_df["timestamp"].astype(str)
available_dates = sorted(list(history_df["timestamp"].unique()))


# --- INTERACTIVE TIME-SLIDER CONTROL PANEL ---
st.markdown("### 🎛️ Temporal State Controls")

slider_col, mode_col = st.columns([3, 1])

with slider_col:
    selected_date = st.select_slider(
        "📅 Historical Snapshot Date (Timestamp T):",
        options=available_dates,
        value=available_dates[-1],
        help="Select any date to freeze and inspect the entire system's state at that moment in time."
    )

with mode_col:
    view_mode = st.radio("Display Scope:", ["Exact Snapshot at T", "Cumulative History Up to T"], index=0)


# --- SIDEBAR FILTERS ---
st.sidebar.header("🔍 Filter Temporal Ledger")

# Filter by Node Type
all_node_types = sorted(list({n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type) for n in nodes}))
selected_types = st.sidebar.multiselect("Node Type", all_node_types, default=all_node_types)

# Filter by Node ID
available_nodes_filtered = [
    n.id for n in nodes
    if (n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type)) in selected_types
]
selected_nodes = st.sidebar.multiselect("Specific Node IDs", sorted(available_nodes_filtered), default=[])

# Filter by Metric Name
all_metrics = sorted(list(history_df["metric_name"].dropna().unique()))
selected_metrics = st.sidebar.multiselect("Metric Name", all_metrics, default=[])

# Apply Scope Filters
if view_mode == "Exact Snapshot at T":
    scoped_df = history_df[history_df["timestamp"] == selected_date].copy()
else:
    scoped_df = history_df[history_df["timestamp"] <= selected_date].copy()

if selected_types:
    scoped_df = scoped_df[scoped_df["node_type"].isin(selected_types)]

if selected_nodes:
    scoped_df = scoped_df[scoped_df["node_id"].isin(selected_nodes)]

if selected_metrics:
    scoped_df = scoped_df[scoped_df["metric_name"].isin(selected_metrics)]


# --- TOP SNAPSHOT KPI BAR ---
kpi1, kpi2, kpi3, kpi4 = st.columns(4)

with kpi1:
    st.metric("Snapshot Date (T)", selected_date)

with kpi2:
    active_nodes_at_t = scoped_df["node_id"].nunique()
    st.metric("Nodes Observed", active_nodes_at_t, delta=f"{len(nodes)} total in graph")

with kpi3:
    st.metric("Temporal Records", len(scoped_df))

with kpi4:
    stressed_count = scoped_df[scoped_df["state_value"].isin(["CRITICAL", "PEAK", "DEFICIT", "HIGH_COMPLIANCE"])]["node_id"].nunique()
    st.metric("High-Stressed Nodes", stressed_count, delta="Market / Compliance Alert" if stressed_count > 0 else "Normal Regimes", delta_color="inverse")

st.divider()


# --- MAIN TABS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🕒 State Machine Snapshot at T",
    "📈 Time-Series Metric Curves",
    "🔄 Categorical State Transitions",
    "📑 Full Tabular Ledger & Export",
    "🧠 GNN Tensor Inspector"
])


# ============================================================================
# TAB 1: SNAPSHOT STATE MACHINE AT T
# ============================================================================
with tab1:
    st.subheader(f"🕒 System Nodal State Machine on {selected_date}")
    st.markdown("Visual status cards for key economic indicators and mandated actions on this date.")

    # Get latest record per node at or before T
    latest_at_t = history_df[history_df["timestamp"] <= selected_date].sort_values("timestamp").groupby("node_id").last().reset_index()

    # Cards for top critical indicators
    spotlight_nodes = ["CO2_ALLOWANCE", "GAS_TTF", "ACT_GAS_90PCT_INJECT", "C24_BASIC_METALS", "ACT_CBAM_SURRENDER"]
    card_cols = st.columns(len(spotlight_nodes))

    for idx, nid in enumerate(spotlight_nodes):
        row = latest_at_t[latest_at_t["node_id"] == nid]
        with card_cols[idx]:
            if not row.empty:
                r = row.iloc[0]
                val = r["metric_value"]
                unit = r["unit"] or ""
                st_val = r["state_value"] or "UNKNOWN"
                metric_n = str(r["metric_name"]).replace("_", " ").title() if pd.notna(r["metric_name"]) else "Metric"
                val_disp = f"{float(val):.1f}" if pd.notna(val) else "N/A"
                delta_val = r.get("delta", 0.0)

                bg_color = "#1E293B"
                border_color = "#334155"
                if st_val in ["CRITICAL", "DEFICIT"]:
                    border_color = "#EF4444"
                elif st_val in ["PEAK", "HIGH_COMPLIANCE"]:
                    border_color = "#F59E0B"
                elif st_val in ["TARGET_MET", "ENFORCED"]:
                    border_color = "#10B981"

                st.markdown(f"""
                <div style="background-color: {bg_color}; border: 2px solid {border_color}; border-radius: 8px; padding: 12px; text-align: center;">
                    <div style="font-size: 0.75rem; color: #94A3B8; font-weight: bold;">{nid}</div>
                    <div style="font-size: 1.6rem; font-weight: bold; color: #F9FAFB; margin: 4px 0;">
                        {val_disp} <span style="font-size: 0.9rem; color: #9CA3AF;">{unit}</span>
                    </div>
                    <div style="font-size: 0.75rem; color: #CBD5E1; margin-bottom: 4px;">{metric_n}</div>
                    <div style="background-color: {border_color}33; color: #F3F4F6; font-size: 0.75rem; padding: 2px 6px; border-radius: 4px; font-weight: bold; display: inline-block;">
                        STATE: {st_val}
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="background-color: #1E293B; border: 1px dashed #475569; border-radius: 8px; padding: 12px; text-align: center;">
                    <div style="font-size: 0.75rem; color: #94A3B8;">{nid}</div>
                    <div style="font-size: 1.1rem; color: #64748B; margin: 12px 0;">No Record at T</div>
                </div>
                """, unsafe_allow_html=True)

    st.markdown("<br/>", unsafe_allow_html=True)
    st.markdown(f"#### 📋 Snapshot Data Grid on {selected_date}")
    
    snapshot_display = scoped_df[[
        "node_id", "node_type", "state_value", "metric_name", "metric_value", "delta", "unit", "source_type", "confidence", "notes"
    ]].copy()
    snapshot_display.columns = [
        "Node ID", "Type", "State Value", "Metric Name", "Value", "Delta (Δ)", "Unit", "Source Type", "Confidence", "Notes"
    ]
    st.dataframe(snapshot_display, width="stretch", height=350, hide_index=True)


# ============================================================================
# TAB 2: TIME-SERIES METRIC CURVES
# ============================================================================
with tab2:
    st.subheader("📈 Quantitative Metric Trajectories Across Time")
    st.markdown("Track continuous metric dynamics behind each graph node.")

    # Multi-node selector for time series
    ts_available_nodes = sorted(list(history_df["node_id"].unique()))
    default_ts_nodes = [n for n in ["CO2_ALLOWANCE", "GAS_TTF", "ACT_GAS_90PCT_INJECT", "C24_BASIC_METALS"] if n in ts_available_nodes]
    selected_ts_nodes = st.multiselect("Select Nodes to Plot:", ts_available_nodes, default=default_ts_nodes)

    if selected_ts_nodes:
        plot_df = history_df[history_df["node_id"].isin(selected_ts_nodes)].copy()
        plot_df = plot_df.sort_values("timestamp")

        fig = px.line(
            plot_df,
            x="timestamp",
            y="metric_value",
            color="node_id",
            markers=True,
            title="Behind-The-Graph Metric Readings Over Time",
            hover_data=["metric_name", "unit", "state_value", "delta"]
        )
        
        # Add a vertical reference line for selected date T
        fig.add_vline(
            x=selected_date,
            line_width=2,
            line_dash="dash",
            line_color="#EF4444",
            annotation_text=f"Selected T: {selected_date}",
            annotation_position="top left"
        )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0B0F19",
            plot_bgcolor="#0E1117",
            height=450,
            xaxis_title="Timestamp",
            yaxis_title="Metric Reading",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, width="stretch")

        st.markdown("#### 🔍 Single-Node Deep Dive")
        single_node = st.selectbox("Focus on Single Node Metric:", selected_ts_nodes, index=0)
        single_df = history_df[history_df["node_id"] == single_node].sort_values("timestamp")
        
        if not single_df.empty:
            sc1, sc2 = st.columns([2, 1])
            with sc1:
                metric_name_curr = single_df.iloc[0]["metric_name"]
                unit_curr = single_df.iloc[0]["unit"]
                
                chart_single = alt.Chart(single_df).mark_area(
                    line={"color": "#3B82F6"},
                    color=alt.Gradient(
                        gradient="linear",
                        stops=[alt.GradientStop(color="#3B82F6", offset=0), alt.GradientStop(color="#1E3A8A00", offset=1)],
                        x1=1, x2=1, y1=1, y2=0
                    )
                ).encode(
                    x=alt.X("timestamp:T", title="Date"),
                    y=alt.Y("metric_value:Q", title=f"{metric_name_curr} ({unit_curr})"),
                    tooltip=["timestamp", "metric_value", "state_value", "delta"]
                ).properties(height=260)
                st.altair_chart(chart_single, width="stretch")
                
            with sc2:
                st.markdown(f"**Node:** `{single_node}`")
                st.markdown(f"**Metric:** `{metric_name_curr}`")
                st.markdown(f"**Unit:** `{unit_curr}`")
                st.markdown(f"**Earliest Value:** `{single_df.iloc[0]['metric_value']} {unit_curr}`")
                st.markdown(f"**Latest Value:** `{single_df.iloc[-1]['metric_value']} {unit_curr}`")
                min_v = single_df['metric_value'].min()
                max_v = single_df['metric_value'].max()
                st.markdown(f"**Historical Min / Max:** `{min_v} / {max_v}`")
    else:
        st.info("Select at least one node to visualize its quantitative time-series trajectory.")


# ============================================================================
# TAB 3: CATEGORICAL STATE TRANSITIONS
# ============================================================================
with tab3:
    st.subheader("🔄 Categorical State Machine Transitions")
    st.markdown("Visual sequence of discrete state shifts (e.g. `PROPOSED` → `ACTIVE` → `ENFORCED`).")

    transition_nodes = st.multiselect(
        "Select Nodes for State Timeline:",
        sorted(list(history_df["node_id"].unique())),
        default=[n for n in ["ACT_CBAM_SURRENDER", "ACT_GAS_90PCT_INJECT", "CO2_ALLOWANCE"] if n in history_df["node_id"].unique()]
    )

    if transition_nodes:
        state_df = history_df[history_df["node_id"].isin(transition_nodes)].sort_values("timestamp")
        
        fig_state = px.scatter(
            state_df,
            x="timestamp",
            y="node_id",
            color="state_value",
            size=[14] * len(state_df),
            hover_data=["metric_value", "unit", "notes"],
            title="State Transition Matrix Across Time"
        )
        fig_state.update_layout(
            template="plotly_dark",
            paper_bgcolor="#0B0F19",
            plot_bgcolor="#0E1117",
            height=320,
            xaxis_title="Date",
            yaxis_title="Node"
        )
        st.plotly_chart(fig_state, width="stretch")


# ============================================================================
# TAB 4: FULL TABULAR LEDGER & EXPORT
# ============================================================================
with tab4:
    st.subheader("📑 Full Behind-The-Graph Ledger (`node_tabular_history`)")
    st.markdown("Exhaustive tabular records of all timestamped states and numeric observations.")

    st.dataframe(history_df, width="stretch", height=500)

    dcol1, dcol2 = st.columns(2)
    with dcol1:
        csv_history = history_df.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download Full Ledger as CSV", csv_history, "node_tabular_history.csv", "text/csv", width="stretch")
    with dcol2:
        json_history = history_df.to_json(orient="records", date_format="iso").encode("utf-8")
        st.download_button("📥 Download Full Ledger as JSON", json_history, "node_tabular_history.json", "application/json", width="stretch")


# ============================================================================
# TAB 5: GNN TENSOR INSPECTOR
# ============================================================================
with tab5:
    st.subheader("🧠 Compiled GNN Feature Tensors")
    st.markdown(f"""
    The dual-layer engine compiles graph topology and behind-the-graph temporal metrics as of **{selected_date}** into 
    standardized tensors for downstream Graph Neural Network (GNN) training (PyTorch Geometric / DGL format).
    """)

    gnn_data = store.export_gnn_dataset(as_of=selected_date)

    gt1, gt2, gt3 = st.columns(3)
    with gt1:
        st.metric("Total Graph Nodes (N)", gnn_data.get("num_nodes", 0))
    with gt2:
        st.metric("Total Graph Edges (M)", gnn_data.get("num_edges", 0))
    with gt3:
        st.metric("Node Feature Dimension (D)", gnn_data.get("feature_dim", 0))

    st.markdown("---")
    st.markdown(r"#### 1. Node Feature Matrix $X \in \mathbb{R}^{N \times D}$")
    st.markdown(r"""
    Feature columns combine one-hot node types, normalized topological degrees, and temporal state aggregations:
    `[is_law, is_action, is_reaction, is_sector, is_commodity, is_economic_index, norm_in_degree, norm_out_degree, latest_metric_val, mean_metric_val, history_count]`
    """)

    feature_names = gnn_data.get("feature_names", [])
    node_ids = gnn_data.get("node_ids", [])
    X_matrix = gnn_data.get("X", [])

    if X_matrix and node_ids:
        x_df = pd.DataFrame(X_matrix, index=node_ids, columns=feature_names)
        st.dataframe(x_df, width="stretch", height=350)

    st.markdown(r"#### 2. Edge Index Tensor $E \in \mathbb{Z}^{2 \times M}$ (COO Format)")
    edge_idx = gnn_data.get("edge_index", [[], []])
    if edge_idx and len(edge_idx) == 2 and len(edge_idx[0]) > 0:
        edge_df = pd.DataFrame({
            "Edge ID": [f"E_{i}" for i in range(len(edge_idx[0]))],
            "Source Index": edge_idx[0],
            "Source Node": [node_ids[i] if i < len(node_ids) else str(i) for i in edge_idx[0]],
            "Target Index": edge_idx[1],
            "Target Node": [node_ids[i] if i < len(node_ids) else str(i) for i in edge_idx[1]],
            "Edge Weight": gnn_data.get("edge_weights", []),
            "Edge Type Index": gnn_data.get("edge_types", [])
        })
        st.dataframe(edge_df, width="stretch", height=300, hide_index=True)
    else:
        st.info("No edge index available.")
