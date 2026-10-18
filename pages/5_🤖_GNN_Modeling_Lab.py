"""
PowerLaw GNN Modeling Lab & Downstream Tensor Exporter
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

load_dotenv()

st.set_page_config(
    page_title="GNN Modeling Lab — PowerLaw",
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
feat_df = pd.DataFrame(X, index=snapshot.get("node_ids", []), columns=feature_names)

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

# --- 3. DOWNSTREAM GNN CODE BLUEPRINT ---
st.subheader("3. PyTorch Geometric (PyG) Integration Blueprint")
st.markdown("Use this template to load the dual-layer tensors directly into a Temporal GNN training pipeline:")

sample_x_list = X.tolist()[:3] if hasattr(X, "tolist") else []
sample_edge_list = edge_index.tolist() if hasattr(edge_index, "tolist") else []
edge_weights = snapshot.get("edge_weights", np.array([]))
sample_w_list = edge_weights[:5].tolist() if hasattr(edge_weights, "tolist") else []

code_sample = f'''import torch
from torch_geometric.data import Data

# 1. Load PowerLaw Dual-Layer Tensors
x = torch.tensor({sample_x_list}, dtype=torch.float) # Shape: [N, D] (Showing sample of {N})
edge_index = torch.tensor({sample_edge_list}, dtype=torch.long) # Shape: [2, M]
edge_weight = torch.tensor({sample_w_list}, dtype=torch.float)

# 2. Instantiate PyG Data Container
data = Data(x=x, edge_index=edge_index, edge_attr=edge_weight)

# 3. Spatio-Temporal GNN Architecture Definition
import torch.nn as nn
from torch_geometric.nn import GATConv

class TemporalLawReactionGNN(nn.Module):
    def __init__(self, in_channels={D}, hidden_dim=64, out_dim=1):
        super().__init__()
        self.conv1 = GATConv(in_channels, hidden_dim, heads=4)
        self.lstm = nn.LSTM(hidden_dim * 4, hidden_dim, batch_first=True)
        self.regressor = nn.Linear(hidden_dim, out_dim)

    def forward(self, x, edge_index, h_state=None):
        # Topological message passing (Law -> Action -> Reaction propagation)
        h = self.conv1(x, edge_index).relu()
        # Behind-the-graph temporal sequence processing
        out, h_state = self.lstm(h.unsqueeze(0), h_state)
        # Predict future Reaction magnitude or price impact
        preds = self.regressor(out.squeeze(0))
        return preds, h_state
'''
st.code(code_sample, language="python")

# --- 4. EXPORT TENSORS ---
st.divider()
st.subheader("4. Export Graph Dataset")
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
        file_name=f"powerlaw_gnn_dataset_{snapshot.get('as_of_date', 'latest')}.json",
        mime="application/json",
        width="stretch"
    )

with col_d2:
    # Full Graph export via GNNGraphExporter
    try:
        full_export = exporter.export_json(indent=2)
        st.download_button(
            label="📦 Download Full Multimodal Graph & Centralities (JSON)",
            data=full_export,
            file_name=f"powerlaw_full_graph_export.json",
            mime="application/json",
            width="stretch"
        )
    except Exception as e:
        st.info(f"Full export unavailable: {e}")
