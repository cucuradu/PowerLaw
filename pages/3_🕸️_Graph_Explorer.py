"""
PowerLaw Graph Explorer & Topology Suite
Interactive Network Visualization via PyVis & NetworkX with Power-Law Analytics
"""

import json
import streamlit as st
import pandas as pd
import numpy as np
import networkx as nx
import altair as alt
import plotly.express as px
import streamlit.components.v1 as components
from pyvis.network import Network

from src.storage.dashboard_data import get_graph_store
from src.models.dual_layer_model import NodeType, EdgeRelation

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


# --- PYVIS INTERACTIVE VISUALIZER ---
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
        
        # Node size based on degree
        size = 16 + int(24 * (deg / max(max_deg, 1)))

        label = data.get("label", node_id)
        # Shorten label for cleaner canvas display
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
        "stabilization": {"iterations": 150}
      },
      "interaction": {
        "hover": true,
        "navigationButtons": true,
        "tooltipDelay": 150,
        "hideEdgesOnDrag": false
      }
    }
    """)

    # Render HTML in Streamlit (with inlined bindings to avoid 404 in iframe)
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


# --- BOTTOM TABS: POWER-LAW ANALYTICS & NODE INSPECTOR ---
g_tab1, g_tab2, g_tab3 = st.tabs([
    "📐 Power-Law & Centrality Analytics",
    "🔍 Node Inspector",
    "📑 Subgraph Edge Matrix"
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
