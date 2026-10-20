"""
ConstructPulse GNN Modeling Lab & Downstream Tensor Exporter
Prepares topological graphs and behind-the-graph temporal tabular data for downstream Graph Neural Networks (PyG, DGL, TGAT).
"""

import os
import json
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from dotenv import load_dotenv

from src.storage.dashboard_data import get_graph_store
from src.models.gnn_dataset import DualLayerGNNDataset

load_dotenv()

st.set_page_config(
    page_title="GNN Modeling Lab — ConstructPulse",
    page_icon="🤖",
    layout="wide"
)

# Load graph store
try:
    store = get_graph_store()
    gnn_ds = DualLayerGNNDataset(store)
except Exception as e:
    st.error(f"Failed to initialize GNN Modeling Lab: {e}")
    st.stop()

st.title("🤖 Graph Neural Network (GNN) Modeling Lab")
st.markdown("""
Interface compiling the **Dual-Layer Topological Graph** and **Behind-the-Graph Tabular Ledger** 
into structured tensors for downstream Graph Neural Networks (e.g. PyG, DGL, TGAT, ST-GCN).
""")

# --- SIDEBAR TEMPORAL SLICE ---
with st.sidebar:
    st.header("⏳ Temporal Snapshot Generator")
    history_df = store.get_history_df()
    if not history_df.empty and "timestamp" in history_df.columns:
        dates = sorted(list(history_df["timestamp"].astype(str).unique()))
        all_dates = dates + ["(Latest)"]
    else:
        all_dates = ["(Latest)"]
    selected_snapshot_date = st.select_slider("Snapshot Horizon Date:", options=all_dates, value="(Latest)")
    target_date = None if selected_snapshot_date == "(Latest)" else selected_snapshot_date

# Generate Tensor Snapshot
try:
    snapshot = gnn_ds.build_snapshot(as_of_date=target_date)
except Exception as e:
    st.error(f"Error compiling GNN snapshot: {e}")
    st.stop()

N = snapshot.get("num_nodes", 0)
M = snapshot.get("num_edges", 0)
D = snapshot.get("feature_dim", 0)
X = snapshot.get("X", np.zeros((0, 0)))
edge_index = snapshot.get("edge_index", np.zeros((2, 0), dtype=int))

# --- KPI METRICS ---
m1, m2, m3, m4 = st.columns(4)
m1.metric("Active Nodes (N)", N)
m2.metric("Active Edges (M)", M)
m3.metric("Feature Dimension (D)", D)
sparsity = 1.0 - (M / (N * (N - 1))) if N > 1 else 1.0
m4.metric("Graph Sparsity", f"{sparsity * 100:.2f}%")

st.divider()

# --- 1. NODE FEATURE MATRIX (X) ---
st.subheader("1. Node Feature Matrix X ∈ ℝ^(N × D)")
st.caption("Combines static topological roles (one-hot type, degrees) with behind-the-graph temporal aggregations.")

feature_names = snapshot.get("feature_names", [f"feat_{i}" for i in range(D)])
if N > 0:
    feat_df = pd.DataFrame(X, index=snapshot.get("node_ids", []), columns=feature_names)
else:
    feat_df = pd.DataFrame(columns=feature_names)

tab_feat1, tab_feat2 = st.tabs(["📊 Feature Matrix Data Table", "🔥 Feature Heatmap"])

with tab_feat1:
    if N > 0:
        st.dataframe(feat_df.style.format(precision=3), width="stretch")
    else:
        st.info("No nodes available at this temporal horizon.")

with tab_feat2:
    if N > 0 and D > 0:
        fig_heat = px.imshow(
            feat_df,
            labels=dict(x="Feature Dimension", y="Node ID", color="Value"),
            x=feature_names,
            y=snapshot.get("node_ids", []),
            color_continuous_scale="Viridis",
            aspect="auto",
            height=500
        )
        fig_heat.update_layout(margin=dict(l=20, r=20, t=30, b=20), paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_heat, width="stretch")
    else:
        st.info("Insufficient dimensions for feature heatmap visualization.")

st.divider()

# --- 2. ADJACENCY MATRIX & EDGE INDEX ---
st.subheader("2. Edge Index E ∈ ℤ^(2 × M) & Adjacency Matrix A ∈ {0, 1}^(N × N)")

col_a1, col_a2 = st.columns([1, 1])

with col_a1:
    st.markdown("**Edge Index Tensor Sample**")
    if M > 0 and len(edge_index) == 2 and len(edge_index[0]) > 0:
        node_ids = snapshot.get("node_ids", [])
        edge_df = pd.DataFrame({
            "Source Index (u)": edge_index[0],
            "Source Node": [node_ids[u] if u < len(node_ids) else str(u) for u in edge_index[0]],
            "Target Index (v)": edge_index[1],
            "Target Node": [node_ids[v] if v < len(node_ids) else str(v) for v in edge_index[1]],
            "Edge Weight": snapshot.get("edge_weights", [1.0] * M)
        })
        st.dataframe(edge_df.head(15), width="stretch", hide_index=True)
        st.caption(f"Showing first {min(15, M)} of {M} directed edges.")
    else:
        st.info("No edges active at this timestamp.")

with col_a2:
    st.markdown("**Adjacency Heatmap**")
    if N > 0 and M > 0:
        A = np.zeros((N, N), dtype=int)
        for u, v in zip(edge_index[0], edge_index[1]):
            if u < N and v < N:
                A[u, v] = 1
        fig_adj = px.imshow(
            A,
            labels=dict(x="Target Index", y="Source Index", color="Connected"),
            color_continuous_scale=["#f0f0f0", "#1E90FF"],
            height=320
        )
        fig_adj.update_layout(coloraxis_showscale=False, margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_adj, width="stretch")
    else:
        st.info("No adjacency data available.")

st.divider()

# --- 3. TEMPORAL GNN (READ-ONLY) ---
# Pages never train or mutate models or data (AGENTS.md); this shows a checkpoint trained offline, if any.
st.subheader("3. 🧠 Temporal GNN (read-only)")
st.caption("Paused: the GNN is not yet shown to beat the VAT-aware autoregressive baseline in the rolling-origin backtest.")

trainer = st.session_state.get("tgnn_trainer")
if trainer is None and st.button("🧠 Load trained GNN checkpoint"):
    from src.gnn.trainer import TGNNTrainer

    trainer = TGNNTrainer(store)
    st.session_state.tgnn_trained = trainer.load_checkpoint()
    st.session_state.tgnn_trainer = trainer

if trainer is None:
    st.info("Load the offline-trained checkpoint to inspect predictions and simulations.")
elif not st.session_state.tgnn_trained:
    st.info("No trained checkpoint found. Training is an offline job, not a dashboard action.")
else:
    tab_loss, tab_shock, tab_preds = st.tabs(["📉 Training History", "🔮 Counterfactual Shock", "🎯 Predictions"])
    with tab_loss:
        if trainer.training_history:
            hist = pd.DataFrame(trainer.training_history)
            fig_loss = px.line(hist, x="epoch", y=[c for c in ("total_loss", "state_loss", "link_loss", "risk_loss") if c in hist],
                               labels=dict(value="Loss", epoch="Epoch", variable="Component"), title="Checkpoint loss history")
            fig_loss.update_layout(margin=dict(l=20, r=20, t=40, b=20), paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_loss, width="stretch")
        else:
            st.info("Checkpoint has no recorded training history.")
    with tab_shock:
        nodes_df = store.get_nodes_df()
        candidates = nodes_df[nodes_df["node_type"].isin(["LAW", "ACTION"])]["id"].tolist() if not nodes_df.empty else []
        candidates = candidates or snapshot.get("node_ids", [])
        c_s1, c_s2 = st.columns([2, 1])
        shock_target = c_s1.selectbox("Shock Target Node (Policy / Action):", options=candidates, index=0 if candidates else None)
        shock_mag = c_s2.slider("Shock Magnitude (Delta):", min_value=-50.0, max_value=50.0, value=25.0, step=5.0)
        if shock_target and st.button("🔮 Run Counterfactual Shock Simulation", width="stretch"):
            shock_df = trainer.simulate_shock(shock_target, shock_magnitude=shock_mag)
            st.dataframe(shock_df, width="stretch", hide_index=True)
    with tab_preds:
        st.dataframe(trainer.predict(), width="stretch", hide_index=True)

# --- 4. EXPORT TENSORS ---
st.divider()
st.subheader("4. Export Graph Dataset")
edge_weights = snapshot.get("edge_weights", np.array([]))
export_dict = {
    "as_of_date": snapshot.get("as_of_date", str(target_date or "latest")),
    "num_nodes": N,
    "num_edges": M,
    "feature_names": feature_names,
    "node_ids": snapshot.get("node_ids", []),
    "X": X.tolist() if hasattr(X, "tolist") else [],
    "edge_index": edge_index.tolist() if hasattr(edge_index, "tolist") else [],
    "edge_weights": edge_weights.tolist() if hasattr(edge_weights, "tolist") else []
}
json_export = json.dumps(export_dict, indent=2)

col_d1, col_d2 = st.columns(2)
with col_d1:
    st.download_button(
        label="📥 Download GNN Tensor Dataset (JSON)",
        data=json_export,
        file_name=f"constructpulse_gnn_dataset_{snapshot.get('as_of_date', 'latest')}.json",
        mime="application/json",
        width="stretch"
    )

with col_d2:
    if st.button("📦 Prepare Full Graph Export", width="stretch"):
        try:
            from src.gnn.graph_exporter import GNNGraphExporter

            full_export = GNNGraphExporter(store).export_json(indent=2)
            st.download_button(
                label="Download Full Multimodal Graph & Centralities (JSON)",
                data=full_export,
                file_name="constructpulse_full_graph_export.json",
                mime="application/json",
                width="stretch",
            )
        except Exception as e:
            st.info(f"Full export unavailable: {e}")

