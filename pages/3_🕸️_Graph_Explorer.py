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

from src.storage.dashboard_data import event_labels, get_forecast_drivers, get_graph_store, get_sector_network, series_names
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
st.title("🕸️ Graph Explorer")
view = st.radio("View", ["Sector network", "Forecast drivers", "Stored records"], horizontal=True,
                help="Sector network: who supplies and buys construction, materials, prices and laws. "
                     "Forecast drivers: what feeds the price forecast. Stored records: everything in the database.")

GROUP_STYLE = {  # colour, size
    "construction": ("#2563EB", 46), "own sector": ("#2563EB", 20), "supplier": ("#10B981", 22), "cost": ("#14B8A6", 20),
    "buyer": ("#F59E0B", 20), "final demand": ("#FBBF24", 22), "material": ("#8B5CF6", 10), "law": ("#EF4444", 16),
}


@st.cache_data(show_spinner=False)
def _sector_network(storage_dir: str, generation: int, year, top: int):
    return get_sector_network(get_graph_store(storage_dir).canonical_store, year=year, top=top)


def _pct(value) -> str:
    return "" if value is None else f"{100 * value:.1f}%"


def render_sector_network(height: int = 700) -> None:
    store = get_graph_store()
    first = _sector_network(str(store.storage_dir), store._generation, None, 12)
    if not first["years"]:
        st.info("No input-output data yet. Run: python -m scripts.ops.ingest --source EUROSTAT_NAIO --limit 10")
        return
    left, right = st.columns(2)
    year = left.select_slider("Input-output year", options=first["years"], value=first["year"],
                              help="Eurostat publishes these tables about three years after the year they describe.")
    top = right.slider("Suppliers and buyers shown", 5, 30, 12)
    data = _sector_network(str(store.storage_dir), store._generation, year, top)
    st.markdown(
        f"German construction in **{data['year']}**: **green** product groups it buys (line width = share of its output "
        "value), **teal** labour, profit, depreciation and taxes, **orange** who buys its work, **purple** building "
        "materials with their latest price change, **red** laws that regulate any of them (EU laws through rules "
        "extracted from the text, German laws through quoted sections). Every link is backed by a stored source.")
    net = Network(height=f"{height}px", width="100%", bgcolor="#0B0F19", font_color="#F3F4F6", directed=True)
    for node in data["nodes"]:
        colour, size = GROUP_STYLE.get(node["group"], ("#94A3B8", 12))
        if node["group"] == "law" and node.get("jurisdiction") == "DE":
            colour = "#F97316"
        lines = [node["label"], node["group"]]
        lines += [f"{p['series']}: {p['yoy_pct']:+.1f}% vs a year earlier ({p['period']})" for p in node.get("prices", [])]
        net.add_node(node["id"], label=node["label"][:38], title="\n".join(lines), color=colour, size=size, shape="dot")
    for edge in data["edges"]:
        share = edge.get("share") or 0
        if edge["kind"] in ("supplies", "cost", "buys"):
            title = (f"{edge['value_mio_eur']:,.0f} million EUR ({_pct(share)} of "
                     f"{'construction output' if edge['kind'] != 'buys' else 'all uses of construction'})")
            if edge.get("import_share") is not None:
                title += f"; {_pct(edge['import_share'])} imported"
            net.add_edge(edge["source"], edge["target"], title=title, width=1 + 40 * share,
                         color=GROUP_STYLE["supplier" if edge["kind"] != "buys" else "buyer"][0])
        elif edge["kind"] == "regulates":
            examples = "\n".join(f"- {e[:160]}" for e in edge.get("examples", []))
            net.add_edge(edge["source"], edge["target"], title=f"{edge['rules']} provisions\n{examples}", color="#EF4444",
                         width=1, dashes=True)
        else:
            net.add_edge(edge["source"], edge["target"], title=edge["kind"], color="#64748B", width=1)
    net.set_options('{"physics": {"solver": "forceAtlas2Based", "forceAtlas2Based": {"gravitationalConstant": -90, '
                    '"springLength": 120}, "stabilization": {"iterations": 120}}, '
                    '"edges": {"arrows": {"to": {"enabled": true, "scaleFactor": 0.5}}, "smooth": false}, '
                    '"interaction": {"hover": true, "navigationButtons": true}}')
    components.html(net.generate_html(), height=height + 10, scrolling=False)

    nodes = {n["id"]: n for n in data["nodes"]}
    supply = [e for e in data["edges"] if e["kind"] in ("supplies", "cost")]
    st.markdown("**What construction spends its money on**")
    st.dataframe(pd.DataFrame([{
        "Input": nodes.get(e["source"], {"label": "Construction (subcontracting)"})["label"] if e["source"] != "sec_F" else "Construction (subcontracting)",
        "Million EUR": round(e["value_mio_eur"]), "Share of output": _pct(e.get("share")),
        "Imported": _pct(e.get("import_share")),
        "Latest price change": "; ".join(f"{p['yoy_pct']:+.1f}%" for p in nodes.get(e["source"], {}).get("prices", [])[:1])}
        for e in sorted(supply, key=lambda e: -e["value_mio_eur"])]), width="stretch", hide_index=True)
    materials = [n for n in data["nodes"] if n["group"] == "material" and n.get("prices")]
    if materials:
        st.markdown("**Building materials: price change on a year earlier**")
        st.dataframe(pd.DataFrame([{"Material": n["label"], "Change": round(n["prices"][0]["yoy_pct"], 1),
                                    "Month": n["prices"][0]["period"]}
                                   for n in sorted(materials, key=lambda n: -n["prices"][0]["yoy_pct"])]),
                     width="stretch", hide_index=True,
                     column_config={"Change": st.column_config.NumberColumn("Change (%)", format="%+.1f")})
    laws = [e for e in data["edges"] if e["kind"] == "regulates"]
    if laws:
        st.markdown("**Laws and what they regulate**")
        st.dataframe(pd.DataFrame([{"Law": nodes[e["source"]]["label"], "Regulates": nodes[e["target"]]["label"],
                                    "Provisions": e["rules"], "Example": (e.get("examples") or [""])[0]}
                                   for e in sorted(laws, key=lambda e: -e["rules"])]), width="stretch", hide_index=True)
    st.caption("Sources: Eurostat input-output table naio_10_cp1700, Destatis producer prices 61241, EUR-Lex, "
               "gesetze-im-internet.de. Links show how the sector is connected; they are not a forecast of cause and effect.")


def render_forecast_drivers(height: int = 620) -> None:
    """The forecast target, its inputs (used or only tested), VAT and dated events. Links are not proof of cause."""
    drivers = get_forecast_drivers()
    st.markdown("What feeds the construction price forecast. **Green** inputs are used; **grey** ones were tested and "
                "left out because they made the forecast worse. **Orange** events happened around big price moves; "
                "that shows timing, not proof of cause.")
    net = Network(height=f"{height}px", width="100%", bgcolor="#0B0F19", font_color="#F3F4F6", directed=True)
    net.add_node("target", label=drivers["target"], color="#2563EB", size=42, shape="dot",
                 title="Destatis price index for building new homes (2021 = 100), forecast one quarter ahead")
    net.add_node("vat", label="VAT rate (law)", color="#10B981", size=24, shape="dot",
                 title="Known in advance once the law is published; the largest single driver of past jumps")
    net.add_edge("vat", "target", label="used", color="#10B981", width=2.5)
    for item in drivers["inputs"]:
        used = item["status"] == "used"
        colour = "#10B981" if used else "#64748B"
        net.add_node(item["key"], label=item["name"] if not item["components"] else "Material prices",
                     color=colour, size=24 if used else 16, shape="dot", title=item["why"])
        net.add_edge(item["key"], "target", label=item["status"], color=colour, width=2.5 if used else 1, dashes=not used)
        for i, component in enumerate(item["components"]):
            node = f"{item['key']}_{i}"
            net.add_node(node, label=component.replace("Material prices: ", ""), color=colour, size=10, shape="dot")
            net.add_edge(node, item["key"], color=colour, width=1)
    for i, event in enumerate(drivers["events"]):
        node = f"event_{i}"
        net.add_node(node, label=f"{event['date'][:7]} {event['kind']}", color="#F59E0B", size=14, shape="dot",
                     title=f"{event['date']}: {event['title']}")
        net.add_edge(node, "target", label="same time", color="#B45309", width=1, dashes=True)
    net.set_options('{"physics": {"solver": "forceAtlas2Based", "forceAtlas2Based": {"gravitationalConstant": -80, '
                    '"springLength": 140}, "stabilization": {"iterations": 80}}, '
                    '"edges": {"font": {"size": 9, "color": "#94A3B8", "strokeWidth": 0}, "arrows": {"to": {"enabled": true}}}, '
                    '"interaction": {"hover": true, "navigationButtons": true}}')
    components.html(net.generate_html(), height=height + 10, scrolling=False)
    if drivers["stored_not_tested"]:
        st.caption("Also stored but not tested as forecast inputs: " + ", ".join(drivers["stored_not_tested"]) + ".")
    st.markdown("**Events**")
    st.dataframe(pd.DataFrame([{"Date": e["date"], "Kind": e["kind"], "What happened": e["title"], "Source": e["source"]}
                               for e in drivers["events"]]),
                 width="stretch", hide_index=True,
                 column_config={"Source": st.column_config.LinkColumn("Source", display_text="open")})


if view == "Sector network":
    render_sector_network()
    st.stop()
if view == "Forecast drivers":
    render_forecast_drivers()
    st.stop()

st.markdown("Everything stored in the database: laws, extracted news events and measured data series. "
            "Retired series are hidden.")

# Load store
try:
    store = get_graph_store()
    G = store.to_networkx()
    names = series_names()
    readable = {**{k: v["name"] for k, v in names.items()}, **event_labels(store.canonical_store)}
    hidden = {k for k, v in names.items() if v["status"] != "active"}
    G.remove_nodes_from([n for n in hidden if n in G])
    for node_id, label in readable.items():
        if node_id in G:
            G.nodes[node_id]["label"] = label
    raw_nodes = [n.model_copy(update={"label": readable.get(n.id, n.label)}) for n in store.get_nodes() if n.id not in hidden]
    raw_edges = [e for e in store.get_edges() if e.source_id not in hidden and e.target_id not in hidden]
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
