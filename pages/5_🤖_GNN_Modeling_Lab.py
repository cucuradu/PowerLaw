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
from src.gnn.graph_exporter import GNNGraphExporter
from src.gnn.trainer import TGNNTrainer
from src.gnn.temporal_gnn import HAS_TORCH

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
    exporter = GNNGraphExporter(store)
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
        all_dates = ["2022-06-30", "2023-01-01", "2023-06-30", "2023-12-31", "2024-06-30", "2024-12-31", "2025-12-31", "(Latest)"]
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

# --- 3. TEMPORAL GNN TRAINING & SHOCK SIMULATION LAB ---
st.divider()
st.subheader("3. 🧠 Live Spatio-Temporal GNN Modeling & Simulation")
st.caption("Train a Temporal GNN directly on the topological graph and behind-the-graph tabular ledger, then simulate policy shocks.")

tab_train, tab_shock, tab_preds = st.tabs([
    "🏋️ Train Spatio-Temporal GNN",
    "🔮 Counterfactual Policy Shock Simulator",
    "🎯 Model Predictions & Node Risk Ledger"
])

# Initialize session state for trainer if needed with disk checkpoint persistence
if "tgnn_trainer" not in st.session_state:
    trainer = TGNNTrainer(store)
    is_loaded = trainer.load_checkpoint()
    st.session_state.tgnn_trainer = trainer
    st.session_state.tgnn_trained = is_loaded
    if is_loaded and trainer.training_history:
        st.session_state.train_results = {
            "final_loss": trainer.training_history[-1].get("total_loss", 0.1),
            "history": trainer.training_history
        }
    else:
        st.session_state.train_results = None

with tab_train:
    c_p1, c_p2, c_p3 = st.columns(3)
    epochs = c_p1.slider("Training Epochs:", min_value=5, max_value=80, value=25, step=5)
    hidden_dim = c_p2.selectbox("Hidden Dimension:", options=[32, 64, 128], index=1)
    lr = c_p3.select_slider("Learning Rate:", options=[0.001, 0.005, 0.01, 0.02, 0.05], value=0.01)

    if st.button("🚀 Train Temporal GNN", type="primary", use_container_width=True):
        with st.spinner("Training Temporal Graph Neural Network across chronological snapshots..."):
            trainer = TGNNTrainer(store, hidden_dim=hidden_dim, lr=lr)
            progress_bar = st.progress(0)
            status_text = st.empty()

            def train_callback(epoch, total_epochs, record):
                progress_bar.progress(epoch / total_epochs)
                status_text.text(f"Epoch {epoch}/{total_epochs} — Loss: {record['total_loss']:.4f}")

            res = trainer.train(epochs=epochs, callback=train_callback)
            trainer.save_checkpoint()
            st.session_state.tgnn_trainer = trainer
            st.session_state.tgnn_trained = True
            st.session_state.train_results = res
            status_text.empty()
            progress_bar.empty()
            st.success(f"✅ Training completed & model locked to disk in {epochs} epochs! Final Loss: {res['final_loss']:.4f}")

    if st.session_state.train_results:
        res = st.session_state.train_results
        hist = pd.DataFrame(res["history"])

        # Loss Plot
        fig_loss = px.line(
            hist,
            x="epoch",
            y=["total_loss", "state_loss", "link_loss", "risk_loss"],
            labels=dict(value="Loss", epoch="Epoch", variable="Component"),
            title="Multi-Task Loss Convergence Curve",
            color_discrete_map={
                "total_loss": "#1E90FF",
                "state_loss": "#2ECC71",
                "link_loss": "#E67E22",
                "risk_loss": "#E74C3C"
            }
        )
        fig_loss.update_layout(margin=dict(l=20, r=20, t=40, b=20), paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_loss, use_container_width=True)

with tab_shock:
    st.markdown("Inject a regulatory or market shock into a node and observe the impulse response cascading through downstream reactions and sectors.")
    
    nodes_df = store.get_nodes_df()
    law_and_action_nodes = []
    if not nodes_df.empty:
        law_and_action_nodes = nodes_df[nodes_df["node_type"].isin(["LAW", "ACTION"])]["id"].tolist()
    if not law_and_action_nodes:
        law_and_action_nodes = snapshot.get("node_ids", [])

    c_s1, c_s2 = st.columns([2, 1])
    shock_target = c_s1.selectbox("Shock Target Node (Policy / Action):", options=law_and_action_nodes, index=0 if law_and_action_nodes else None)
    shock_mag = c_s2.slider("Shock Magnitude (Delta):", min_value=-50.0, max_value=50.0, value=25.0, step=5.0)

    if st.button("🔮 Run Counterfactual Shock Simulation", use_container_width=True):
        with st.spinner("Propagating counterfactual shock through spatio-temporal GNN..."):
            trainer = st.session_state.tgnn_trainer
            if not st.session_state.tgnn_trained:
                trainer.train(epochs=15)
                st.session_state.tgnn_trained = True

            shock_df = trainer.simulate_shock(shock_target, shock_magnitude=shock_mag)
            
            # Chart Top 10 impacted nodes
            top_shocks = shock_df[shock_df["node_id"] != shock_target].head(10)
            if not top_shocks.empty:
                fig_shock = px.bar(
                    top_shocks,
                    x="impulse_response",
                    y="label",
                    color="impulse_response",
                    orientation="h",
                    color_continuous_scale="RdBu_r",
                    title=f"Impulse Response on Downstream Nodes from {shock_target} Shock ({shock_mag:+0.1f})",
                    labels=dict(impulse_response="State Impulse (Delta)", label="Target Node")
                )
                fig_shock.update_layout(margin=dict(l=20, r=20, t=40, b=20), paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_shock, use_container_width=True)

            st.dataframe(shock_df, use_container_width=True, hide_index=True)

with tab_preds:
    st.markdown("Predicted future states, volatility risk ratings, and model confidence scores for all nodes in the network:")
    trainer = st.session_state.tgnn_trainer
    if not st.session_state.tgnn_trained:
        st.info("Train the model above to generate predictions, or click below for rapid inference:")
        if st.button("⚡ Run Instant Prediction (Quick Train 10 Epochs)"):
            trainer.train(epochs=10)
            st.session_state.tgnn_trained = True
            st.rerun()
    else:
        preds_df = trainer.predict()
        st.dataframe(preds_df, use_container_width=True, hide_index=True)

st.divider()

# --- 4. CONSTRUCTION MULTI-FACTOR RISK & SENSITIVITY RANKING ---
st.subheader("4. 🏆 Construction Multi-Factor Risk & Sensitivity Ranking")
st.caption("Cross-sectional multi-factor scoring evaluating Sharpe-adjusted momentum, return volatility, and regulatory exposure.")

from src.quant.ranking import ConstructionFactorRanker
ranker = ConstructionFactorRanker(store)
factor_ranks_df = ranker.compute_factor_rankings()
if not factor_ranks_df.empty:
    st.dataframe(factor_ranks_df, use_container_width=True, hide_index=True)
else:
    st.info("Insufficient history for factor ranking.")

# --- 5. EXPORT TENSORS ---
st.divider()
st.subheader("5. Export Graph Dataset")
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
        use_container_width=True
    )

with col_d2:
    # Full Graph export via GNNGraphExporter
    try:
        full_export = exporter.export_json(indent=2)
        st.download_button(
            label="📦 Download Full Multimodal Graph & Centralities (JSON)",
            data=full_export,
            file_name=f"constructpulse_full_graph_export.json",
            mime="application/json",
            use_container_width=True
        )
    except Exception as e:
        st.info(f"Full export unavailable: {e}")

