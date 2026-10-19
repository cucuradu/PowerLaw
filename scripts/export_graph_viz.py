"""
Exporter & Compiler for PowerLaw Interactive Graph Visualization.
Extracts nodes, edges, network metrics, and temporal history from LanceDB
and generates a self-contained, high-performance HTML5 Canvas / SVG interactive visualizer.
Supports dynamic in-memory updates (add node, add edge, edit node, inject temporal states, export JSON).
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

# Ensure project root is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import networkx as nx
from src.storage.dashboard_data import get_graph_store

def export_graph_visualization(output_paths=None, storage_dir=None, *, persist=True):
    store = get_graph_store(storage_dir=storage_dir)
    nodes = store.get_nodes()
    edges = store.get_edges()
    history = store.get_history()

    # Build NetworkX graph for topology metrics
    G = nx.DiGraph()
    for n in nodes:
        ntype = n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type)
        G.add_node(n.id, label=n.label, node_type=ntype)
    for e in edges:
        erel = e.relation_type.value if hasattr(e.relation_type, "value") else str(e.relation_type)
        if e.source_id in G and e.target_id in G:
            G.add_edge(e.source_id, e.target_id, relation_type=erel, weight=float(e.weight))

    # Calculate graph centrality metrics
    in_degrees = dict(G.in_degree())
    out_degrees = dict(G.out_degree())
    total_degrees = dict(G.degree())
    betweenness = nx.betweenness_centrality(G) if len(G) > 0 else {}

    # Group history records by node_id
    history_by_node = {}
    for h in history:
        nid = h.node_id
        if nid not in history_by_node:
            history_by_node[nid] = []
        history_by_node[nid].append({
            "timestamp": h.timestamp,
            "metric_name": h.metric_name,
            "metric_value": float(h.metric_value) if h.metric_value is not None else None,
            "unit": h.unit or "",
            "state_value": h.state_value or "",
            "event_summary": getattr(h, "notes", "") or getattr(h, "metric_name", "") or "",
            "source_url": getattr(h, "source_id", "") or ""
        })

    # Sort each node's history chronologically
    for nid in history_by_node:
        history_by_node[nid].sort(key=lambda x: x["timestamp"])

    # Prepare node objects
    viz_nodes = []
    for n in nodes:
        ntype = n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type)
        nid = n.id
        viz_nodes.append({
            "id": nid,
            "label": n.label,
            "type": ntype,
            "description": n.description,
            "jurisdiction": n.jurisdiction or "EU",
            "activation_date": n.activation_date or None,
            "in_degree": in_degrees.get(nid, 0),
            "out_degree": out_degrees.get(nid, 0),
            "total_degree": total_degrees.get(nid, 0),
            "betweenness": round(betweenness.get(nid, 0.0), 4),
            "history_count": len(history_by_node.get(nid, [])),
            "history": history_by_node.get(nid, [])[:15]
        })

    # Prepare edge objects
    viz_edges = []
    for e in edges:
        erel = e.relation_type.value if hasattr(e.relation_type, "value") else str(e.relation_type)
        viz_edges.append({
            "source": e.source_id,
            "target": e.target_id,
            "relation": erel,
            "weight": float(e.weight),
            "timestamp": e.timestamp or ""
        })

    payload = {
        "metadata": {
            "title": "PowerLaw Dual-Layer Construction Knowledge Graph",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_nodes": len(viz_nodes),
            "total_edges": len(viz_edges),
            "total_history_records": len(history)
        },
        "nodes": viz_nodes,
        "edges": viz_edges
    }

    html_content = generate_html_viewer(payload)
    if not persist:
        return payload, html_content

    # Save JSON only from the explicit export command, never from dashboard reads.
    json_path = repo_root / "visualizations" / "graph_data.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    if output_paths is None:
        output_paths = [repo_root / "visualizations" / "interactive_graph.html"]

    for p in output_paths:
        try:
            target = Path(p)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(html_content)
            print(f"Exported interactive graph to: {target.resolve()}")
        except Exception as err:
            print(f"Notice: Could not write to {p}: {err}")

    return payload, html_content

def generate_html_viewer(payload):
    json_str = json.dumps(payload).replace("</script>", "<\\/script>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PowerLaw: Updatable Interactive Knowledge Graph Explorer</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 0;
      overflow: hidden;
      background-color: #07090E;
      color: #E2E8F0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      user-select: none;
    }}
    #canvas-container {{
      position: absolute;
      top: 0;
      left: 0;
      width: 100vw;
      height: 100vh;
      overflow: hidden;
    }}
    canvas {{
      display: block;
      width: 100%;
      height: 100%;
      cursor: grab;
    }}
    canvas:active {{
      cursor: grabbing;
    }}
    .glass-panel {{
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.1);
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
    }}
    .badge-chip {{
      cursor: pointer;
      transition: all 0.2s ease;
    }}
    .badge-chip:hover {{
      transform: translateY(-1px);
    }}
    .badge-chip.inactive {{
      opacity: 0.35;
      filter: grayscale(80%);
    }}
    .custom-tooltip {{
      position: absolute;
      pointer-events: none;
      z-index: 50;
      display: none;
      transition: opacity 0.15s ease;
    }}
    /* Modal Backdrop */
    .modal-backdrop {{
      background: rgba(0, 0, 0, 0.7);
      backdrop-filter: blur(8px);
    }}
  </style>
</head>
<body class="bg-[#07090E] text-slate-100">

  <!-- Main Graph Canvas -->
  <div id="canvas-container">
    <canvas id="graphCanvas"></canvas>
  </div>

  <!-- Top Floating Navigation & Action Bar -->
  <header class="absolute top-4 left-4 right-4 z-20 flex flex-wrap items-center justify-between gap-4 pointer-events-none">
    
    <!-- Branding & Live Status -->
    <div class="pointer-events-auto glass-panel rounded-2xl px-4 py-2.5 flex items-center gap-3.5">
      <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center text-lg shadow-lg shadow-blue-500/30">
        ⚖️
      </div>
      <div>
        <div class="flex items-center gap-2">
          <h1 class="text-sm font-bold text-white tracking-wide">PowerLaw Graph Explorer</h1>
          <span class="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
            LIVE & UPDATABLE
          </span>
        </div>
        <div class="flex items-center gap-2.5 text-xs text-slate-400 mt-0.5 font-mono">
          <span><b id="kpi-nodes" class="text-white font-semibold">68</b> Nodes</span>
          <span>•</span>
          <span><b id="kpi-edges" class="text-white font-semibold">109</b> Edges</span>
          <span>•</span>
          <span><b id="kpi-history" class="text-white font-semibold">1,038</b> Records</span>
        </div>
      </div>
    </div>

    <!-- Search Bar with Live Autocomplete -->
    <div class="pointer-events-auto relative w-72">
      <div class="glass-panel rounded-2xl px-3 py-2 flex items-center gap-2 focus-within:ring-2 focus-within:ring-blue-500/60 transition-all">
        <svg class="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
        </svg>
        <input 
          id="searchInput" 
          type="text" 
          placeholder="Search entities, commodities, CBAM..." 
          class="bg-transparent text-xs text-white placeholder-slate-400 focus:outline-none w-full"
          autocomplete="off"
        />
        <button id="clearSearchBtn" class="text-slate-400 hover:text-white hidden text-xs">✕</button>
      </div>
      <!-- Autocomplete Dropdown -->
      <div id="searchDropdown" class="glass-panel absolute left-0 right-0 top-11 mt-1 rounded-xl max-h-60 overflow-y-auto hidden z-50 p-1 shadow-2xl">
      </div>
    </div>

    <!-- Updatable Authoring Controls & Layout Switcher -->
    <div class="pointer-events-auto glass-panel rounded-2xl p-1.5 flex items-center gap-1.5">
      
      <!-- Interactive Update Buttons -->
      <button id="btnOpenAddNode" class="px-2.5 py-1.5 rounded-xl text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white transition-all shadow-sm flex items-center gap-1" title="Add a new Regulation, Action, Reaction, or Commodity to the Graph">
        <span>➕</span> Node
      </button>

      <button id="btnOpenAddEdge" class="px-2.5 py-1.5 rounded-xl text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white transition-all shadow-sm flex items-center gap-1" title="Connect two nodes with a directed causal relationship">
        <span>🔗</span> Connect
      </button>

      <div class="w-px h-5 bg-slate-700 mx-1"></div>

      <!-- Layout Mode Buttons -->
      <button id="btnLayoutForce" class="px-2.5 py-1.5 rounded-xl text-xs font-medium bg-blue-600 text-white transition-all shadow-sm" title="Organic Spring Physics">
        Physics
      </button>
      <button id="btnLayoutPipeline" class="px-2.5 py-1.5 rounded-xl text-xs font-medium text-slate-300 hover:text-white hover:bg-slate-800 transition-all" title="Left-to-Right Causal Pipeline (Law → Action → Reaction → Market)">
        Pipeline
      </button>
      <button id="btnLayoutRadial" class="px-2.5 py-1.5 rounded-xl text-xs font-medium text-slate-300 hover:text-white hover:bg-slate-800 transition-all" title="Concentric Type Rings">
        Radial
      </button>

      <div class="w-px h-5 bg-slate-700 mx-1"></div>

      <!-- Utility Tools -->
      <button id="btnCenterView" class="p-2 rounded-xl text-slate-300 hover:text-white hover:bg-slate-800 transition-all" title="Center & Reset View">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4"></path></svg>
      </button>
      <button id="btnTogglePhysics" class="p-2 rounded-xl text-slate-300 hover:text-white hover:bg-slate-800 transition-all" title="Pause / Resume Physics Simulation">
        <svg id="iconPause" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
        <svg id="iconPlay" class="w-4 h-4 hidden" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
      </button>
      <button id="btnExportJson" class="p-2 rounded-xl text-slate-300 hover:text-white hover:bg-slate-800 transition-all" title="Export Live Graph JSON">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
      </button>
      <button id="btnExportPng" class="p-2 rounded-xl text-slate-300 hover:text-white hover:bg-slate-800 transition-all" title="Export High-Res PNG Snapshot">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>
      </button>
    </div>
  </header>

  <!-- Left Category Filter Pills -->
  <aside class="absolute left-4 top-24 z-20 flex flex-col gap-2 pointer-events-none">
    <div class="pointer-events-auto glass-panel rounded-2xl p-3 flex flex-col gap-2 max-w-xs shadow-xl">
      <div class="text-[10px] font-bold tracking-wider text-slate-400 uppercase mb-0.5 flex items-center justify-between">
        <span>Node Categories</span>
        <button id="btnToggleAllFilters" class="text-blue-400 hover:underline lowercase font-normal">all</button>
      </div>

      <div class="flex flex-col gap-1.5 text-xs">
        <div class="badge-chip flex items-center justify-between px-2.5 py-1.5 rounded-xl bg-blue-500/15 border border-blue-500/30 text-blue-300" data-type="LAW">
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full bg-blue-500 shadow-sm shadow-blue-500"></span>
            <span class="font-medium">📜 EU Regulations</span>
          </div>
          <span id="count-LAW" class="font-bold text-blue-200">8</span>
        </div>

        <div class="badge-chip flex items-center justify-between px-2.5 py-1.5 rounded-xl bg-amber-500/15 border border-amber-500/30 text-amber-300" data-type="ACTION">
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full bg-amber-500 shadow-sm shadow-amber-500"></span>
            <span class="font-medium">⚡ Mandated Actions</span>
          </div>
          <span id="count-ACTION" class="font-bold text-amber-200">12</span>
        </div>

        <div class="badge-chip flex items-center justify-between px-2.5 py-1.5 rounded-xl bg-red-500/15 border border-red-500/30 text-red-300" data-type="REACTION">
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full bg-red-500 shadow-sm shadow-red-500"></span>
            <span class="font-medium">🎯 Market Reactions</span>
          </div>
          <span id="count-REACTION" class="font-bold text-red-200">19</span>
        </div>

        <div class="badge-chip flex items-center justify-between px-2.5 py-1.5 rounded-xl bg-emerald-500/15 border border-emerald-500/30 text-emerald-300" data-type="SECTOR">
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full bg-emerald-500 shadow-sm shadow-emerald-500"></span>
            <span class="font-medium">🏭 NACE Sectors</span>
          </div>
          <span id="count-SECTOR" class="font-bold text-emerald-200">9</span>
        </div>

        <div class="badge-chip flex items-center justify-between px-2.5 py-1.5 rounded-xl bg-purple-500/15 border border-purple-500/30 text-purple-300" data-type="COMMODITY">
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full bg-purple-500 shadow-sm shadow-purple-500"></span>
            <span class="font-medium">🧱 Commodities</span>
          </div>
          <span id="count-COMMODITY" class="font-bold text-purple-200">10</span>
        </div>

        <div class="badge-chip flex items-center justify-between px-2.5 py-1.5 rounded-xl bg-yellow-500/15 border border-yellow-500/30 text-yellow-300" data-type="ECONOMIC_INDEX">
          <div class="flex items-center gap-2">
            <span class="w-2.5 h-2.5 rounded-full bg-yellow-500 shadow-sm shadow-yellow-500"></span>
            <span class="font-medium">📈 Equities & Indices</span>
          </div>
          <span id="count-ECONOMIC_INDEX" class="font-bold text-yellow-200">10</span>
        </div>
      </div>
    </div>

    <!-- Edge Relationship Legend -->
    <div class="pointer-events-auto glass-panel rounded-2xl p-2.5 text-xs text-slate-400 flex flex-col gap-1.5 shadow-xl max-w-xs">
      <span class="text-[10px] font-bold uppercase tracking-wider text-slate-500">Causal Relations</span>
      <div class="grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
        <div class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-blue-400"></span> MANDATES</div>
        <div class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-amber-400"></span> TRIGGERS</div>
        <div class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-emerald-400"></span> AFFECTS</div>
        <div class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-red-400"></span> IMPACTS</div>
        <div class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-slate-400"></span> REGULATES</div>
        <div class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full bg-pink-400"></span> CAUSES</div>
      </div>
    </div>
  </aside>

  <!-- Right Flyout Inspector Panel (With Live State Updates) -->
  <div id="inspectorDrawer" class="glass-panel fixed top-4 right-4 bottom-4 w-96 rounded-2xl z-40 transform translate-x-[110%] transition-transform duration-300 ease-out flex flex-col overflow-hidden shadow-2xl">
    
    <!-- Drawer Header -->
    <div class="p-4 border-b border-slate-700/60 flex items-start justify-between gap-3 bg-slate-900/40">
      <div>
        <div id="inspectorBadge" class="inline-block text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wider mb-1">
          LAW
        </div>
        <h2 id="inspectorTitle" class="text-base font-bold text-white leading-tight">
          Node Title
        </h2>
        <div id="inspectorId" class="text-xs font-mono text-slate-400 mt-0.5">
          NODE_ID
        </div>
      </div>
      <button id="btnCloseDrawer" class="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
      </button>
    </div>

    <!-- Drawer Content Scroll Area -->
    <div class="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
      
      <!-- Metadata Grid -->
      <div class="grid grid-cols-2 gap-2 bg-slate-900/60 p-2.5 rounded-xl border border-slate-800">
        <div>
          <span class="text-slate-500 uppercase text-[10px] font-semibold">Jurisdiction</span>
          <div id="metaJurisdiction" class="text-white font-medium text-xs mt-0.5">EU</div>
        </div>
        <div>
          <span class="text-slate-500 uppercase text-[10px] font-semibold">Activation Date</span>
          <div id="metaDate" class="text-white font-medium text-xs mt-0.5">Unknown</div>
        </div>
        <div>
          <span class="text-slate-500 uppercase text-[10px] font-semibold">In / Out Degree</span>
          <div id="metaDegrees" class="text-white font-medium text-xs mt-0.5">3 in • 5 out</div>
        </div>
        <div>
          <span class="text-slate-500 uppercase text-[10px] font-semibold">Betweenness Centrality</span>
          <div id="metaBetweenness" class="text-white font-medium text-xs mt-0.5">0.1425</div>
        </div>
      </div>

      <!-- Description -->
      <div>
        <h3 class="text-slate-400 uppercase text-[10px] font-bold tracking-wider mb-1">Statutory / Market Description</h3>
        <p id="inspectorDescription" class="text-slate-200 leading-relaxed bg-slate-900/40 p-2.5 rounded-xl border border-slate-800">
          Description content goes here...
        </p>
      </div>

      <!-- Causal Cascade Links -->
      <div>
        <h3 class="text-slate-400 uppercase text-[10px] font-bold tracking-wider mb-1.5 flex items-center justify-between">
          <span>Upstream Drivers (<span id="countUpstream">0</span>)</span>
        </h3>
        <div id="listUpstream" class="space-y-1.5"></div>
      </div>

      <div>
        <h3 class="text-slate-400 uppercase text-[10px] font-bold tracking-wider mb-1.5 flex items-center justify-between">
          <span>Downstream Impacts (<span id="countDownstream">0</span>)</span>
        </h3>
        <div id="listDownstream" class="space-y-1.5"></div>
      </div>

      <!-- Tabular History & Live State Record Injection -->
      <div>
        <div class="flex items-center justify-between mb-1.5">
          <h3 class="text-slate-400 uppercase text-[10px] font-bold tracking-wider">
            Temporal Ledger (<span id="countHistory">0</span>)
          </h3>
          <button id="btnOpenAddStateModal" class="text-[10px] text-blue-400 hover:text-blue-300 font-medium">
            + Log Metric
          </button>
        </div>
        <div id="historyTable" class="space-y-1.5 max-h-48 overflow-y-auto"></div>
      </div>
    </div>

    <!-- Drawer Footer Actions -->
    <div class="p-3 border-t border-slate-700/60 bg-slate-900/60 flex items-center justify-between gap-2">
      <button id="btnFocusEgo" class="flex-1 py-1.5 px-3 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-medium text-xs transition-colors shadow">
        Isolate Shock Path
      </button>
      <button id="btnClearIsolation" class="py-1.5 px-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition-colors">
        Show All
      </button>
    </div>
  </div>

  <!-- Bottom Floating Timeline & Playback Bar -->
  <footer class="absolute bottom-4 left-4 right-4 z-20 pointer-events-none flex justify-center">
    <div class="pointer-events-auto glass-panel rounded-2xl px-5 py-2.5 flex items-center gap-4 max-w-2xl w-full shadow-2xl">
      <button id="btnPlayTimeline" class="p-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs transition-all shadow-md flex items-center justify-center">
        <svg id="iconTimelinePlay" class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"></path></svg>
        <svg id="iconTimelinePause" class="w-4 h-4 hidden" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 9v6m4-6v6"></path></svg>
      </button>
      <div class="flex-1 flex flex-col gap-1">
        <div class="flex items-center justify-between text-[11px] text-slate-400">
          <span>Temporal Playback As-Of: <b id="currentTimelineDate" class="text-white font-mono">Unknown</b></span>
          <span class="text-slate-500">2021 — 2026</span>
        </div>
        <input 
          id="timelineSlider" 
          type="range" 
          min="0" 
          max="100" 
          value="100" 
          class="w-full h-1.5 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
        />
      </div>
      <div class="text-xs font-mono px-2.5 py-1 rounded-lg bg-slate-800 text-blue-400 border border-slate-700/80">
        <span id="activeNodesCount">68</span> active
      </div>
    </div>
  </footer>

  <!-- Hover Tooltip -->
  <div id="hoverTooltip" class="custom-tooltip glass-panel rounded-xl p-3 shadow-2xl border border-slate-700/80 max-w-xs text-xs pointer-events-none">
    <div id="tooltipType" class="text-[9px] uppercase font-bold tracking-wider mb-0.5 text-blue-400">LAW</div>
    <div id="tooltipTitle" class="font-bold text-white text-sm mb-1 leading-snug">Title</div>
    <div id="tooltipDesc" class="text-slate-300 text-[11px] line-clamp-2">Description...</div>
  </div>

  <!-- ==================== MODALS FOR LIVE UPDATES ==================== -->

  <!-- Modal 1: ➕ Add Entity / Node -->
  <div id="modalAddNode" class="modal-backdrop fixed inset-0 z-50 flex items-center justify-center p-4 hidden">
    <div class="glass-panel w-full max-w-md rounded-2xl p-5 border border-slate-700/80 shadow-2xl flex flex-col gap-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2">
          <span class="text-lg">➕</span>
          <h3 class="text-sm font-bold text-white">Add Entity to Knowledge Graph</h3>
        </div>
        <button id="btnCloseModalNode" class="text-slate-400 hover:text-white text-sm">✕</button>
      </div>

      <form id="formAddNode" class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Entity Label / Name</label>
          <input id="inputNodeLabel" type="text" placeholder="e.g. EU Low-Carbon Cement Mandate 2027" required class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500" />
        </div>

        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Node Type</label>
            <select id="selectNodeType" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-2.5 py-2 text-white focus:outline-none focus:border-blue-500">
              <option value="LAW">📜 LAW (Regulation)</option>
              <option value="ACTION">⚡ ACTION (Mandate)</option>
              <option value="REACTION" selected>🎯 REACTION (Market Shock)</option>
              <option value="COMMODITY">🧱 COMMODITY (Physical Good)</option>
              <option value="SECTOR">🏭 SECTOR (NACE)</option>
              <option value="ECONOMIC_INDEX">📈 ECONOMIC_INDEX (Stock/FX)</option>
            </select>
          </div>
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Activation Date</label>
            <input id="inputNodeDate" type="date" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-2.5 py-2 text-white focus:outline-none focus:border-blue-500" />
          </div>
        </div>

        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Description / Market Rationale</label>
          <textarea id="inputNodeDesc" rows="3" placeholder="Explain the compliance rule, market impact, or cost shift..." required class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"></textarea>
        </div>

        <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
          <button type="button" id="btnCancelAddNode" class="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300">Cancel</button>
          <button type="submit" class="px-4 py-1.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold">Inject Node</button>
        </div>
      </form>
    </div>
  </div>

  <!-- Modal 2: 🔗 Connect Causal Edge -->
  <div id="modalAddEdge" class="modal-backdrop fixed inset-0 z-50 flex items-center justify-center p-4 hidden">
    <div class="glass-panel w-full max-w-md rounded-2xl p-5 border border-slate-700/80 shadow-2xl flex flex-col gap-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2">
          <span class="text-lg">🔗</span>
          <h3 class="text-sm font-bold text-white">Connect Causal Relationship</h3>
        </div>
        <button id="btnCloseModalEdge" class="text-slate-400 hover:text-white text-sm">✕</button>
      </div>

      <form id="formAddEdge" class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Source Entity (Driver / Cause)</label>
          <select id="selectEdgeSource" required class="w-full bg-slate-900 border border-slate-700 rounded-xl px-2.5 py-2 text-white focus:outline-none focus:border-blue-500 max-h-32">
          </select>
        </div>

        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Relationship Type</label>
            <select id="selectEdgeRelation" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-2.5 py-2 text-white focus:outline-none focus:border-blue-500">
              <option value="MANDATES">MANDATES (Statutory)</option>
              <option value="TRIGGERS" selected>TRIGGERS (Action/Shock)</option>
              <option value="AFFECTS">AFFECTS (Cost / Margin)</option>
              <option value="IMPACTS">IMPACTS (Sector Output)</option>
              <option value="REGULATES">REGULATES (Compliance)</option>
              <option value="CAUSES">CAUSES (Direct Shock)</option>
            </select>
          </div>
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Causal Weight (Impact)</label>
            <input id="inputEdgeWeight" type="number" step="0.1" min="0.1" max="5.0" value="1.5" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-2.5 py-2 text-white focus:outline-none focus:border-blue-500" />
          </div>
        </div>

        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Target Entity (Receiver / Shocked)</label>
          <select id="selectEdgeTarget" required class="w-full bg-slate-900 border border-slate-700 rounded-xl px-2.5 py-2 text-white focus:outline-none focus:border-blue-500 max-h-32">
          </select>
        </div>

        <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
          <button type="button" id="btnCancelAddEdge" class="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300">Cancel</button>
          <button type="submit" class="px-4 py-1.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold">Connect Edge</button>
        </div>
      </form>
    </div>
  </div>

  <!-- Modal 3: ⏱️ Log Metric / State Record -->
  <div id="modalAddState" class="modal-backdrop fixed inset-0 z-50 flex items-center justify-center p-4 hidden">
    <div class="glass-panel w-full max-w-md rounded-2xl p-5 border border-slate-700/80 shadow-2xl flex flex-col gap-4">
      <div class="flex items-center justify-between border-b border-slate-800 pb-3">
        <div class="flex items-center gap-2">
          <span class="text-lg">⏱️</span>
          <h3 class="text-sm font-bold text-white">Log Tabular Metric / State</h3>
        </div>
        <button id="btnCloseModalState" class="text-slate-400 hover:text-white text-sm">✕</button>
      </div>

      <form id="formAddState" class="space-y-3 text-xs">
        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Target Entity</label>
          <div id="targetStateEntityLabel" class="text-white font-semibold py-1 px-2 rounded-lg bg-slate-800">Entity</div>
        </div>

        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Timestamp</label>
            <input id="inputStateDate" type="date" required class="w-full bg-slate-900 border border-slate-700 rounded-xl px-2.5 py-2 text-white focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">State Value</label>
            <select id="selectStateValue" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-2.5 py-2 text-white focus:outline-none focus:border-blue-500">
              <option value="ACTIVE" selected>ACTIVE</option>
              <option value="PEAK">PEAK</option>
              <option value="TARGET_MET">TARGET_MET</option>
              <option value="REVISED">REVISED</option>
              <option value="STRESS">STRESS</option>
            </select>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Metric Value</label>
            <input id="inputStateVal" type="number" step="0.01" placeholder="e.g. 740.0" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-2.5 py-2 text-white focus:outline-none focus:border-blue-500" />
          </div>
          <div>
            <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Unit</label>
            <input id="inputStateUnit" type="text" placeholder="EUR/t, %, index" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-2.5 py-2 text-white focus:outline-none focus:border-blue-500" />
          </div>
        </div>

        <div>
          <label class="block text-slate-400 uppercase text-[10px] font-semibold mb-1">Event Summary</label>
          <input id="inputStateSummary" type="text" placeholder="e.g. Spot price surge following CBAM import audits" required class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500" />
        </div>

        <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-800">
          <button type="button" id="btnCancelAddState" class="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300">Cancel</button>
          <button type="submit" class="px-4 py-1.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-semibold">Record State</button>
        </div>
      </form>
    </div>
  </div>

  <!-- Raw Embedded Graph Data -->
  <script id="graphDataJson" type="application/json">
{json_str}
  </script>

  <!-- Interactive & Updatable Graph Engine Implementation -->
  <script>
    (function() {{
      const rawData = JSON.parse(document.getElementById('graphDataJson').textContent);
      const canvas = document.getElementById('graphCanvas');
      const ctx = canvas.getContext('2d');
      const tooltip = document.getElementById('hoverTooltip');
      const drawer = document.getElementById('inspectorDrawer');

      // Theme Colors
      const NODE_COLORS = {{
        'LAW': '#3B82F6',           // Blue
        'ACTION': '#F59E0B',        // Amber
        'REACTION': '#EF4444',      // Red
        'SECTOR': '#10B981',        // Emerald
        'COMMODITY': '#8B5CF6',     // Violet
        'ECONOMIC_INDEX': '#EAB308' // Gold
      }};

      const EDGE_COLORS = {{
        'MANDATES': '#60A5FA',
        'TRIGGERS': '#FBBF24',
        'AFFECTS': '#34D399',
        'IMPACTS': '#F87171',
        'REGULATES': '#94A3B8',
        'CAUSES': '#F472B6'
      }};

      // Active state
      let nodes = [];
      let edges = [];
      let nodeMap = new Map();
      let activeTypes = new Set(['LAW', 'ACTION', 'REACTION', 'SECTOR', 'COMMODITY', 'ECONOMIC_INDEX']);
      let selectedNode = null;
      let hoveredNode = null;
      let currentLayout = 'force'; // 'force', 'pipeline', 'radial'
      let isSimRunning = true;
      let dragNode = null;

      // Transform state (Pan & Zoom)
      let transform = {{ x: 0, y: 0, scale: 0.95 }};
      let isPanning = false;
      let startPan = {{ x: 0, y: 0 }};

      // Timeline state
      let timelineIndex = 100;
      let isTimelinePlaying = false;
      let timelineTimer = null;
      const storedDates = rawData.nodes.flatMap(n => [n.activation_date, ...(n.history || []).map(h => h.timestamp)])
        .concat(rawData.edges.map(e => e.timestamp)).filter(Boolean).map(d => new Date(d).getTime()).filter(Number.isFinite);
      let minDate = storedDates.length ? Math.min(...storedDates) : null;
      let maxDate = storedDates.length ? Math.max(...storedDates) : null;

      // Initialize Nodes & Edges
      function initData() {{
        const width = window.innerWidth;
        const height = window.innerHeight;

        nodes = rawData.nodes.map(n => {{
          const maxDeg = Math.max(...rawData.nodes.map(x => x.total_degree || 1));
          const deg = n.total_degree || 1;
          const radius = 13 + (deg / maxDeg) * 22; // scale radius 13..35
          const actDate = n.activation_date ? new Date(n.activation_date).getTime() : null;

          return {{
            ...n,
            x: width / 2 + (Math.random() - 0.5) * (width * 0.7),
            y: height / 2 + (Math.random() - 0.5) * (height * 0.7),
            vx: 0,
            vy: 0,
            radius: radius,
            color: NODE_COLORS[n.type] || '#94A3B8',
            actDate: actDate,
            pinned: false
          }};
        }});

        nodes.forEach(n => nodeMap.set(n.id, n));

        edges = rawData.edges.filter(e => nodeMap.has(e.source) && nodeMap.has(e.target)).map(e => {{
          return {{
            ...e,
            sourceNode: nodeMap.get(e.source),
            targetNode: nodeMap.get(e.target),
            color: EDGE_COLORS[e.relation] || '#64748B'
          }};
        }});

        updateKpiCounts();
        populateEdgeDropdowns();
        resetView();
      }}

      function updateKpiCounts() {{
        document.getElementById('kpi-nodes').textContent = nodes.length;
        document.getElementById('kpi-edges').textContent = edges.length;
        let totalHist = 0;
        nodes.forEach(n => {{ totalHist += (n.history ? n.history.length : 0); }});
        document.getElementById('kpi-history').textContent = totalHist.toLocaleString();

        const counts = {{ 'LAW': 0, 'ACTION': 0, 'REACTION': 0, 'SECTOR': 0, 'COMMODITY': 0, 'ECONOMIC_INDEX': 0 }};
        nodes.forEach(n => {{ if (counts[n.type] !== undefined) counts[n.type]++; }});
        Object.keys(counts).forEach(k => {{
          const el = document.getElementById(`count-${{k}}`);
          if (el) el.textContent = counts[k];
        }});
        updateActiveCounts();
      }}

      function populateEdgeDropdowns() {{
        const sSource = document.getElementById('selectEdgeSource');
        const sTarget = document.getElementById('selectEdgeTarget');
        if (!sSource || !sTarget) return;

        sSource.innerHTML = '';
        sTarget.innerHTML = '';

        const sorted = [...nodes].sort((a, b) => a.label.localeCompare(b.label));
        sorted.forEach(n => {{
          const opt1 = document.createElement('option');
          opt1.value = n.id;
          opt1.textContent = `${{n.label}} (${{n.type}})`;
          sSource.appendChild(opt1);

          const opt2 = document.createElement('option');
          opt2.value = n.id;
          opt2.textContent = `${{n.label}} (${{n.type}})`;
          sTarget.appendChild(opt2);
        }});
      }}

      // Canvas resize
      function resize() {{
        const dpr = window.devicePixelRatio || 1;
        canvas.width = window.innerWidth * dpr;
        canvas.height = window.innerHeight * dpr;
        ctx.scale(dpr, dpr);
      }}
      window.addEventListener('resize', resize);
      resize();
      initData();

      // --- Force Physics Simulation ---
      function updatePhysics() {{
        if (!isSimRunning && currentLayout === 'force') return;

        const width = window.innerWidth;
        const height = window.innerHeight;

        if (currentLayout === 'force') {{
          // Multi-body repulsion
          const kRep = 1450;
          for (let i = 0; i < nodes.length; i++) {{
            const a = nodes[i];
            if (!activeTypes.has(a.type)) continue;

            for (let j = i + 1; j < nodes.length; j++) {{
              const b = nodes[j];
              if (!activeTypes.has(b.type)) continue;

              const dx = b.x - a.x;
              const dy = b.y - a.y;
              const dist = Math.sqrt(dx * dx + dy * dy) || 1;
              const minDist = a.radius + b.radius + 18;

              let force = kRep / (dist * dist);
              if (dist < minDist) {{
                force += (minDist - dist) * 0.55; // collision repulsion
              }}

              const fx = (dx / dist) * force;
              const fy = (dy / dist) * force;

              if (!a.pinned) {{ a.vx -= fx; a.vy -= fy; }}
              if (!b.pinned) {{ b.vx += fx; b.vy += fy; }}
            }}

            // Center gravity
            const cx = width / 2;
            const cy = height / 2;
            const cdx = cx - a.x;
            const cdy = cy - a.y;
            if (!a.pinned) {{
              a.vx += cdx * 0.0009;
              a.vy += cdy * 0.0009;
            }}
          }}

          // Hooke spring tension along edges
          const kSpring = 0.048;
          const targetLen = 135;
          edges.forEach(e => {{
            const a = e.sourceNode;
            const b = e.targetNode;
            if (!activeTypes.has(a.type) || !activeTypes.has(b.type)) return;

            const dx = b.x - a.x;
            const dy = b.y - a.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const force = (dist - targetLen) * kSpring;

            const fx = (dx / dist) * force;
            const fy = (dy / dist) * force;

            if (!a.pinned) {{ a.vx += fx; a.vy += fy; }}
            if (!b.pinned) {{ b.vx += fx; b.vy += fy; }}
          }});

          // Position integration & damping
          const damping = 0.86;
          nodes.forEach(n => {{
            if (!n.pinned) {{
              n.vx *= damping;
              n.vy *= damping;
              n.x += n.vx;
              n.y += n.vy;
            }}
          }});
        }} else if (currentLayout === 'pipeline') {{
          // Hierarchical columns: Law (0), Action (1), Reaction (2), Sector/Commodity (3), Stocks (4)
          const cols = {{
            'LAW': 0,
            'ACTION': 1,
            'REACTION': 2,
            'COMMODITY': 3,
            'SECTOR': 3,
            'ECONOMIC_INDEX': 4
          }};
          const colX = [width * 0.12, width * 0.32, width * 0.52, width * 0.72, width * 0.88];
          const colGroups = [[], [], [], [], []];
          nodes.forEach(n => {{
            if (activeTypes.has(n.type)) {{
              const c = cols[n.type] ?? 2;
              colGroups[c].push(n);
            }}
          }});

          colGroups.forEach((group, cIdx) => {{
            const targetX = colX[cIdx];
            const stepY = (height * 0.8) / Math.max(group.length + 1, 2);
            group.forEach((n, idx) => {{
              const targetY = height * 0.12 + (idx + 1) * stepY;
              n.x += (targetX - n.x) * 0.12;
              n.y += (targetY - n.y) * 0.12;
            }});
          }});
        }} else if (currentLayout === 'radial') {{
          // Concentric rings by type
          const ringRadii = {{
            'LAW': 120,
            'ACTION': 240,
            'REACTION': 360,
            'COMMODITY': 480,
            'SECTOR': 480,
            'ECONOMIC_INDEX': 580
          }};
          const groups = {{}};
          nodes.forEach(n => {{
            if (!groups[n.type]) groups[n.type] = [];
            if (activeTypes.has(n.type)) groups[n.type].push(n);
          }});

          const cx = width / 2;
          const cy = height / 2;
          Object.keys(groups).forEach(type => {{
            const list = groups[type];
            const r = ringRadii[type] || 300;
            const angleStep = (2 * Math.PI) / Math.max(list.length, 1);
            list.forEach((n, idx) => {{
              const angle = idx * angleStep;
              const targetX = cx + Math.cos(angle) * r;
              const targetY = cy + Math.sin(angle) * r;
              n.x += (targetX - n.x) * 0.12;
              n.y += (targetY - n.y) * 0.12;
            }});
          }});
        }}
      }}

      // --- Causal Path Discovery ---
      function getCausalChain(rootNode) {{
        if (!rootNode) return {{ upstream: new Set(), downstream: new Set() }};
        const upstream = new Set();
        const downstream = new Set();

        function findUpstream(currId) {{
          edges.forEach(e => {{
            if (e.target === currId && !upstream.has(e.source)) {{
              upstream.add(e.source);
              findUpstream(e.source);
            }}
          }});
        }}

        function findDownstream(currId) {{
          edges.forEach(e => {{
            if (e.source === currId && !downstream.has(e.target)) {{
              downstream.add(e.target);
              findDownstream(e.target);
            }}
          }});
        }}

        findUpstream(rootNode.id);
        findDownstream(rootNode.id);

        return {{ upstream, downstream }};
      }}

      // --- Rendering Loop ---
      function render() {{
        const width = window.innerWidth;
        const height = window.innerHeight;

        ctx.clearRect(0, 0, width, height);

        // Apply World Transform
        ctx.save();
        ctx.translate(transform.x, transform.y);
        ctx.scale(transform.scale, transform.scale);

        const activeNode = selectedNode || hoveredNode;
        const causal = getCausalChain(activeNode);
        const hasFocus = activeNode != null;

        const curTime = minDate + (timelineIndex / 100) * (maxDate - minDate);

        // 1. Draw Edges
        edges.forEach(e => {{
          const a = e.sourceNode;
          const b = e.targetNode;
          if (!activeTypes.has(a.type) || !activeTypes.has(b.type)) return;

          if (a.actDate > curTime || b.actDate > curTime) return;

          const isConnected = hasFocus && (
            (e.source === activeNode.id) || 
            (e.target === activeNode.id) ||
            (causal.upstream.has(e.source) && causal.upstream.has(e.target)) ||
            (causal.downstream.has(e.source) && causal.downstream.has(e.target))
          );

          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);

          if (hasFocus) {{
            if (isConnected) {{
              ctx.strokeStyle = (e.source === activeNode.id || causal.downstream.has(e.target)) 
                ? '#F59E0B' // downstream orange
                : '#38BDF8'; // upstream cyan
              ctx.lineWidth = 2.8;
              ctx.globalAlpha = 1.0;
            }} else {{
              ctx.strokeStyle = '#334155';
              ctx.lineWidth = 0.8;
              ctx.globalAlpha = 0.12;
            }}
          }} else {{
            ctx.strokeStyle = e.color;
            ctx.lineWidth = 1.2;
            ctx.globalAlpha = 0.45;
          }}

          ctx.stroke();

          // Arrowhead
          const angle = Math.atan2(b.y - a.y, b.x - a.x);
          const arrowDist = b.radius + 6;
          const arrowX = b.x - Math.cos(angle) * arrowDist;
          const arrowY = b.y - Math.sin(angle) * arrowDist;

          ctx.save();
          ctx.translate(arrowX, arrowY);
          ctx.rotate(angle);
          ctx.beginPath();
          ctx.moveTo(0, 0);
          ctx.lineTo(-6, -3.5);
          ctx.lineTo(-6, 3.5);
          ctx.closePath();
          ctx.fillStyle = ctx.strokeStyle;
          ctx.fill();
          ctx.restore();
        }});

        // 2. Draw Nodes
        nodes.forEach(n => {{
          if (!activeTypes.has(n.type)) return;

          const isTimeActive = n.actDate !== null && n.actDate <= curTime;
          if (!isTimeActive) return;

          const isSelected = selectedNode && selectedNode.id === n.id;
          const isHovered = hoveredNode && hoveredNode.id === n.id;
          const isUpstream = hasFocus && causal.upstream.has(n.id);
          const isDownstream = hasFocus && causal.downstream.has(n.id);
          const isPathNode = isSelected || isHovered || isUpstream || isDownstream;

          ctx.save();
          ctx.globalAlpha = hasFocus ? (isPathNode ? 1.0 : 0.15) : 1.0;

          // Outer Glow
          if (isSelected || isHovered) {{
            ctx.beginPath();
            ctx.arc(n.x, n.y, n.radius + 9, 0, Math.PI * 2);
            ctx.fillStyle = isSelected ? 'rgba(59, 130, 246, 0.4)' : 'rgba(255, 255, 255, 0.25)';
            ctx.fill();
          }} else if (isUpstream) {{
            ctx.beginPath();
            ctx.arc(n.x, n.y, n.radius + 6, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(56, 189, 248, 0.35)';
            ctx.fill();
          }} else if (isDownstream) {{
            ctx.beginPath();
            ctx.arc(n.x, n.y, n.radius + 6, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(245, 158, 11, 0.35)';
            ctx.fill();
          }}

          // Node Circle
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
          ctx.fillStyle = n.color;
          ctx.fill();

          // Border Ring
          ctx.lineWidth = isSelected ? 3.5 : (isHovered ? 2.5 : 1.5);
          ctx.strokeStyle = isSelected ? '#FFFFFF' : '#0B0F19';
          ctx.stroke();

          // Label text
          if (transform.scale > 0.42 || isPathNode) {{
            ctx.font = `${{isPathNode ? 'bold 12px' : '11px'}} sans-serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';

            const label = n.label.length > 20 ? n.label.slice(0, 18) + '...' : n.label;
            
            ctx.lineWidth = 3;
            ctx.strokeStyle = 'rgba(7, 9, 14, 0.9)';
            ctx.strokeText(label, n.x, n.y + n.radius + 5);

            ctx.fillStyle = isPathNode ? '#FFFFFF' : '#CBD5E1';
            ctx.fillText(label, n.x, n.y + n.radius + 5);
          }}

          ctx.restore();
        }});

        ctx.restore();
      }}

      // --- Animation Tick ---
      function tick() {{
        updatePhysics();
        render();
        requestAnimationFrame(tick);
      }}
      tick();

      // Coordinates
      function getCanvasCoords(evt) {{
        const rect = canvas.getBoundingClientRect();
        return {{
          x: (evt.clientX - rect.left - transform.x) / transform.scale,
          y: (evt.clientY - rect.top - transform.y) / transform.scale
        }};
      }}

      function findNodeAt(x, y) {{
        for (let i = nodes.length - 1; i >= 0; i--) {{
          const n = nodes[i];
          if (!activeTypes.has(n.type)) continue;
          const dx = n.x - x;
          const dy = n.y - y;
          if (dx * dx + dy * dy <= n.radius * n.radius + 40) {{
            return n;
          }}
        }}
        return null;
      }}

      // Mouse drag, zoom, pan
      canvas.addEventListener('mousedown', (e) => {{
        if (e.button !== 0) return;
        const coords = getCanvasCoords(e);
        const node = findNodeAt(coords.x, coords.y);

        if (node) {{
          dragNode = node;
          dragNode.pinned = true;
        }} else {{
          isPanning = true;
          startPan = {{ x: e.clientX - transform.x, y: e.clientY - transform.y }};
        }}
      }});

      canvas.addEventListener('mousemove', (e) => {{
        const coords = getCanvasCoords(e);

        if (dragNode) {{
          dragNode.x = coords.x;
          dragNode.y = coords.y;
          dragNode.vx = 0;
          dragNode.vy = 0;
          return;
        }}

        if (isPanning) {{
          transform.x = e.clientX - startPan.x;
          transform.y = e.clientY - startPan.y;
          return;
        }}

        const node = findNodeAt(coords.x, coords.y);
        if (node !== hoveredNode) {{
          hoveredNode = node;
          if (hoveredNode) {{
            showTooltip(e.clientX, e.clientY, hoveredNode);
          }} else {{
            hideTooltip();
          }}
        }} else if (hoveredNode) {{
          showTooltip(e.clientX, e.clientY, hoveredNode);
        }}
      }});

      window.addEventListener('mouseup', () => {{
        if (dragNode) {{
          dragNode.pinned = false;
          dragNode = null;
        }}
        isPanning = false;
      }});

      canvas.addEventListener('wheel', (e) => {{
        e.preventDefault();
        const zoomFactor = e.deltaY < 0 ? 1.12 : 0.89;
        const mouseX = e.clientX;
        const mouseY = e.clientY;

        const newScale = Math.max(0.2, Math.min(transform.scale * zoomFactor, 3.5));
        transform.x = mouseX - (mouseX - transform.x) * (newScale / transform.scale);
        transform.y = mouseY - (mouseY - transform.y) * (newScale / transform.scale);
        transform.scale = newScale;
      }}, {{ passive: false }});

      canvas.addEventListener('click', (e) => {{
        const coords = getCanvasCoords(e);
        const node = findNodeAt(coords.x, coords.y);

        if (node) {{
          selectNode(node);
        }} else {{
          selectedNode = null;
          closeDrawer();
        }}
      }});

      function selectNode(node) {{
        selectedNode = node;
        openDrawer(node);
      }}

      function showTooltip(x, y, node) {{
        tooltip.style.left = `${{x + 14}}px`;
        tooltip.style.top = `${{y + 14}}px`;
        document.getElementById('tooltipType').textContent = node.type;
        document.getElementById('tooltipType').style.color = node.color;
        document.getElementById('tooltipTitle').textContent = node.label;
        document.getElementById('tooltipDesc').textContent = node.description || 'No description available.';
        tooltip.style.display = 'block';
      }}

      function hideTooltip() {{
        tooltip.style.display = 'none';
      }}

      // Drawer
      function openDrawer(node) {{
        const badge = document.getElementById('inspectorBadge');
        badge.textContent = node.type;
        badge.style.backgroundColor = `${{node.color}}25`;
        badge.style.color = node.color;
        badge.style.border = `1px solid ${{node.color}}60`;

        document.getElementById('inspectorTitle').textContent = node.label;
        document.getElementById('inspectorId').textContent = node.id;
        document.getElementById('metaJurisdiction').textContent = node.jurisdiction || 'EU';
        document.getElementById('metaDate').textContent = node.activation_date || 'N/A';
        document.getElementById('metaDegrees').textContent = `${{node.in_degree}} in • ${{node.out_degree}} out`;
        document.getElementById('metaBetweenness').textContent = node.betweenness;
        document.getElementById('inspectorDescription').textContent = node.description || 'No description provided.';

        // Populate Upstream
        const listUp = document.getElementById('listUpstream');
        listUp.innerHTML = '';
        const upEdges = edges.filter(e => e.target === node.id);
        document.getElementById('countUpstream').textContent = upEdges.length;
        if (upEdges.length === 0) {{
          listUp.innerHTML = '<div class="text-slate-500 italic">None (Root source)</div>';
        }} else {{
          upEdges.forEach(e => {{
            const div = document.createElement('div');
            div.className = 'p-2 rounded-lg bg-slate-900/60 border border-slate-800 hover:border-blue-500/50 cursor-pointer flex items-center justify-between transition-all';
            div.innerHTML = `
              <div class="flex items-center gap-1.5 overflow-hidden">
                <span class="w-2 h-2 rounded-full" style="background:${{e.sourceNode.color}}"></span>
                <span class="font-medium text-slate-200 truncate">${{e.sourceNode.label}}</span>
              </div>
              <span class="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-slate-800 text-blue-300 border border-slate-700">${{e.relation}}</span>
            `;
            div.addEventListener('click', () => flyToNode(e.sourceNode));
            listUp.appendChild(div);
          }});
        }}

        // Populate Downstream
        const listDown = document.getElementById('listDownstream');
        listDown.innerHTML = '';
        const downEdges = edges.filter(e => e.source === node.id);
        document.getElementById('countDownstream').textContent = downEdges.length;
        if (downEdges.length === 0) {{
          listDown.innerHTML = '<div class="text-slate-500 italic">None (Terminal sink)</div>';
        }} else {{
          downEdges.forEach(e => {{
            const div = document.createElement('div');
            div.className = 'p-2 rounded-lg bg-slate-900/60 border border-slate-800 hover:border-amber-500/50 cursor-pointer flex items-center justify-between transition-all';
            div.innerHTML = `
              <div class="flex items-center gap-1.5 overflow-hidden">
                <span class="w-2 h-2 rounded-full" style="background:${{e.targetNode.color}}"></span>
                <span class="font-medium text-slate-200 truncate">${{e.targetNode.label}}</span>
              </div>
              <span class="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-slate-800 text-amber-300 border border-slate-700">${{e.relation}}</span>
            `;
            div.addEventListener('click', () => flyToNode(e.targetNode));
            listDown.appendChild(div);
          }});
        }}

        // History
        const hTable = document.getElementById('historyTable');
        hTable.innerHTML = '';
        const hList = node.history || [];
        document.getElementById('countHistory').textContent = hList.length;
        if (hList.length === 0) {{
          hTable.innerHTML = '<div class="text-slate-500 italic">No temporal records recorded.</div>';
        }} else {{
          hList.forEach(h => {{
            const div = document.createElement('div');
            div.className = 'p-2 rounded-lg bg-slate-900/80 border border-slate-800/80 text-[11px]';
            div.innerHTML = `
              <div class="flex items-center justify-between text-slate-400 font-mono text-[10px] mb-0.5">
                <span>${{h.timestamp}}</span>
                <span class="text-emerald-400 font-bold">${{h.metric_value !== null ? h.metric_value + ' ' + (h.unit || '') : h.state_value}}</span>
              </div>
              <div class="text-slate-200">${{h.event_summary || (h.metric_name ? h.metric_name : 'State log')}}</div>
            `;
            hTable.appendChild(div);
          }});
        }}

        drawer.classList.remove('translate-x-[110%]');
      }}

      function closeDrawer() {{
        drawer.classList.add('translate-x-[110%]');
      }}
      document.getElementById('btnCloseDrawer').addEventListener('click', closeDrawer);

      // Camera fly
      function flyToNode(node) {{
        if (!node) return;
        selectNode(node);

        const width = window.innerWidth;
        const height = window.innerHeight;
        const targetScale = 1.35;
        const targetX = width / 2 - node.x * targetScale;
        const targetY = height / 2 - node.y * targetScale;

        let start = performance.now();
        const duration = 400;
        const initX = transform.x;
        const initY = transform.y;
        const initScale = transform.scale;

        function animateFly(now) {{
          const elapsed = now - start;
          const p = Math.min(elapsed / duration, 1);
          const ease = p * (2 - p);

          transform.x = initX + (targetX - initX) * ease;
          transform.y = initY + (targetY - initY) * ease;
          transform.scale = initScale + (targetScale - initScale) * ease;

          if (p < 1) requestAnimationFrame(animateFly);
        }}
        requestAnimationFrame(animateFly);
      }}

      function resetView() {{
        transform = {{ x: 0, y: 0, scale: 0.95 }};
      }}
      document.getElementById('btnCenterView').addEventListener('click', resetView);

      // Search
      const searchInput = document.getElementById('searchInput');
      const searchDropdown = document.getElementById('searchDropdown');
      const clearSearchBtn = document.getElementById('clearSearchBtn');

      searchInput.addEventListener('input', (e) => {{
        const q = e.target.value.toLowerCase().trim();
        if (q.length === 0) {{
          searchDropdown.classList.add('hidden');
          clearSearchBtn.classList.add('hidden');
          return;
        }}
        clearSearchBtn.classList.remove('hidden');

        const matches = nodes.filter(n => 
          n.label.toLowerCase().includes(q) || 
          n.id.toLowerCase().includes(q) ||
          (n.description && n.description.toLowerCase().includes(q))
        ).slice(0, 8);

        if (matches.length === 0) {{
          searchDropdown.innerHTML = '<div class="p-2 text-xs text-slate-500 italic">No matching entities found.</div>';
        }} else {{
          searchDropdown.innerHTML = '';
          matches.forEach(m => {{
            const item = document.createElement('div');
            item.className = 'px-3 py-2 rounded-lg hover:bg-slate-800/80 cursor-pointer flex items-center justify-between text-xs transition-colors';
            item.innerHTML = `
              <div class="flex items-center gap-2 overflow-hidden">
                <span class="w-2.5 h-2.5 rounded-full" style="background:${{m.color}}"></span>
                <span class="font-medium text-white truncate">${{m.label}}</span>
              </div>
              <span class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-900 text-slate-400 border border-slate-700/60">${{m.type}}</span>
            `;
            item.addEventListener('click', () => {{
              flyToNode(m);
              searchDropdown.classList.add('hidden');
            }});
            searchDropdown.appendChild(item);
          }});
        }}
        searchDropdown.classList.remove('hidden');
      }});

      clearSearchBtn.addEventListener('click', () => {{
        searchInput.value = '';
        searchDropdown.classList.add('hidden');
        clearSearchBtn.classList.add('hidden');
      }});

      // Filters
      document.querySelectorAll('.badge-chip').forEach(chip => {{
        chip.addEventListener('click', () => {{
          const type = chip.getAttribute('data-type');
          if (activeTypes.has(type)) {{
            activeTypes.delete(type);
            chip.classList.add('inactive');
          }} else {{
            activeTypes.add(type);
            chip.classList.remove('inactive');
          }}
          updateActiveCounts();
        }});
      }});

      document.getElementById('btnToggleAllFilters').addEventListener('click', () => {{
        const allTypes = ['LAW', 'ACTION', 'REACTION', 'SECTOR', 'COMMODITY', 'ECONOMIC_INDEX'];
        const allActive = allTypes.every(t => activeTypes.has(t));
        if (allActive) {{
          activeTypes.clear();
          document.querySelectorAll('.badge-chip').forEach(c => c.classList.add('inactive'));
        }} else {{
          allTypes.forEach(t => activeTypes.add(t));
          document.querySelectorAll('.badge-chip').forEach(c => c.classList.remove('inactive'));
        }}
        updateActiveCounts();
      }});

      function updateActiveCounts() {{
        let count = 0;
        nodes.forEach(n => {{
          if (activeTypes.has(n.type)) count++;
        }});
        document.getElementById('activeNodesCount').textContent = count;
      }}

      // Layout Switcher
      const btnForce = document.getElementById('btnLayoutForce');
      const btnPipe = document.getElementById('btnLayoutPipeline');
      const btnRadial = document.getElementById('btnLayoutRadial');

      function setLayout(mode) {{
        currentLayout = mode;
        [btnForce, btnPipe, btnRadial].forEach(b => {{
          b.className = 'px-2.5 py-1.5 rounded-xl text-xs font-medium text-slate-300 hover:text-white hover:bg-slate-800 transition-all';
        }});

        if (mode === 'force') btnForce.className = 'px-2.5 py-1.5 rounded-xl text-xs font-medium bg-blue-600 text-white transition-all shadow-sm';
        if (mode === 'pipeline') btnPipe.className = 'px-2.5 py-1.5 rounded-xl text-xs font-medium bg-blue-600 text-white transition-all shadow-sm';
        if (mode === 'radial') btnRadial.className = 'px-2.5 py-1.5 rounded-xl text-xs font-medium bg-blue-600 text-white transition-all shadow-sm';
      }}

      btnForce.addEventListener('click', () => setLayout('force'));
      btnPipe.addEventListener('click', () => setLayout('pipeline'));
      btnRadial.addEventListener('click', () => setLayout('radial'));

      // Physics Toggle
      const btnTogglePhysics = document.getElementById('btnTogglePhysics');
      const iconPause = document.getElementById('iconPause');
      const iconPlay = document.getElementById('iconPlay');

      btnTogglePhysics.addEventListener('click', () => {{
        isSimRunning = !isSimRunning;
        iconPause.classList.toggle('hidden', !isSimRunning);
        iconPlay.classList.toggle('hidden', isSimRunning);
      }});

      // Export PNG
      document.getElementById('btnExportPng').addEventListener('click', () => {{
        const link = document.createElement('a');
        link.download = `powerlaw-graph-${{new Date().toISOString().slice(0, 10)}}.png`;
        link.href = canvas.toDataURL('image/png');
        link.click();
      }});

      // Export Updated JSON
      document.getElementById('btnExportJson').addEventListener('click', () => {{
        const exportObj = {{
          metadata: {{
            title: "PowerLaw Live Graph Export",
            exported_at: new Date().toISOString(),
            total_nodes: nodes.length,
            total_edges: edges.length
          }},
          nodes: nodes.map(n => ({{
            id: n.id,
            label: n.label,
            type: n.type,
            description: n.description,
            jurisdiction: n.jurisdiction,
            activation_date: n.activation_date,
            in_degree: n.in_degree,
            out_degree: n.out_degree,
            history: n.history || []
          }})),
          edges: edges.map(e => ({{
            source: e.source,
            target: e.target,
            relation: e.relation,
            weight: e.weight,
            timestamp: e.timestamp
          }}))
        }};
        const blob = new Blob([JSON.stringify(exportObj, null, 2)], {{ type: 'application/json' }});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `powerlaw_graph_updated_${{Date.now()}}.json`;
        a.click();
        URL.revokeObjectURL(url);
      }});

      // Timeline Scrubber
      const timelineSlider = document.getElementById('timelineSlider');
      const curDateLabel = document.getElementById('currentTimelineDate');
      const btnPlayTimeline = document.getElementById('btnPlayTimeline');
      const iconTPlay = document.getElementById('iconTimelinePlay');
      const iconTPause = document.getElementById('iconTimelinePause');

      function updateTimelineDate(val) {{
        timelineIndex = parseInt(val);
        const t = minDate + (timelineIndex / 100) * (maxDate - minDate);
        curDateLabel.textContent = minDate === null ? 'Unknown' : new Date(t).toISOString().slice(0, 10);
      }}

      timelineSlider.addEventListener('input', (e) => {{
        updateTimelineDate(e.target.value);
      }});

      btnPlayTimeline.addEventListener('click', () => {{
        isTimelinePlaying = !isTimelinePlaying;
        iconTPlay.classList.toggle('hidden', isTimelinePlaying);
        iconTPause.classList.toggle('hidden', !isTimelinePlaying);

        if (isTimelinePlaying) {{
          if (timelineIndex >= 100) timelineIndex = 0;
          timelineTimer = setInterval(() => {{
            timelineIndex += 1;
            if (timelineIndex > 100) {{
              timelineIndex = 100;
              clearInterval(timelineTimer);
              isTimelinePlaying = false;
              iconTPlay.classList.remove('hidden');
              iconTPause.classList.add('hidden');
            }}
            timelineSlider.value = timelineIndex;
            updateTimelineDate(timelineIndex);
          }}, 120);
        }} else {{
          clearInterval(timelineTimer);
        }}
      }});

      // Focus Neighborhood
      document.getElementById('btnFocusEgo').addEventListener('click', () => {{
        if (!selectedNode) return;
        const causal = getCausalChain(selectedNode);
        const allowed = new Set([selectedNode.id, ...causal.upstream, ...causal.downstream]);
        nodes.forEach(n => {{
          if (!allowed.has(n.id)) {{
            activeTypes.delete(n.type);
          }}
        }});
        updateActiveCounts();
      }});

      document.getElementById('btnClearIsolation').addEventListener('click', () => {{
        ['LAW', 'ACTION', 'REACTION', 'SECTOR', 'COMMODITY', 'ECONOMIC_INDEX'].forEach(t => activeTypes.add(t));
        document.querySelectorAll('.badge-chip').forEach(c => c.classList.remove('inactive'));
        updateActiveCounts();
      }});

      // ============================================================
      // ==================== LIVE UPDATE LOGIC =====================
      // ============================================================

      // Modal 1: ➕ Add Node
      const modalAddNode = document.getElementById('modalAddNode');
      document.getElementById('btnOpenAddNode').addEventListener('click', () => {{
        modalAddNode.classList.remove('hidden');
        document.getElementById('inputNodeLabel').focus();
      }});
      document.getElementById('btnCloseModalNode').addEventListener('click', () => modalAddNode.classList.add('hidden'));
      document.getElementById('btnCancelAddNode').addEventListener('click', () => modalAddNode.classList.add('hidden'));

      document.getElementById('formAddNode').addEventListener('submit', (e) => {{
        e.preventDefault();
        const label = document.getElementById('inputNodeLabel').value.trim();
        const type = document.getElementById('selectNodeType').value;
        const actDate = document.getElementById('inputNodeDate').value || null;
        const desc = document.getElementById('inputNodeDesc').value.trim();

        // Generate clean ID
        const cleanId = `${{type}}_${{label.toUpperCase().replace(/[^A-Z0-9]/g, '_').slice(0, 24)}}_${{Date.now().toString().slice(-4)}}`;
        const width = window.innerWidth;
        const height = window.innerHeight;

        const newNode = {{
          id: cleanId,
          label: label,
          type: type,
          description: desc,
          jurisdiction: 'EU',
          activation_date: actDate,
          actDate: actDate ? new Date(actDate).getTime() : null,
          in_degree: 0,
          out_degree: 0,
          total_degree: 0,
          betweenness: 0.0,
          history_count: 0,
          history: [],
          x: width / 2 + (Math.random() - 0.5) * 150,
          y: height / 2 + (Math.random() - 0.5) * 150,
          vx: 0,
          vy: 0,
          radius: 18,
          color: NODE_COLORS[type] || '#94A3B8',
          pinned: false
        }};

        nodes.push(newNode);
        nodeMap.set(newNode.id, newNode);
        activeTypes.add(type);
        document.querySelector(`.badge-chip[data-type="${{type}}"]`)?.classList.remove('inactive');

        updateKpiCounts();
        populateEdgeDropdowns();
        modalAddNode.classList.add('hidden');
        document.getElementById('formAddNode').reset();

        // Fly camera to newly added node
        flyToNode(newNode);
      }});

      // Modal 2: 🔗 Connect Edge
      const modalAddEdge = document.getElementById('modalAddEdge');
      document.getElementById('btnOpenAddEdge').addEventListener('click', () => {{
        populateEdgeDropdowns();
        if (selectedNode) {{
          document.getElementById('selectEdgeSource').value = selectedNode.id;
        }}
        modalAddEdge.classList.remove('hidden');
      }});
      document.getElementById('btnCloseModalEdge').addEventListener('click', () => modalAddEdge.classList.add('hidden'));
      document.getElementById('btnCancelAddEdge').addEventListener('click', () => modalAddEdge.classList.add('hidden'));

      document.getElementById('formAddEdge').addEventListener('submit', (e) => {{
        e.preventDefault();
        const srcId = document.getElementById('selectEdgeSource').value;
        const tgtId = document.getElementById('selectEdgeTarget').value;
        const rel = document.getElementById('selectEdgeRelation').value;
        const weight = parseFloat(document.getElementById('inputEdgeWeight').value) || 1.0;

        if (srcId === tgtId) {{
          alert('Cannot connect a node to itself.');
          return;
        }}

        const srcNode = nodeMap.get(srcId);
        const tgtNode = nodeMap.get(tgtId);

        if (!srcNode || !tgtNode) return;

        const newEdge = {{
          source: srcId,
          target: tgtId,
          relation: rel,
          weight: weight,
          timestamp: new Date().toISOString().slice(0, 10),
          sourceNode: srcNode,
          targetNode: tgtNode,
          color: EDGE_COLORS[rel] || '#64748B'
        }};

        edges.push(newEdge);

        // Update degrees
        srcNode.out_degree = (srcNode.out_degree || 0) + 1;
        srcNode.total_degree = (srcNode.total_degree || 0) + 1;
        tgtNode.in_degree = (tgtNode.in_degree || 0) + 1;
        tgtNode.total_degree = (tgtNode.total_degree || 0) + 1;

        updateKpiCounts();
        modalAddEdge.classList.add('hidden');

        // Re-inspect if selected
        if (selectedNode) selectNode(selectedNode);
      }});

      // Modal 3: ⏱️ Log Metric / State
      const modalAddState = document.getElementById('modalAddState');
      document.getElementById('btnOpenAddStateModal').addEventListener('click', () => {{
        if (!selectedNode) return;
        document.getElementById('targetStateEntityLabel').textContent = `${{selectedNode.label}} (${{selectedNode.id}})`;
        modalAddState.classList.remove('hidden');
      }});
      document.getElementById('btnCloseModalState').addEventListener('click', () => modalAddState.classList.add('hidden'));
      document.getElementById('btnCancelAddState').addEventListener('click', () => modalAddState.classList.add('hidden'));

      document.getElementById('formAddState').addEventListener('submit', (e) => {{
        e.preventDefault();
        if (!selectedNode) return;

        const date = document.getElementById('inputStateDate').value;
        const stateVal = document.getElementById('selectStateValue').value;
        const mVal = document.getElementById('inputStateVal').value;
        const unit = document.getElementById('inputStateUnit').value.trim();
        const summary = document.getElementById('inputStateSummary').value.trim();

        if (!selectedNode.history) selectedNode.history = [];
        selectedNode.history.unshift({{
          timestamp: date,
          metric_name: 'Custom State Update',
          metric_value: mVal !== '' ? parseFloat(mVal) : null,
          unit: unit,
          state_value: stateVal,
          event_summary: summary
        }});

        updateKpiCounts();
        modalAddState.classList.add('hidden');
        document.getElementById('formAddState').reset();

        // Refresh drawer
        selectNode(selectedNode);
      }});

    }})();
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    export_graph_visualization()
