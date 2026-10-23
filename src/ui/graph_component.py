"""Interactive network view for Streamlit (vis-network, vendored; no CDN).

Python lays the nodes out (fixed x/y), so a rerun after a click redraws the same picture. Clicking a node or an edge
sends its id back: `result.node` / `result.edge` (trigger values, set for one rerun). Hovering shows a tooltip.
"""
import hashlib
import json
from pathlib import Path

import streamlit as st

_VIS = (Path(__file__).parent / "vendor" / "vis-network.min.js").read_text(encoding="utf-8")

_JS = _VIS + r"""
export default function (component) {
  const { parentElement, data, setTriggerValue } = component;
  const host = parentElement.querySelector(".graph") || parentElement.appendChild(document.createElement("div"));
  host.className = "graph";
  host.style.height = (data.height || 640) + "px";
  if (host.__hash === data.hash && host.__network) { return; }   // same picture: keep zoom and position
  if (host.__network) { host.__network.destroy(); }
  const vis = globalThis.vis;
  const tip = (text) => { const el = document.createElement("div"); el.className = "tip"; el.innerText = text; return el; };
  const nodes = new vis.DataSet(data.nodes.map(n => ({...n, title: n.tip ? tip(n.tip) : undefined})));
  const edges = new vis.DataSet(data.edges.map(e => ({...e, title: e.tip ? tip(e.tip) : undefined})));
  const network = new vis.Network(host, { nodes, edges }, {
    physics: false,
    interaction: { hover: true, tooltipDelay: 80, multiselect: false, zoomSpeed: 0.6, hideEdgesOnDrag: false },
    nodes: { shape: "dot", borderWidth: 1.5, font: { face: "Inter, Segoe UI, sans-serif", size: 13, color: "#1F2937",
             strokeWidth: 3, strokeColor: "#FFFFFF" } },
    edges: { arrows: { to: { enabled: true, scaleFactor: 0.45 } }, smooth: { type: "continuous", roundness: 0.25 },
             selectionWidth: 2, hoverWidth: 1.5, font: { size: 11, color: "#374151", strokeWidth: 3, strokeColor: "#FFFFFF", align: "middle" } },
  });
  network.on("click", (p) => {
    if (p.nodes.length) { setTriggerValue("node", p.nodes[0]); }
    else if (p.edges.length) { setTriggerValue("edge", p.edges[0]); }
  });
  network.once("afterDrawing", () => network.fit({ animation: false }));
  host.__network = network;
  host.__hash = data.hash;
  return () => { network.destroy(); host.__network = null; host.__hash = null; };
}
"""

_CSS = """
.graph { width: 100%; border: 1px solid #E5E7EB; border-radius: 12px; background: #FFFFFF; }
.tip { max-width: 360px; white-space: pre-wrap; font: 12px/1.45 Inter, "Segoe UI", sans-serif; color: #111827;
       background: #FFFFFF; border: 1px solid #D1D5DB; border-radius: 8px; padding: 8px 10px;
       box-shadow: 0 6px 18px rgba(0,0,0,0.12); }
div.vis-tooltip { position: absolute; z-index: 10; pointer-events: none; }
"""

_component = st.components.v2.component("store_graph", js=_JS, css=_CSS, html='<div class="graph"></div>')


def network(nodes: list, edges: list, height: int = 640, key: str = "graph"):
    """Draw `nodes` ({id, label, x, y, color, size, tip, ...}) and `edges` ({id, from, to, color, width, tip, ...}).
    Returns the component result; `.node` / `.edge` hold the id clicked in this run (else None)."""
    payload = {"nodes": nodes, "edges": edges, "height": height}
    payload["hash"] = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return _component(data=payload, key=key, on_node_change=lambda: None, on_edge_change=lambda: None)
