"""
ConstructPulse Graph Explorer & Topology Suite
Interactive Network Visualization via Plotly & NetworkX with Power-Law Analytics
"""

import os
import json
import streamlit as st
import pandas as pd
import numpy as np
import networkx as nx
import altair as alt
import plotly.express as px
import plotly.graph_objects as go
import streamlit.components.v1 as components
from pyvis.network import Network
from pathlib import Path

from src.storage.dashboard_data import get_graph_store
from src.models.dual_layer_model import (
    GraphNode,
    GraphEdge,
    NodeStateRecord,
    NodeType,
    EdgeRelation
)
from scripts.export_graph_viz import export_graph_visualization

st.set_page_config(
    page_title="Dual-Layer Graph Explorer",
    page_icon="🕸️",
    layout="wide"
)

# Header
st.title("🕸️ Dual-Layer Graph Explorer & Scale-Free Topology")
st.markdown("""
Interactive causal graph visualizing statutory authority (**Laws**), compliance requirements (**Actions**), 
market shocks (**Reactions**), and industrial receivers (**Sectors & Commodities**).
""")

# Load store
try:
    store = get_graph_store()
    G = store.to_networkx()
    raw_nodes = store.get_nodes()
    raw_edges = store.get_edges()
except Exception as e:
    st.error(f"Failed to load graph data: {e}")
    st.stop()

if len(G) == 0:
    st.warning("The graph store has no nodes loaded.")
    st.stop()


# --- COLOR & STYLE PALETTE ---
NODE_COLORS = {
    "LAW": "#2563EB",          # Royal Blue
    "ACTION": "#F59E0B",       # Amber Orange
    "REACTION": "#EF4444",     # Crimson Red
    "SECTOR": "#10B981",       # Emerald Green
    "COMMODITY": "#8B5CF6",    # Deep Purple
    "ECONOMIC_INDEX": "#FBBF24" # Golden Yellow
}

EDGE_COLORS = {
    "MANDATES": "#3B82F6",
    "TRIGGERS": "#F59E0B",
    "REGULATES": "#64748B",
    "AFFECTS": "#10B981",
    "IMPACTS": "#EF4444",
    "CAUSES": "#EC4899",
    "AMENDS": "#8B5CF6"
}


# --- SIDEBAR CONTROLS & FILTERS ---
st.sidebar.header("🛠️ Graph Filters & Controls")

# 1. Node Type Filter
available_node_types = sorted(list({n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type) for n in raw_nodes}))
selected_node_types = st.sidebar.multiselect(
    "Filter by Node Type",
    options=available_node_types,
    default=available_node_types
)

# 2. Edge Relation Filter
available_edge_types = sorted(list({e.relation_type.value if hasattr(e.relation_type, "value") else str(e.relation_type) for e in raw_edges}))
selected_edge_types = st.sidebar.multiselect(
    "Filter by Edge Relation",
    options=available_edge_types,
    default=available_edge_types
)

# 3. Focus / Ego-Network Mode
st.sidebar.markdown("---")
st.sidebar.subheader("🎯 Focus Mode")
focus_mode = st.sidebar.radio(
    "Exploration Scope",
    ["Full Network", "Ego-Network (k-Hop Neighborhood)"]
)

all_node_ids = sorted(list(G.nodes()))
selected_ego_node = None
k_hops = 1

if focus_mode == "Ego-Network (k-Hop Neighborhood)":
    # Prioritize candidate nodes that match active node type filter
    type_filtered_nids = [
        n.id for n in raw_nodes
        if (n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type)) in selected_node_types
    ]
    candidate_options = sorted(type_filtered_nids) if type_filtered_nids else all_node_ids
    selected_ego_node = st.sidebar.selectbox("Center Node", candidate_options)
    k_hops = st.sidebar.slider("Neighborhood Hops", min_value=1, max_value=3, value=1)

# 4. Canvas Height Slider
canvas_height = st.sidebar.slider("Canvas Height (px)", min_value=500, max_value=900, value=650, step=50)


# --- SUBGRAPH EXTRACTION ---
# Filter nodes by type
allowed_nodes = {
    n.id for n in raw_nodes
    if (n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type)) in selected_node_types
}

# Build filtered NetworkX graph
filtered_G = nx.DiGraph()

for n in raw_nodes:
    if n.id in allowed_nodes:
        ntype = n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type)
        filtered_G.add_node(
            n.id,
            label=n.label,
            node_type=ntype,
            description=n.description,
            jurisdiction=n.jurisdiction,
            activation_date=n.activation_date or ""
        )

for e in raw_edges:
    erel = e.relation_type.value if hasattr(e.relation_type, "value") else str(e.relation_type)
    if erel in selected_edge_types:
        if e.source_id in filtered_G and e.target_id in filtered_G:
            filtered_G.add_edge(
                e.source_id,
                e.target_id,
                relation_type=erel,
                weight=float(e.weight),
                timestamp=e.timestamp or ""
            )

# Apply Ego-Network extraction if requested
if focus_mode == "Ego-Network (k-Hop Neighborhood)":
    if selected_ego_node and selected_ego_node in filtered_G:
        # Undirected view for ego neighborhood to capture both in and out connections
        undirected_G = filtered_G.to_undirected()
        ego_nodes = set(nx.single_source_shortest_path_length(undirected_G, selected_ego_node, cutoff=k_hops).keys())
        filtered_G = filtered_G.subgraph(ego_nodes).copy()
    else:
        st.sidebar.warning(f"⚠️ Center node '{selected_ego_node}' is not present in the filtered graph.")


# --- TOP METRICS BAR ---
m1, m2, m3, m4 = st.columns(4)

with m1:
    st.metric("Visible Graph Nodes", len(filtered_G.nodes()), delta=f"{len(G.nodes())} total")

with m2:
    st.metric("Visible Graph Edges", len(filtered_G.edges()), delta=f"{len(G.edges())} total")

with m3:
    density = nx.density(filtered_G) if len(filtered_G) > 1 else 0.0
    st.metric("Graph Density", f"{density:.4f}")

with m4:
    degrees = [d for _, d in filtered_G.degree()] if len(filtered_G) > 0 else [0]
    avg_deg = sum(degrees) / max(len(degrees), 1)
    st.metric("Average Node Degree", f"{avg_deg:.2f}")

st.divider()


# --- VISUAL LEGEND ---
st.markdown("""
<div style="background-color: #0E1117; border: 1px solid #262730; border-radius: 8px; padding: 12px; margin-bottom: 16px; display: flex; flex-wrap: wrap; gap: 14px; align-items: center;">
    <span style="font-weight: bold; color: #94A3B8; font-size: 0.85rem;">NODE LEGEND:</span>
    <span style="color: #60A5FA; font-weight: 600; font-size: 0.85rem;"><span style="color: #2563EB;">●</span> LAW</span>
    <span style="color: #FBBF24; font-weight: 600; font-size: 0.85rem;"><span style="color: #F59E0B;">●</span> ACTION</span>
    <span style="color: #F87171; font-weight: 600; font-size: 0.85rem;"><span style="color: #EF4444;">●</span> REACTION</span>
    <span style="color: #34D399; font-weight: 600; font-size: 0.85rem;"><span style="color: #10B981;">●</span> SECTOR (NACE)</span>
    <span style="color: #A78BFA; font-weight: 600; font-size: 0.85rem;"><span style="color: #8B5CF6;">●</span> COMMODITY</span>
    <span style="color: #FDE047; font-weight: 600; font-size: 0.85rem;"><span style="color: #FBBF24;">●</span> ECONOMIC INDEX</span>
    <span style="color: #64748B; margin-left: auto; font-size: 0.8rem;">💡 Drag nodes to rearrange • Scroll to zoom • Hover for details</span>
</div>
""", unsafe_allow_html=True)


# --- ENGINE SELECTION ---
col_eng, col_dl1, col_dl2 = st.columns([2, 1, 1])
with col_eng:
    engine_mode = st.radio(
        "Interactive Engine",
        ["📊 Native Plotly Network Graph (Interactive)", "⚡ High-Performance Updatable Canvas (HTML5)", "🕸️ 3D / PyVis Network View"],
        horizontal=True
    )

# Downloads and the canvas use current configured storage, never bundled snapshots.
graph_payload, updatable_html_content = export_graph_visualization(storage_dir=store.storage_dir, persist=False)
json_data_content = json.dumps(graph_payload)

with col_dl1:
    st.download_button(
        label="📥 Standalone HTML",
        data=updatable_html_content,
        file_name="constructpulse_interactive_graph.html",
        mime="text/html",
        use_container_width=True
    )

with col_dl2:
    st.download_button(
        label="📥 Graph JSON",
        data=json_data_content,
        file_name="constructpulse_graph_data.json",
        mime="application/json",
        use_container_width=True
    )


# --- RENDER SELECTED VISUALIZER ---
if engine_mode == "📊 Native Plotly Network Graph (Interactive)":
    if len(filtered_G.nodes()) == 0:
        st.info("No nodes match the selected filters. Please adjust the filters in the sidebar.")
    else:
        # Compute layout coordinates
        pos = nx.spring_layout(filtered_G, seed=42, k=0.38, iterations=50)

        # Build Edge Traces
        edge_x = []
        edge_y = []
        for u, v in filtered_G.edges():
            x0, y0 = pos[u]
            x1, y1 = pos[v]
            edge_x.extend([x0, x1, None])
            edge_y.extend([y0, y1, None])

        edge_trace = go.Scatter(
            x=edge_x,
            y=edge_y,
            line=dict(width=1.2, color="#475569"),
            hoverinfo="none",
            mode="lines"
        )

        # Build Node Traces
        node_x = []
        node_y = []
        node_text = []
        node_labels = []
        node_color = []
        node_size = []

        total_degrees = dict(filtered_G.degree())
        max_deg = max(total_degrees.values()) if total_degrees else 1

        for n in filtered_G.nodes():
            x, y = pos[n]
            node_x.append(x)
            node_y.append(y)

            data = filtered_G.nodes[n]
            ntype = data.get("node_type", "UNKNOWN")
            lbl = data.get("label", n)
            deg = total_degrees.get(n, 1)

            node_labels.append(lbl[:16] + "..." if len(lbl) > 18 else lbl)
            node_color.append(NODE_COLORS.get(ntype, "#94A3B8"))
            node_size.append(14 + int(24 * (deg / max(max_deg, 1))))

            desc = data.get("description", "")
            desc_sub = (desc[:140] + "...") if len(desc) > 140 else desc
            tooltip = f"<b>{lbl}</b><br>ID: <code>{n}</code><br>Type: <b>{ntype}</b><br>Degree: {deg}<br>Jurisdiction: {data.get('jurisdiction', 'EU')}<br><i>{desc_sub}</i>"
            node_text.append(tooltip)

        node_trace = go.Scatter(
            x=node_x,
            y=node_y,
            mode="markers+text",
            hoverinfo="text",
            hovertext=node_text,
            text=node_labels,
            textposition="bottom center",
            textfont=dict(size=10, color="#CBD5E1"),
            marker=dict(
                color=node_color,
                size=node_size,
                line=dict(width=2, color="#0F172A"),
                opacity=0.95
            )
        )

        fig_net = go.Figure(
            data=[edge_trace, node_trace],
            layout=go.Layout(
                showlegend=False,
                hovermode="closest",
                margin=dict(b=10, l=10, r=10, t=10),
                xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(15,23,42,0.65)",
                height=canvas_height
            )
        )
        st.plotly_chart(fig_net, use_container_width=True)

elif engine_mode == "⚡ High-Performance Updatable Canvas (HTML5)":
    # Embed the high-performance self-contained updatable canvas
    components.html(updatable_html_content, height=canvas_height + 40, scrolling=False)
else:
    # PyVis Network View
    if len(filtered_G.nodes()) == 0:
        st.info("No nodes match the selected filters. Please adjust the filters in the sidebar.")
    else:
        net = Network(
            height=f"{canvas_height}px",
            width="100%",
            bgcolor="#0B0F19",
            font_color="#F3F4F6",
            directed=True
        )

        # Degrees for scaling
        in_degrees = dict(filtered_G.in_degree())
        out_degrees = dict(filtered_G.out_degree())
        total_degrees = dict(filtered_G.degree())
        max_deg = max(total_degrees.values()) if total_degrees else 1

        # Add Nodes
        for node_id, data in filtered_G.nodes(data=True):
            ntype = data.get("node_type", "UNKNOWN")
            color = NODE_COLORS.get(ntype, "#94A3B8")
            deg = total_degrees.get(node_id, 1)
            size = 16 + int(24 * (deg / max(max_deg, 1)))

            label = data.get("label", node_id)
            display_label = label if len(label) <= 24 else f"{label[:21]}..."

            tooltip = f"""
            <div style='font-family: sans-serif; font-size: 12px; color: #111827; background: #FFFFFF; padding: 8px; border-radius: 4px;'>
                <strong>{label}</strong><br/>
                <b>ID:</b> {node_id}<br/>
                <b>Type:</b> {ntype}<br/>
                <b>In-Degree:</b> {in_degrees.get(node_id, 0)} | <b>Out-Degree:</b> {out_degrees.get(node_id, 0)}<br/>
                <b>Jurisdiction:</b> {data.get('jurisdiction', 'EU')}<br/>
                <b>Description:</b> {data.get('description', '')[:160]}...
            </div>
            """

            net.add_node(
                node_id,
                label=display_label,
                title=tooltip,
                color=color,
                size=size,
                borderWidth=2,
                borderWidthSelected=4,
                shape="dot"
            )

        # Add Edges
        for u, v, data in filtered_G.edges(data=True):
            rel = data.get("relation_type", "RELATED")
            edge_color = EDGE_COLORS.get(rel, "#94A3B8")
            weight = data.get("weight", 1.0)

            edge_tooltip = f"<b>{u}</b> ➔ <b>{v}</b><br/>Relation: <b>{rel}</b><br/>Weight: {weight}"
            if data.get("timestamp"):
                edge_tooltip += f"<br/>Active Since: {data['timestamp']}"

            net.add_edge(
                u,
                v,
                title=edge_tooltip,
                label=rel,
                color=edge_color,
                arrows="to",
                width=1.5 if abs(weight) <= 1.0 else 2.5,
                font={"size": 9, "color": "#94A3B8", "strokeWidth": 0}
            )

        # PyVis Physics Options
        net.set_options("""
        {
          "physics": {
            "forceAtlas2Based": {
              "gravitationalConstant": -50,
              "centralGravity": 0.01,
              "springLength": 100,
              "springConstant": 0.08,
              "damping": 0.4
            },
            "maxVelocity": 50,
            "solver": "forceAtlas2Based",
            "timestep": 0.35,
            "stabilization": {"iterations": 50}
          },
          "interaction": {
            "hover": true,
            "navigationButtons": true,
            "tooltipDelay": 150,
            "hideEdgesOnDrag": false
          }
        }
        """)

        html_content = net.generate_html()
        try:
            import pyvis
            pyvis_dir = os.path.dirname(pyvis.__file__)
            utils_path = os.path.join(pyvis_dir, "lib", "bindings", "utils.js")
            if os.path.exists(utils_path):
                with open(utils_path, "r", encoding="utf-8") as f:
                    utils_js = f.read()
                html_content = html_content.replace(
                    '<script src="lib/bindings/utils.js"></script>',
                    f'<script type="text/javascript">\n{utils_js}\n</script>'
                )
        except Exception:
            pass
        components.html(html_content, height=canvas_height + 10, scrolling=False)


st.divider()


# --- BOTTOM TABS: POWER-LAW ANALYTICS, NODE INSPECTOR, LIVE AUTHORING ---
g_tab1, g_tab2, g_tab3, g_tab4 = st.tabs([
    "📐 Power-Law & Centrality Analytics",
    "🔍 Node Inspector",
    "📑 Subgraph Edge Matrix",
    "✏️ Live Database Authoring Studio"
])



# ============================================================================
# TAB 1: POWER-LAW ANALYTICS
# ============================================================================
with g_tab1:
    st.subheader("📐 Scale-Free Power-Law Degree Properties")
    st.markdown(r"""
    Scale-free networks exhibit heavy-tailed degree distributions:
    $$P(k) \sim k^{-\gamma}$$
    Networks with $2 < \gamma < 3$ are resilient to random node failure but acutely vulnerable to targeted disruption of hub nodes.
    """)

    p_col1, p_col2 = st.columns(2)

    with p_col1:
        st.markdown("#### 📊 Degree Distribution (Log-Log Scale)")
        degrees = [d for _, d in G.degree() if d > 0]
        if degrees:
            deg_series = pd.Series(degrees).value_counts().sort_index().reset_index()
            deg_series.columns = ["Degree_k", "Count_Pk"]
            deg_series["Log_k"] = np.log10(deg_series["Degree_k"])
            deg_series["Log_Pk"] = np.log10(deg_series["Count_Pk"])

            chart_log = alt.Chart(deg_series).mark_circle(size=100, color="#3B82F6").encode(
                x=alt.X("Degree_k:Q", scale=alt.Scale(type="log"), title="Node Degree (k) [Log Scale]"),
                y=alt.Y("Count_Pk:Q", scale=alt.Scale(type="log"), title="Frequency P(k) [Log Scale]"),
                tooltip=["Degree_k", "Count_Pk"]
            ).properties(height=300)

            # Add linear regression fit line
            fit_line = chart_log.transform_regression("Degree_k", "Count_Pk", method="pow").mark_line(color="#EF4444")

            st.altair_chart(chart_log + fit_line, width="stretch")
        else:
            st.info("Insufficient degree variance for power law plotting.")

    with p_col2:
        st.markdown("#### 🏆 Topological Centrality Leaders")
        
        # Centralities
        in_deg_sorted = sorted(G.in_degree(), key=lambda x: x[1], reverse=True)[:5]
        out_deg_sorted = sorted(G.out_degree(), key=lambda x: x[1], reverse=True)[:5]
        betweenness = nx.betweenness_centrality(G)
        between_sorted = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)[:5]

        cent_rows = []
        for i in range(5):
            in_item = in_deg_sorted[i] if i < len(in_deg_sorted) else ("", 0)
            out_item = out_deg_sorted[i] if i < len(out_deg_sorted) else ("", 0)
            bet_item = between_sorted[i] if i < len(between_sorted) else ("", 0.0)

            cent_rows.append({
                "Rank": f"#{i+1}",
                "Top In-Degree (Impacted)": f"{in_item[0]} ({in_item[1]})",
                "Top Out-Degree (Leverage)": f"{out_item[0]} ({out_item[1]})",
                "Top Betweenness (Bottleneck)": f"{bet_item[0]} ({bet_item[1]:.3f})"
            })
        st.dataframe(pd.DataFrame(cent_rows), width="stretch", hide_index=True)


# ============================================================================
# TAB 2: NODE INSPECTOR
# ============================================================================
with g_tab2:
    st.subheader("🔍 Detailed Node Semantics & Connections")

    inspector_node_id = st.selectbox("Select Node to Inspect:", all_node_ids, index=0)
    target_node = store.get_node(inspector_node_id)

    if target_node:
        n_col1, n_col2 = st.columns([1, 1])

        with n_col1:
            st.markdown(f"### `{target_node.id}`")
            st.markdown(f"**Label:** {target_node.label}")
            ntype_str = target_node.node_type.value if hasattr(target_node.node_type, "value") else str(target_node.node_type)
            st.markdown(f"**Node Type:** `{ntype_str}` | **Jurisdiction:** `{target_node.jurisdiction}`")
            st.markdown(f"**Activation Date:** `{target_node.activation_date or 'N/A'}`")
            st.markdown(f"**Description:** {target_node.description}")

            if target_node.metadata:
                st.markdown("**Metadata:**")
                st.json(target_node.metadata)

        with n_col2:
            st.markdown("#### 🔗 Connected Edges")
            inbound = [e for e in raw_edges if e.target_id == target_node.id]
            outbound = [e for e in raw_edges if e.source_id == target_node.id]

            st.markdown(f"**Incoming Relations ({len(inbound)}):**")
            for e in inbound:
                src_node = store.get_node(e.source_id)
                src_label = f" ({src_node.label})" if src_node and src_node.label != e.source_id else ""
                st.markdown(f"- ⬅️ `{e.source_id}`{src_label} — **[{e.relation_type.value if hasattr(e.relation_type, 'value') else e.relation_type}]**")

            st.markdown(f"**Outgoing Relations ({len(outbound)}):**")
            for e in outbound:
                tgt_node = store.get_node(e.target_id)
                tgt_label = f" ({tgt_node.label})" if tgt_node and tgt_node.label != e.target_id else ""
                st.markdown(f"- ➡️ `{e.target_id}`{tgt_label} — **[{e.relation_type.value if hasattr(e.relation_type, 'value') else e.relation_type}]** (Weight: {e.weight})")


# ============================================================================
# TAB 3: SUBGRAPH EDGE MATRIX
# ============================================================================
with g_tab3:
    st.subheader("📑 Active Subgraph Edges")
    edge_records = []
    for u, v, data in filtered_G.edges(data=True):
        edge_records.append({
            "Source Node": u,
            "Relation": data.get("relation_type", ""),
            "Target Node": v,
            "Weight": data.get("weight", 1.0),
            "Timestamp": data.get("timestamp", "")
        })
    df_edges = pd.DataFrame(edge_records)
    st.dataframe(df_edges, width="stretch", height=400)


# ============================================================================
# TAB 4: LIVE DATABASE AUTHORING STUDIO
# ============================================================================
with g_tab4:
    st.subheader("✏️ Live Graph Authoring & LanceDB Injection Studio")
    st.markdown("""
    Inject new statutory regulations, industry actions, market shocks, and commodity metrics directly 
    into the **LanceDB Dual-Layer Store**. Changes immediately re-compile and sync with both the **Topological Graph** 
    and the **High-Performance Canvas**.
    """)

    auth_col1, auth_col2, auth_col3 = st.columns(3)

    # 1. Inject Node
    with auth_col1:
        st.markdown("#### ➕ 1. Inject Graph Node")
        with st.form("form_inject_node", clear_on_submit=True):
            new_label = st.text_input("Entity Label", placeholder="e.g. EU Low-Carbon Cement Mandate 2027")
            new_type = st.selectbox(
                "Node Type",
                ["LAW", "ACTION", "REACTION", "COMMODITY", "SECTOR", "ECONOMIC_INDEX"],
                index=2
            )
            new_id = st.text_input("Custom ID (Optional)", placeholder="Auto-generated if empty")
            new_desc = st.text_area("Description / Rationale", placeholder="Explain compliance obligation or market effect...")
            new_juris = st.text_input("Jurisdiction", value="EU")
            new_date = st.date_input("Activation Date")
            submit_node = st.form_submit_button("💾 Save Node to Database", use_container_width=True)

            if submit_node and new_label:
                node_id = new_id.strip() if new_id.strip() else f"{new_type}_{new_label.upper().replace(' ', '_')[:20]}_{int(pd.Timestamp.now().timestamp()) % 10000}"
                node_obj = GraphNode(
                    id=node_id,
                    label=new_label.strip(),
                    node_type=NodeType(new_type),
                    description=new_desc.strip(),
                    jurisdiction=new_juris.strip(),
                    activation_date=str(new_date)
                )
                store.add_node(node_obj)
                store.save()
                export_graph_visualization()
                st.success(f"✅ Injected Node `{node_id}` into LanceDB!")
                st.rerun()

    # 2. Connect Edge
    with auth_col2:
        st.markdown("#### 🔗 2. Connect Causal Edge")
        with st.form("form_inject_edge", clear_on_submit=True):
            all_ids = sorted([n.id for n in raw_nodes])
            src_choice = st.selectbox("Source (Driver / Cause)", all_ids, index=0 if all_ids else 0)
            tgt_choice = st.selectbox("Target (Impacted / Receiver)", all_ids, index=min(1, len(all_ids)-1) if all_ids else 0)
            rel_choice = st.selectbox(
                "Relation Type",
                ["MANDATES", "TRIGGERS", "AFFECTS", "IMPACTS", "REGULATES", "CAUSES"],
                index=1
            )
            edge_w = st.slider("Causal Weight (Impact Scale)", min_value=0.1, max_value=5.0, value=1.5, step=0.1)
            submit_edge = st.form_submit_button("🔗 Connect Causal Relation", use_container_width=True)

            if submit_edge:
                if src_choice == tgt_choice:
                    st.error("Cannot connect a node to itself.")
                else:
                    edge_obj = GraphEdge(
                        source_id=src_choice,
                        target_id=tgt_choice,
                        relation_type=EdgeRelation(rel_choice),
                        weight=float(edge_w),
                        timestamp=pd.Timestamp.now().strftime("%Y-%m-%d")
                    )
                    store.add_edge(edge_obj)
                    store.save()
                    export_graph_visualization()
                    st.success(f"✅ Connected `{src_choice}` ➔ `{tgt_choice}` ({rel_choice})!")
                    st.rerun()

    # 3. Log Tabular State Metric
    with auth_col3:
        st.markdown("#### ⏱️ 3. Log Tabular State Metric")
        with st.form("form_inject_state", clear_on_submit=True):
            all_ids = sorted([n.id for n in raw_nodes])
            state_target = st.selectbox("Target Node", all_ids, index=0 if all_ids else 0)
            state_val = st.selectbox("State Category", ["ACTIVE", "PEAK", "TARGET_MET", "REVISED", "STRESS"], index=0)
            metric_n = st.text_input("Metric Name", placeholder="e.g. spot_price, compliance_cost")
            metric_v = st.number_input("Metric Value", value=100.0, step=1.0)
            metric_u = st.text_input("Unit", value="EUR/t")
            state_notes = st.text_area("State Summary / Event Notes", placeholder="e.g. Market quotation update after CBAM review")
            submit_state = st.form_submit_button("⏱️ Record in Tabular Ledger", use_container_width=True)

            if submit_state:
                target_node_obj = store.get_node(state_target)
                rec = NodeStateRecord(
                    node_id=state_target,
                    node_type=target_node_obj.node_type if target_node_obj else NodeType.ACTION,
                    state_value=state_val,
                    metric_name=metric_n.strip() or "spot_metric",
                    metric_value=float(metric_v),
                    unit=metric_u.strip(),
                    notes=state_notes.strip(),
                    timestamp=pd.Timestamp.now().strftime("%Y-%m-%d")
                )
                store.add_state_record(rec)
                store.save()
                export_graph_visualization()
                st.success(f"✅ Recorded state entry for `{state_target}` in LanceDB!")
                st.rerun()
