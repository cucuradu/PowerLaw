"""
PowerLaw Dual-Layer GNN Dashboard
Executive Overview of the Dual-Layer Legal & Economic System
"""

import os
import json
import logging
import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import networkx as nx
from dotenv import load_dotenv

from src.storage.dashboard_data import (
    get_graph_store,
    get_system_kpis,
    get_all_laws_data,
    get_all_news_data
)

load_dotenv()

# Streamlit Page Setup
st.set_page_config(
    page_title="PowerLaw: Dual-Layer Legal & Economic Platform",
    page_icon="⚖️",
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
    .kpi-sub {
        color: #10B981;
        font-size: 0.8rem;
        margin-top: 2px;
    }
    .hero-box {
        background: linear-gradient(135deg, rgba(30, 58, 138, 0.4) 0%, rgba(15, 23, 42, 0.8) 100%);
        border: 1px solid #1E40AF;
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 24px;
    }
    .badge-layer {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: bold;
        margin-right: 6px;
    }
    .badge-layer-1 { background-color: #1E3A8A; color: #93C5FD; border: 1px solid #3B82F6; }
    .badge-layer-2 { background-color: #064E3B; color: #6EE7B7; border: 1px solid #10B981; }
</style>
""", unsafe_allow_html=True)


# --- DATA INITIALIZATION ---
try:
    kpis = get_system_kpis()
    store = get_graph_store()
    laws = get_all_laws_data()
    news = get_all_news_data()
except Exception as e:
    st.error(f"Error loading system telemetry: {e}")
    st.stop()


# --- HERO BANNER ---
st.markdown("""
<div class="hero-box">
    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
        <div>
            <h1 style="margin: 0; color: #FFFFFF; font-size: 2.3rem; font-weight: 800;">
                ⚖️ PowerLaw: Dual-Layer Legal & Economic Platform
            </h1>
            <p style="margin: 8px 0 16px 0; color: #94A3B8; font-size: 1.1rem; max-width: 900px;">
                Executive intelligence engine modeling EU Regulations as executable logic gates across industrial supply chains.
                Couples a <strong>Topological Graph</strong> of statutory mandates and market reactions with a 
                <strong>Behind-The-Graph Tabular Time-Series</strong> engine for Graph Neural Network (GNN) simulation.
            </p>
            <div>
                <span class="badge-layer badge-layer-1">LAYER 1: TOPOLOGICAL CAUSAL GRAPH</span>
                <span class="badge-layer badge-layer-2">LAYER 2: BEHIND-THE-GRAPH TABULAR TIME-SERIES</span>
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
    gamma_val = kpis.get("gamma", 1.8)
    st.metric(
        label="📐 Power-Law (γ)",
        value=f"{gamma_val:.2f}",
        delta="Scale-Free Topology" if 1.5 <= gamma_val <= 3.0 else "Sub-Exponential",
        delta_color="normal"
    )

st.divider()


# --- 2. SYSTEM ARCHITECTURE DIAGRAM ---
st.markdown("### 🏛️ System Architecture: Dual-Layer Graph & Tabular Engine")
st.markdown("""
The PowerLaw architecture operates across two coupled representations:
1. **Topological Graph Layer**: Directed acyclic and feedback graph tracking statutory authority (`LAW`), 
mandated compliance steps (`ACTION`), economic events (`REACTION`), and industrial structures (`SECTOR`, `COMMODITY`).
2. **Behind-the-Graph Tabular Layer**: Continuous time-series records per node capturing historical metrics 
(e.g., carbon price, gas storage %, compliance cost) and categorical states (`ACTIVE`, `PEAK`, `TARGET_MET`).
""")

# Render Architecture Diagram with Graphviz
architecture_dot = """
digraph G {
    rankdir=LR;
    bgcolor="transparent";
    node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, penwidth=1.5];
    edge [fontname="Helvetica", fontsize=9, color="#64748B", fontcolor="#94A3B8"];

    // Ingestion Sources
    subgraph cluster_ingest {
        label = "Upstream Pipelines";
        style = "dashed";
        color = "#475569";
        fontcolor = "#CBD5E1";
        fontsize = 11;
        
        eurlex [label="EUR-Lex Directives\\n& Regulations (PDF)", fillcolor="#1E293B", fontcolor="#E2E8F0", color="#334155"];
        news_rss [label="Economic & Trade\\nNews Feeds (RSS)", fillcolor="#1E293B", fontcolor="#E2E8F0", color="#334155"];
        market_idx [label="Market Benchmarks\\n(TTF, EUA, BDI)", fillcolor="#1E293B", fontcolor="#E2E8F0", color="#334155"];
    }

    // Processing & Extraction
    subgraph cluster_extract {
        label = "Extraction & Vector Pipeline";
        style = "dashed";
        color = "#475569";
        fontcolor = "#CBD5E1";
        fontsize = 11;

        llm_logic [label="Gemini LLM\\nAtomic LogicGate Extractor\\n(IF-THEN Rules)", fillcolor="#1E3A8A", fontcolor="#BFDBFE", color="#3B82F6"];
        news_extractor [label="Receptor Signal &\\nImpact Extractor", fillcolor="#1E3A8A", fontcolor="#BFDBFE", color="#3B82F6"];
    }

    // Layer 1: Topological Graph
    subgraph cluster_layer1 {
        label = "LAYER 1: Topological Graph (Nodes & Edges)";
        style = "filled";
        fillcolor="#0F172A";
        color = "#3B82F6";
        fontcolor = "#93C5FD";
        fontsize = 12;

        law_node [label="LAW Node\\n(e.g., CBAM, ETS)", fillcolor="#2563EB", fontcolor="#FFFFFF", color="#60A5FA"];
        act_node [label="ACTION Node\\n(Surrender, Permit)", fillcolor="#D97706", fontcolor="#FFFFFF", color="#FBBF24"];
        react_node [label="REACTION Node\\n(Price Spike, Supply Cut)", fillcolor="#DC2626", fontcolor="#FFFFFF", color="#F87171"];
        sector_node [label="SECTOR Node\\n(NACE C24, D35)", fillcolor="#059669", fontcolor="#FFFFFF", color="#34D399"];
        comm_node [label="COMMODITY Node\\n(EUA, Gas TTF, Steel)", fillcolor="#7C3AED", fontcolor="#FFFFFF", color="#A78BFA"];

        law_node -> act_node [label="MANDATES", color="#60A5FA", fontcolor="#93C5FD"];
        act_node -> react_node [label="TRIGGERS", color="#FBBF24", fontcolor="#FDE68A"];
        act_node -> sector_node [label="AFFECTS", color="#34D399", fontcolor="#A7F3D0"];
        react_node -> comm_node [label="IMPACTS", color="#F87171", fontcolor="#FECACA"];
        law_node -> sector_node [label="REGULATES", style="dotted", color="#94A3B8"];
    }

    // Layer 2: Behind-The-Graph Tabular Engine
    subgraph cluster_layer2 {
        label = "LAYER 2: Behind-The-Graph Tabular Engine";
        style = "filled";
        fillcolor="#064E3B";
        color = "#10B981";
        fontcolor = "#A7F3D0";
        fontsize = 12;

        history_tbl [label="NodeStateRecord Store\\n[timestamp, node_id, state_val,\\nmetric_name, metric_val, delta]", shape=note, fillcolor="#047857", fontcolor="#FFFFFF", color="#34D399"];
    }

    // Downstream Consumers
    subgraph cluster_downstream {
        label = "Downstream Analytics & GNN";
        style = "dashed";
        color = "#475569";
        fontcolor = "#CBD5E1";
        fontsize = 11;

        powerlaw_eng [label="Scale-Free Power-Law\\nP(k) ~ k^(-γ)\\nCentrality Profiler", fillcolor="#1E293B", fontcolor="#E2E8F0", color="#64748B"];
        gnn_tensors [label="GNN Tensors\\nFeature Matrix X (N x D)\\nEdge Index E (2 x M)", fillcolor="#1E293B", fontcolor="#E2E8F0", color="#64748B"];
    }

    // Cross-cluster connections
    eurlex -> llm_logic;
    news_rss -> news_extractor;
    
    llm_logic -> law_node;
    llm_logic -> act_node;
    news_extractor -> react_node;
    market_idx -> comm_node;

    law_node -> history_tbl [style="dashed", color="#34D399"];
    act_node -> history_tbl [style="dashed", color="#34D399"];
    react_node -> history_tbl [style="dashed", color="#34D399"];
    comm_node -> history_tbl [style="dashed", color="#34D399"];

    cluster_layer1 -> powerlaw_eng;
    cluster_layer1 -> gnn_tensors;
    history_tbl -> gnn_tensors [label="Temporal Aggs"];
}
"""

st.graphviz_chart(architecture_dot, width="stretch")

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
st.caption("PowerLaw Framework v2.0 • Phase 2 Dual-Layer Architecture • Operating on LanceDB & NetworkX")
