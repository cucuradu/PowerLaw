"""Graph: everything the store holds and how it is connected. Click a zone to open it, a node or an arrow for details."""
from pathlib import Path

import pandas as pd
import streamlit as st

from src.config import get_settings
from src.storage.graph_view import (drivers, edge_details, focus_view, load_graph_view, map_view, node_details, outcomes,
                                     schema_details, schema_view, weight_text)
from src.ui.graph_component import network

SNAPSHOT = Path(get_settings().data_dir) / "reports" / "graph-view-latest.json"


@st.cache_data(show_spinner=False)
def _view(mtime: float):  # reloads when the weekly update writes a new snapshot
    return load_graph_view()


view = _view(SNAPSHOT.stat().st_mtime if SNAPSHOT.exists() else 0.0)
st.title("🕸️ Store graph")
if not view:
    st.info("No graph snapshot yet: run `python -m scripts.ops.build_graph_view`.")
    st.stop()
inv = view["inventory"]
st.caption(f"{inv['nodes']} nodes and {inv['relations']} relations from the store (snapshot {view['generated_at'][:10]}), "
           f"backed by {inv['documents']} stored documents. Cross-border trade flows ({inv['cross_border_flows_not_drawn']:,}) "
           "are stored but not drawn.")

state = st.session_state
state.setdefault("open_zones", set())
state.setdefault("selected", None)
state.setdefault("graph_mode", "Architecture")
by_id = {n["id"]: n for n in view["nodes"]}
if "focus" in st.query_params and st.query_params["focus"] in by_id:  # opened from another page
    state.update(go_to=st.query_params["focus"], selected=("node", st.query_params["focus"]))
    del st.query_params["focus"]
if "go_to" in state:  # set by a button below; widgets can only be changed before they are drawn
    state.graph_mode, state.focus_select = "Relations", state.pop("go_to")
if "open_zone" in state:  # from the architecture view: show that zone on the map
    state.graph_mode, state.open_zones = "Store map", state.open_zones | {state.pop("open_zone")}


def go_to(node_id: str) -> None:
    state.update(go_to=node_id, selected=("node", node_id))
    st.rerun()

# --- Controls ----------------------------------------------------------------------------------------------------
c1, c2, c3 = st.columns([1.3, 1.6, 1.4])
mode = c1.segmented_control("View", ["Architecture", "Store map", "Relations"], key="graph_mode",
                            help="Architecture: the kinds of nodes and relations, with counts. Store map: the store by zone; "
                                 "click a zone to open it. Relations: what surrounds one node, or one cost chain.") or "Architecture"
years = view["years"]
year = c2.select_slider("Input-output year", years, value=years[-1],
                        help="Eurostat publishes Germany's input-output tables in this classification for 2010-2022; "
                             "older tables use a different classification and are not stored.")
min_share = c3.slider("Hide flows below (% of construction output)", 0.0, 3.0, 0.5, 0.1,
                      help="Input-output flows smaller than this are hidden; set 0 to see every supplier and buyer.") / 100

if mode == "Relations":
    f1, f2, f3 = st.columns([2.2, 0.8, 1.6])
    options = sorted(by_id, key=lambda nid: (view["zones"][by_id[nid]["zone"]]["label"], by_id[nid]["label"]))
    state.setdefault("focus_select", "sec_F")
    focus = f1.selectbox("Around", options, key="focus_select",
                         format_func=lambda nid: f"{by_id[nid]['label']}  ·  {view['zones'][by_id[nid]['zone']]['label']}")
    depth = f2.segmented_control("Steps", [1, 2], default=1)
    chains = {c["label"]: c["id"] for c in view["chains"]}
    chain_label = f3.selectbox("Or show a cost chain", ["—"] + list(chains))
    chain = chains.get(chain_label)
    nodes, edges = focus_view(view, focus, depth or 1, year, min_share, chain=chain)
    if chain:
        st.caption(next(c["why"] for c in view["chains"] if c["id"] == chain))
elif mode == "Architecture":
    nodes, edges = schema_view(view, year, min_share)
else:
    nodes, edges = map_view(view, state.open_zones, year, min_share)

# --- Graph and details -------------------------------------------------------------------------------------------
left, right = st.columns([2.6, 1.6], gap="medium")
with left:
    clicked = network(nodes, edges, height=660, key=f"graph-{mode}")
    if mode == "Relations" and chain:
        st.markdown("<div style='font-size:0.82rem;color:#374151'><b>Chain steps</b> &nbsp;"
                    "<span style='color:#16A34A'>━ tested: supported</span> &nbsp; <span style='color:#F97316'>━ data stored, not tested</span>"
                    " &nbsp; <span style='color:#9CA3AF'>╍ not supported or needs data</span> &nbsp; (hollow nodes: no data yet)</div>",
                    unsafe_allow_html=True)
    zone_chips = " ".join(f"<span style='display:inline-flex;align-items:center;margin:2px 10px 2px 0'>"
                          f"<span style='width:11px;height:11px;border-radius:50%;background:{z['color']};margin-right:5px'></span>"
                          f"{z['label']}</span>" for z in view["zones"].values())
    edge_chips = " ".join(f"<span style='display:inline-flex;align-items:center;margin:2px 10px 2px 0'>"
                          f"<span style='width:18px;height:0;border-top:3px {'dashed' if t['dashes'] else 'solid'} {t['color']};margin-right:5px'></span>"
                          f"{t['label']}</span>" for t in view["edge_types"].values())
    st.markdown(f"<div style='font-size:0.82rem;color:#374151'><b>Nodes</b> &nbsp;{zone_chips}<br><b>Arrows</b> &nbsp;{edge_chips}</div>",
                unsafe_allow_html=True)
    st.caption("Faded nodes are rules, value added, policy numbers and EU-panel groups. Arrow width shows the weight "
               "(input-output share, index weight, tested pass-through). Scroll to zoom, drag to move.")

if clicked.node:
    if clicked.node.startswith("kind:"):
        state.selected = ("kind", clicked.node)
    elif clicked.node.startswith("zone:"):
        state.open_zones ^= {clicked.node[5:]}
        state.selected = ("zone", clicked.node[5:])
        st.rerun()
    else:
        state.selected = ("node", clicked.node)
elif clicked.edge:
    state.selected = ("schema" if clicked.edge.startswith("schema:") else "edge", clicked.edge)

with right:
    if mode == "Store map" and state.open_zones and st.button("Close all zones", width="stretch"):
        state.open_zones = set()
        st.rerun()
    kind, ref = state.selected or (None, None)
    if (kind in ("kind", "schema")) != (mode == "Architecture"):  # selection from another view
        kind, ref = None, None
    if mode == "Relations" and kind in (None, "zone"):  # show the node the view is centred on
        kind, ref = "node", state.get("focus_select", "sec_F")
    if kind == "node" and ref in by_id:
        n = node_details(view, ref, year, min_share)
        zone = view["zones"][n["zone"]]
        st.markdown(f"<span style='color:{zone['color']};font-weight:700;font-size:0.8rem'>● {zone['label'].upper()} · {n['kind']}</span>",
                    unsafe_allow_html=True)
        st.subheader(n["label"])
        if n.get("description"):
            st.write(n["description"])
        if n["facts"]:
            st.markdown("\n".join(f"- **{k}:** {v}" for k, v in n["facts"].items()))
        b1, b2 = st.columns(2)
        if b1.button("Relations around it", width="stretch"):
            go_to(ref)
        law = ref if n["kind"] == "law" else next((r["other"] for r in n["relations"] if r["type"] == "contains rule"), None)
        if law and b2.button("Open in Laws", width="stretch"):
            st.switch_page("views/laws.py", query_params={"law": law})
        per_pct = lambda e: "not measured" if e is None else f"{e:.3g}% per 1%"
        t_drivers, t_outcomes, t_relations = st.tabs(["What drives it", "What it moves", f"Relations ({len(n['relations'])})"])
        with t_drivers:
            rows = drivers(view, ref, year, min_share)
            if rows:
                st.dataframe(pd.DataFrame([{"Driver": r["label"], "Strength": per_pct(r["strength"]), "Path": r["path"], "How": r["how"]}
                                           for r in rows]), hide_index=True, width="stretch", height=min(420, 36 * (len(rows) + 1)))
                st.caption("Strength: how much this node moves when the driver moves 1%, along the strongest path (input-output "
                           "cost shares, index weights and tested pass-throughs multiplied). 'Not measured': the link is "
                           "known (law text, accounting, a hypothesis) but its size is not.")
            else:
                st.caption("Nothing in the store drives this node.")
        with t_outcomes:
            o = outcomes(view, ref, year, min_share)
            m = o["moves"]
            if m:
                u = "%" if m["unit"] == "%" else " pts"
                st.markdown(f"**Its history** ({m.get('series', 'own series')}, twelve-month changes since {m['since']}): "
                            f"calm year {m['p10']:+.1f}{u}, typical {m['p50']:+.1f}{u}, bad year {m['p90']:+.1f}{u}, "
                            f"record {m['high']:+.1f}{u} (fall {m['low']:+.1f}{u}); latest {m['latest']:+.1f}{u} ({m['latest_month']}).")
            if o["rows"]:
                st.dataframe(pd.DataFrame([{"Outcome": r["label"], "Effect": per_pct(r["effect"]),
                                            **({"Calm": f"{r['scenarios']['p10']:+.3g}%", "Typical": f"{r['scenarios']['p50']:+.3g}%",
                                                "Bad year": f"{r['scenarios']['p90']:+.3g}%", "Record": f"{r['scenarios']['high']:+.3g}%"}
                                               if r.get("scenarios") else {}),
                                            "Strongest path": r["path"]} for r in o["rows"]]),
                             hide_index=True, width="stretch", height=min(420, 36 * (len(o["rows"]) + 1)))
                st.caption("Effect: summed over every measured path (a material reaches the index through several trades). "
                           "Calm / typical / bad / record: the effect if this node moves as in its 10th, 50th and 90th "
                           "percentile and record twelve months, all else equal. Construction (the price index) comes first.")
            else:
                st.caption("This node does not move anything else in the store.")
        with t_relations:
            if n["relations"]:
                st.dataframe(pd.DataFrame([{"With": f"{r['direction']} {r['other_label']}", "Relation": r["type"],
                                            "Weight": r["weight_text"],
                                            "Year": r["year"] or ""} for r in n["relations"][:60]]),
                             hide_index=True, width="stretch", height=min(420, 36 * (len(n["relations"][:60]) + 1)))
    elif kind == "edge":
        d = edge_details(view, ref, year, min_share)
        if d["bundle"]:
            st.markdown(f"**{d['count']} × {d['type']['label']}**")
            st.caption(d["type"]["about"])
            st.markdown("\n".join(f"- {item}" for item in d["items"][:40]) + ("\n- …" if d["count"] > 40 else ""))
            st.caption("Open a zone to see these as single arrows.")
        else:
            st.markdown(f"<span style='color:{d['type_info']['color']};font-weight:700;font-size:0.8rem'>"
                        f"━ {d['type_info']['label'].upper()}</span>", unsafe_allow_html=True)
            st.subheader(f"{d['from_label']} → {d['to_label']}")
            st.caption(d["type_info"]["about"])
            facts = {"Weight": weight_text(view, d) or None,
                     "Year": d.get("year"), "Value": f"{d['value_mio_eur']:,.0f} million EUR" if d.get("value_mio_eur") else None,
                     "Note": d.get("note")}
            st.markdown("\n".join(f"- **{k}:** {v}" for k, v in facts.items() if v))
            ev = d.get("evidence")
            if ev and ev.get("quote"):
                st.markdown("**Evidence in the store**")
                st.markdown("> " + ev["quote"].replace("\n", " ")[:500])
            if ev and ev.get("url"):
                st.caption(f"Source: {ev.get('publisher') or ''} {ev['url']}")
            b1, b2 = st.columns(2)
            if b1.button("Go to start", width="stretch"):
                state.update(selected=("node", d["source"]))
                st.rerun()
            if b2.button("Go to end", width="stretch"):
                state.update(selected=("node", d["target"]))
                st.rerun()
    elif kind in ("kind", "schema"):
        d = schema_details(view, ref, year, min_share)
        if d["what"] == "kind":
            zone = view["zones"][d["zone"]]
            st.markdown(f"<span style='color:{zone['color']};font-weight:700;font-size:0.8rem'>● {zone['label'].upper()}</span>",
                        unsafe_allow_html=True)
            st.subheader(f"{d['count']} × {d['kind']}")
            st.write(d["about"])
            if st.button("Show them on the store map", width="stretch"):
                state.open_zone = d["zone"]
                st.rerun()
            pick = st.selectbox("Open one in Relations", [m[0] for m in d["members"]], index=None,
                                format_func=dict(d["members"]).get, placeholder="Most connected first")
            if pick:
                go_to(pick)
            st.markdown("\n".join(f"- {label}" for _, label in d["members"][:25]) + ("\n- …" if d["count"] > 25 else ""))
        else:
            st.markdown(f"<span style='color:{d['type']['color']};font-weight:700;font-size:0.8rem'>━ {d['type']['label'].upper()}</span>",
                        unsafe_allow_html=True)
            st.subheader(f"{d['count']} relations")
            st.write(d["type"]["about"])
            st.markdown("\n".join(f"- {item}" for item in d["items"][:40]) + ("\n- …" if d["count"] > 40 else ""))
    elif kind == "zone":
        z = view["zones"][ref]
        st.subheader(z["label"])
        st.write(z["about"])
        st.caption("Click a node inside the zone for details, or the zone bubble again to close it.")
    elif mode == "Architecture":
        st.markdown("**How the graph is built**")
        st.markdown("- Each **box** is a kind of node; the number is how many the store holds.\n"
                    "- Each **arrow** is a kind of relation between them, with how many exist (input-output flows: chosen year and threshold).\n"
                    "- Click a box or an arrow for its meaning and the full list.\n"
                    f"- **Behind the graph:** {inv['series']} time series hold the observations, and {inv['documents']} source "
                    "documents hold the quotes every law, rule and relation is taken from.")
    else:
        st.markdown("**How to read it**")
        st.markdown("- Each **bubble** is a zone of the store; the number is how many nodes it holds. Click to open it.\n"
                    "- Click a **node** to see what it is and everything it connects to.\n"
                    "- Click an **arrow** to see the relation, its weight and the evidence quoted from the source.\n"
                    "- **Relations** view: pick any node, or a cost chain, to see its neighbourhood.")
