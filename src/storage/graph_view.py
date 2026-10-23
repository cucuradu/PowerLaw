"""Graph page logic: turn the store snapshot (scripts.ops.build_graph_view) into drawable views and details.

Three views:
  schema   the architecture: one box per kind of node, one arrow per kind of relation, with counts.
  map      the store by zone; a closed zone is one bubble (with its size), an open zone shows its members.
           Relations between closed zones are bundled with a count.
  focus    everything within one or two steps of a chosen node (rings by distance), or one cost chain (left to right).
Input-output relations exist per year: only the chosen year is drawn, and only shares at or above `min_share`.
"""
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import get_settings

ZONE_CENTERS = {"construction": (0, 0), "laws": (-1250, -150), "economy": (0, -1150), "materials": (1250, -150),
                "data": (0, 1100)}
WEIGHTED = {"supplies", "buys", "part_of", "tested_link"}
NEIGHBOUR_CAP = 30  # per node in the focus view, strongest first


def load_graph_view(folder: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = (folder or Path(get_settings().data_dir) / "reports") / "graph-view-latest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def visible_edges(view: Dict[str, Any], year: Optional[int], min_share: float) -> List[Dict[str, Any]]:
    """Edges of the chosen input-output year (others have no year) above the share threshold."""
    out = []
    for e in view["edges"]:
        if e.get("year") is not None and e["year"] != year:
            continue
        if e["type"] in ("supplies", "buys") and (e.get("weight") or 0) < min_share:
            continue
        out.append(e)
    return out


def short(label: str, limit: int = 32) -> str:
    """Graph label: long names are cut (the tooltip and the details panel show them in full)."""
    return label if len(label) <= limit else label[:limit - 1].rstrip(" ,;") + "…"


def _hex_alpha(color: str, alpha: float) -> str:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def _node_style(view: Dict[str, Any], n: Dict[str, Any], focus: bool = False) -> Dict[str, Any]:
    color = view["zones"][n["zone"]]["color"]
    size = {"law": 20, "rule": 8, "EU panel": 12}.get(n["kind"], 8 + 4 * math.log1p(n["degree"]))
    if n["id"] == "sec_F":
        size = 34
    if n["kind"] == "planned":  # no data yet: hollow grey
        return {"color": {"background": "#FFFFFF", "border": "#9CA3AF", "highlight": {"background": "#F3F4F6", "border": "#111827"},
                          "hover": {"background": "#F3F4F6", "border": "#111827"}},
                "size": 9 * (1.6 if focus else 1), "borderWidth": 2, "shapeProperties": {"borderDashes": [3, 3]}, "font": {"size": 11, "color": "#6B7280"}}
    background = _hex_alpha(color, 0.45) if n["kind"] in ("rule", "EU panel", "value added", "policy number") else color
    return {"color": {"background": background, "border": color, "highlight": {"background": color, "border": "#111827"},
                      "hover": {"background": color, "border": "#111827"}},
            "size": size * (1.6 if focus else 1), "borderWidth": 4 if focus else 1.5,
            "font": {"size": 15 if focus else 13 if n["kind"] in ("law", "sector", "trade", "commodity") or n["id"] == "sec_F" else 11}}


CHAIN_COLORS = {"supported": "#16A34A", "not supported": "#9CA3AF", "not tested": "#F97316", "needs data": "#9CA3AF"}


def _edge_style(view: Dict[str, Any], e: Dict[str, Any], label: bool) -> Dict[str, Any]:
    t = view["edge_types"][e["type"]]
    if e["type"] == "chain" and label:  # chain view: one clear arrow per step, coloured by its test status
        mechanism, _, status = (e.get("note") or "").partition("; ")
        color = next((c for k, c in CHAIN_COLORS.items() if k in status), "#F97316")
        return {"color": {"color": color, "highlight": color, "hover": color}, "width": 2.5,
                "dashes": "needs data" in status or "not supported" in status, "label": short(mechanism, 24),
                "font": {"size": 11, "color": "#374151"}}
    weight = e.get("weight")
    width = 1 + min(12, 14 * math.sqrt(weight)) if e["type"] in WEIGHTED and weight else 1.3
    text = weight_text(view, e).split(",")[0] if label else None
    return {"color": {"color": _hex_alpha(t["color"], 0.55), "highlight": t["color"], "hover": t["color"]},
            "width": width, "dashes": t["dashes"], **({"label": text} if text else {})}


def weight_text(view: Dict[str, Any], e: Dict[str, Any]) -> str:
    """The weight as people read it. Index weights are stored as a share of the whole index; a trade also gets its
    share of its group (shell or finishing work), which is what the arrow to the group means."""
    w = e.get("weight")
    if w is None or e["type"] not in WEIGHTED:
        return ""
    if e["type"] == "tested_link":
        return f"{w:.2f}"
    if e["type"] == "part_of":
        group = next((x["weight"] for x in view["edges"] if x["type"] == "part_of" and x["source"] == e["target"] and x.get("weight")), None)
        return f"{100 * w / group:.1f}% of the group, {100 * w:.2f}% of the index" if group else f"{100 * w:.1f}% of the index"
    return f"{100 * w:.1f}%"


def node_tip(view: Dict[str, Any], n: Dict[str, Any]) -> str:
    zone = view["zones"][n["zone"]]["label"]
    text = f"{n['label']}\n{n['kind']} · {zone}"
    if n.get("description"):
        text += "\n\n" + (n["description"][:220] + ("…" if len(n["description"]) > 220 else ""))
    return text + "\n\nClick for details"


def edge_text(view: Dict[str, Any], e: Dict[str, Any], labels: Dict[str, str]) -> str:
    t = view["edge_types"][e["type"]]["label"]
    text = f"{labels.get(e['source'], e['source'])} → {labels.get(e['target'], e['target'])}\n{t}"
    if weight_text(view, e):
        text += f": {weight_text(view, e)}"
    if e.get("year"):
        text += f" ({e['year']})"
    if e.get("note"):
        text += f"\n{e['note']}"
    return text


def map_view(view: Dict[str, Any], open_zones: set, year: Optional[int], min_share: float) -> Tuple[list, list]:
    """Nodes and edges for the zone map; closed zones are bubbles 'zone:<id>', bundled edges 'bundle:...'."""
    by_id = {n["id"]: n for n in view["nodes"]}
    labels = {nid: n["label"] for nid, n in by_id.items()}
    members = defaultdict(list)
    for n in view["nodes"]:
        members[n["zone"]].append(n)
    nodes = []
    # Each zone sits in its own direction from construction (the centre), just outside whatever is open next to it,
    # so the view stays zoomed in enough to read labels.
    radius = {z: _cluster_radius(len(members[z])) if z in open_zones else 70 for z in view["zones"]}
    centre = radius["construction"]
    for zone, info in view["zones"].items():
        dx, dy = ZONE_CENTERS[zone]
        norm = math.hypot(dx, dy) or 1
        distance = 0 if zone == "construction" else centre + radius[zone] + 160
        cx, cy = dx / norm * distance, dy / norm * distance
        if zone in open_zones:
            nodes += [{"id": n["id"], "label": short(n["label"], 26) if n["kind"] != "rule" else "", "x": x, "y": y,
                       "tip": node_tip(view, n), **_node_style(view, n)}
                      for n, (x, y) in zip(members[zone], _cluster_positions(members[zone], cx, cy))]
        else:
            kinds = defaultdict(int)
            for n in members[zone]:
                kinds[n["kind"]] += 1
            nodes.append({"id": f"zone:{zone}", "label": f"{info['label']}\n{len(members[zone])}", "x": cx, "y": cy,
                          "size": 34 + 3 * math.sqrt(len(members[zone])), "shape": "dot",
                          "color": {"background": _hex_alpha(info["color"], 0.18), "border": info["color"],
                                    "highlight": {"background": _hex_alpha(info["color"], 0.35), "border": info["color"]},
                                    "hover": {"background": _hex_alpha(info["color"], 0.3), "border": info["color"]}},
                          "borderWidth": 3, "font": {"size": 22, "color": "#111827"},
                          "tip": info["about"] + "\n\n" + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
                                 + "\n\nClick to open this zone"})
    endpoint = lambda nid: nid if by_id[nid]["zone"] in open_zones else f"zone:{by_id[nid]['zone']}"
    edges, bundles = [], defaultdict(list)
    for e in visible_edges(view, year, min_share):
        s, t = endpoint(e["source"]), endpoint(e["target"])
        if s == t:
            continue
        if s.startswith("zone:") or t.startswith("zone:"):
            bundles[(s, t, e["type"])].append(e)
        else:
            edges.append({"id": e["id"], "from": s, "to": t, "tip": edge_text(view, e, labels), **_edge_style(view, e, label=False)})
    for (s, t, kind), group in bundles.items():
        style = view["edge_types"][kind]
        edges.append({"id": f"bundle:{s}|{t}|{kind}", "from": s, "to": t, "label": str(len(group)) if len(group) > 1 else None,
                      "width": 1 + 1.6 * math.log2(1 + len(group)), "dashes": style["dashes"],
                      "color": {"color": _hex_alpha(style["color"], 0.5), "highlight": style["color"], "hover": style["color"]},
                      "tip": f"{len(group)} × {style['label']}\nClick for the list"})
    return nodes, [{k: v for k, v in e.items() if v is not None} for e in edges]


def _cluster_radius(count: int) -> float:
    """Radius of the rings _cluster_positions needs for `count` members."""
    ring, placed = 0, 0
    while placed < count:
        placed += 1 if ring == 0 else max(6, int(2 * math.pi * 110 * ring / 70))
        ring += 1
    return 110 * max(ring - 1, 0) + 40


def _cluster_positions(nodes: List[Dict[str, Any]], cx: float, cy: float) -> List[Tuple[float, float]]:
    """Members of an open zone on rings around the zone centre: most connected first, laws before their rules."""
    order = sorted(range(len(nodes)), key=lambda i: (nodes[i]["kind"] == "rule", -nodes[i]["degree"], nodes[i]["label"]))
    positions, ring, placed = [None] * len(nodes), 0, 0
    while placed < len(order):
        radius = 0 if ring == 0 else 110 * ring
        capacity = 1 if ring == 0 else max(6, int(2 * math.pi * radius / 70))
        for k in range(min(capacity, len(order) - placed)):
            angle = 2 * math.pi * k / capacity + ring * 0.4
            positions[order[placed]] = (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
            placed += 1
        ring += 1
    return positions


def focus_view(view: Dict[str, Any], focus: str, depth: int, year: Optional[int], min_share: float,
               chain: Optional[str] = None) -> Tuple[list, list]:
    """Rings around `focus` (distance 1, 2), strongest NEIGHBOUR_CAP neighbours per node; or one cost chain."""
    by_id = {n["id"]: n for n in view["nodes"]}
    labels = {nid: n["label"] for nid, n in by_id.items()}
    candidates = visible_edges(view, year, min_share)
    if chain:
        chosen = [e for e in candidates if e.get("chain") == chain]
        positions = _layers([e for e in chosen])
    else:
        adjacency = defaultdict(list)
        for e in candidates:
            adjacency[e["source"]].append(e)
            adjacency[e["target"]].append(e)
        level, chosen, queue = {focus: 0}, {}, deque([focus])
        while queue:
            nid = queue.popleft()
            if level[nid] >= depth:
                continue
            strongest = sorted(adjacency[nid], key=lambda e: -(e.get("weight") or 0))[:NEIGHBOUR_CAP]
            for e in strongest:
                other = e["target"] if e["source"] == nid else e["source"]
                chosen[e["id"]] = e
                if other not in level:
                    level[other] = level[nid] + 1
                    queue.append(other)
        chosen = [e for e in chosen.values() if e["source"] in level and e["target"] in level]
        incoming = {e["source"] for e in chosen if e["target"] == focus}
        positions = _rings(level, by_id, incoming)
    nodes = [{"id": nid, "label": short(by_id[nid]["label"]), "x": x, "y": y, "tip": node_tip(view, by_id[nid]),
              **_node_style(view, by_id[nid], focus=nid == focus and not chain)} for nid, (x, y) in positions.items()]
    edges = [{"id": e["id"], "from": e["source"], "to": e["target"], "tip": edge_text(view, e, labels),
              **_edge_style(view, e, label=True)} for e in chosen]
    return nodes, edges


def _rings(level: Dict[str, int], by_id: Dict[str, Any], incoming: set = frozenset()) -> Dict[str, Tuple[float, float]]:
    """Rings by distance; on the first ring, what flows into the focus sits on the left, what it feeds on the right."""
    rings = defaultdict(list)
    for nid, d in level.items():
        rings[d].append(nid)
    zone_order = ["laws", "economy", "construction", "materials", "data"]
    out = {}
    for d, members in rings.items():
        members.sort(key=lambda nid: (nid not in incoming, zone_order.index(by_id[nid]["zone"]), by_id[nid]["label"]))
        radius = 0 if d == 0 else 300 * d + 10 * len(members) / max(d, 1)
        for k, nid in enumerate(members):
            angle = math.pi / 2 + 2 * math.pi * (k + 0.5) / max(1, len(members))  # start at the top, go left first
            out[nid] = (radius * math.cos(angle), -radius * math.sin(angle))
    return out


def _layers(edges: List[Dict[str, Any]]) -> Dict[str, Tuple[float, float]]:
    """Left-to-right layers for a chain: each node one step right of its furthest predecessor."""
    preds = defaultdict(set)
    nodes = set()
    for e in edges:
        preds[e["target"]].add(e["source"])
        nodes |= {e["source"], e["target"]}
    layer = {}

    def depth(nid, seen=()):
        if nid not in layer:
            layer[nid] = 0 if nid in seen else 1 + max((depth(p, seen + (nid,)) for p in preds[nid]), default=-1)
        return layer[nid]
    for nid in nodes:
        depth(nid)
    columns = defaultdict(list)
    for nid in sorted(nodes):
        columns[layer[nid]].append(nid)
    out = {}
    for col in sorted(columns):  # order each column by where its predecessors sit, to cut crossings
        ids = sorted(columns[col], key=lambda nid: (sum(out[p][1] for p in preds[nid] if p in out) / max(1, len([p for p in preds[nid] if p in out])), nid))
        for i, nid in enumerate(ids):
            out[nid] = (340 * col, 110 * (i - (len(ids) - 1) / 2))
    return out


def node_details(view: Dict[str, Any], nid: str, year: Optional[int], min_share: float) -> Dict[str, Any]:
    """The node and its relations (strongest first) for the details panel."""
    by_id = {n["id"]: n for n in view["nodes"]}
    labels = {k: n["label"] for k, n in by_id.items()}
    relations = []
    for e in visible_edges(view, year, min_share):
        if nid in (e["source"], e["target"]):
            other = e["target"] if e["source"] == nid else e["source"]
            relations.append({"edge": e["id"], "direction": "→" if e["source"] == nid else "←", "other": other,
                              "other_label": labels.get(other, other), "type": view["edge_types"][e["type"]]["label"],
                              "weight": e.get("weight") if e["type"] in WEIGHTED else None, "weight_text": weight_text(view, e),
                              "year": e.get("year")})
    relations.sort(key=lambda r: (-(r["weight"] or 0), r["type"], r["other_label"]))
    return {**by_id[nid], "relations": relations}


def edge_details(view: Dict[str, Any], eid: str, year: Optional[int], min_share: float) -> Dict[str, Any]:
    """One stored relation, or the list behind a bundled edge between zones."""
    by_id = {n["id"]: n for n in view["nodes"]}
    labels = {k: n["label"] for k, n in by_id.items()}
    if eid.startswith("bundle:"):
        s, t, kind = eid[len("bundle:"):].split("|")
        zone = lambda nid: f"zone:{by_id[nid]['zone']}"
        match = lambda end, nid: end == nid or end == zone(nid)
        group = [e for e in visible_edges(view, year, min_share) if e["type"] == kind
                 and match(s, e["source"]) and match(t, e["target"])]
        return {"bundle": True, "type": view["edge_types"][kind], "count": len(group),
                "items": [edge_text(view, e, labels).split("\n")[0] + (f" ({weight_text(view, e)})" if weight_text(view, e) else "")
                          for e in sorted(group, key=lambda e: -(e.get("weight") or 0))]}
    e = next(x for x in view["edges"] if x["id"] == eid)
    return {"bundle": False, **e, "type_info": view["edge_types"][e["type"]], "from_label": labels.get(e["source"]),
            "to_label": labels.get(e["target"])}


KIND_ABOUT = {
    "law": "An EU or German law, with its official text stored as a source document.",
    "rule": "An If / Then requirement extracted from one article of a law, with the article quoted as evidence.",
    "sector": "An industry (NACE classification) from the input-output tables: who supplies whom, in million EUR.",
    "trade": "A construction trade (e.g. concrete work, roofing) with its own Destatis price index.",
    "commodity": "A material or energy carrier (e.g. steel, cement, gas) tracked by producer or import prices.",
    "series": "A stored time series (price index, cost index, rate); the observations sit in the series table.",
    "planned": "A stage of a cost chain the store does not hold data for yet.",
    "EU panel": "The same indicator for the EU countries, grouped into one node per indicator.",
    "value added": "Wages, profits and taxes: the part of output that is not bought from other sectors.",
    "policy number": "A number a law sets (e.g. the CO2 price path), quoted from the law's text.",
}
KIND_PLURAL = {"law": "Laws", "rule": "Rules", "sector": "Sectors", "trade": "Trades", "commodity": "Commodities",
               "series": "Series", "planned": "Planned stages", "EU panel": "EU panels", "value added": "Value added",
               "policy number": "Policy numbers"}


def _tint(color: str, amount: float) -> str:
    """Opaque light version of `color`, so arrows do not show through a box."""
    return "#" + "".join(f"{round(255 - (255 - int(color[i:i + 2], 16)) * amount):02X}" for i in (1, 3, 5))


def _kind_key(n: Dict[str, Any]) -> str:
    return f"kind:{n['zone']}/{n['kind']}"


def schema_view(view: Dict[str, Any], year: Optional[int], min_share: float) -> Tuple[list, list]:
    """The graph's architecture: one node per kind of node (per zone), one arrow per kind of relation, with counts."""
    groups = defaultdict(list)
    for n in view["nodes"]:
        groups[_kind_key(n)].append(n)
    by_zone = defaultdict(list)
    for key, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        by_zone[members[0]["zone"]].append(key)
    nodes = []
    for zone, keys in by_zone.items():
        cx, cy = (0.3 * c for c in ZONE_CENTERS[zone])
        info = view["zones"][zone]
        side = zone in ("laws", "materials")  # left and right zones stack vertically, the others side by side
        for i, key in enumerate(keys):
            offset = i - (len(keys) - 1) / 2
            kind, count = groups[key][0]["kind"], len(groups[key])
            name = KIND_PLURAL.get(kind, kind)
            if kind == "sector":
                name = "Construction sectors" if zone == "construction" else "Other sectors"
            nodes.append({"id": key, "label": f"{name}\n{count}", "x": cx + (0 if side else 250 * offset), "y": cy + (130 * offset if side else 0),
                          "shape": "box", "margin": 12, "borderWidth": 2,
                          "color": {"background": _tint(info["color"], 0.14), "border": info["color"],
                                    "highlight": {"background": _tint(info["color"], 0.3), "border": "#111827"},
                                    "hover": {"background": _tint(info["color"], 0.24), "border": info["color"]}},
                          "font": {"size": 22, "color": "#111827"},
                          "tip": f"{name}: {count}\n{KIND_ABOUT.get(kind, '')}\n\nClick for examples"})
    key_of = {n["id"]: _kind_key(n) for n in view["nodes"]}
    counts = defaultdict(int)
    for e in visible_edges(view, year, min_share):
        counts[(key_of[e["source"]], e["type"], key_of[e["target"]])] += 1
    edges = []
    for (s, kind, t), count in counts.items():
        style = view["edge_types"][kind]
        edges.append({"id": f"schema:{s}|{kind}|{t}", "from": s, "to": t, "label": str(count),
                      "width": 1 + 1.4 * math.log2(1 + count), "dashes": style["dashes"],
                      "font": {"size": 15, "color": "#374151", "strokeWidth": 4, "strokeColor": "#FFFFFF"},
                      "color": {"color": _hex_alpha(style["color"], 0.6), "highlight": style["color"], "hover": style["color"]},
                      "tip": f"{count} × {style['label']}\n{style['about']}\n\nClick for the list"})
    return nodes, edges


def schema_details(view: Dict[str, Any], ref: str, year: Optional[int], min_share: float) -> Dict[str, Any]:
    """A kind of node (its members, most connected first) or a kind of relation (its arrows, strongest first)."""
    labels = {n["id"]: n["label"] for n in view["nodes"]}
    if ref.startswith("kind:"):
        members = sorted((n for n in view["nodes"] if _kind_key(n) == ref), key=lambda n: -n["degree"])
        kind = members[0]["kind"]
        return {"what": "kind", "zone": members[0]["zone"], "kind": kind, "about": KIND_ABOUT.get(kind, ""),
                "count": len(members), "members": [(n["id"], n["label"]) for n in members]}
    s, kind, t = ref[len("schema:"):].split("|")
    key_of = {n["id"]: _kind_key(n) for n in view["nodes"]}
    group = sorted((e for e in visible_edges(view, year, min_share)
                    if e["type"] == kind and key_of[e["source"]] == s and key_of[e["target"]] == t),
                   key=lambda e: -(e.get("weight") or 0))
    return {"what": "relation", "type": view["edge_types"][kind], "count": len(group),
            "items": [edge_text(view, e, labels).split("\n")[0]
                      + (f" ({weight_text(view, e)})" if weight_text(view, e) else "")
                      for e in group]}
