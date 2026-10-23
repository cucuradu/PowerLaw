"""Laws: what each law in the store is, how it can reach construction prices, and what the store holds about it."""
import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src.config import REPO_ROOT, get_settings
from src.storage.graph_view import load_graph_view

SNAPSHOT = Path(get_settings().data_dir) / "reports" / "graph-view-latest.json"
RELEVANCE = {"high": "●●●", "medium": "●●○", "low": "●○○"}


@st.cache_data(show_spinner=False)
def _data(mtime: float, profiles_mtime: float):
    profiles = json.loads((REPO_ROOT / "config" / "law_profiles.json").read_text(encoding="utf-8"))
    return profiles, load_graph_view()


profiles_file = REPO_ROOT / "config" / "law_profiles.json"
profiles, view = _data(SNAPSHOT.stat().st_mtime if SNAPSHOT.exists() else 0.0, profiles_file.stat().st_mtime)
laws = profiles["laws"]
nodes = {n["id"]: n for n in (view or {}).get("nodes", [])}
edges = (view or {}).get("edges", [])
st.title("📜 Laws")
st.caption("Every law in the store, what it does, how it can reach construction prices, and what the store holds about it: "
           "rules extracted from its text, the sectors and goods it regulates, numbers it sets and laws it implements.")

rules_of = {lid: [e["target"] for e in edges if e["source"] == lid and e["type"] == "contains"] for lid in laws}
table = pd.DataFrame([{"id": lid, "Law": p["short"], "Title": p["title"], "Type": p["type"], "Adopted": p["adopted"],
                       "Channel": p["channel"], "Relevance": RELEVANCE[p["relevance"]], "Rules": len(rules_of[lid]),
                       "_rank": ("high", "medium", "low").index(p["relevance"])} for lid, p in laws.items()])
table = table.sort_values(["_rank", "Adopted"], ascending=[True, False])

f1, f2, f3 = st.columns([2, 1.2, 1.6])
search = f1.text_input("Search", placeholder="name, title or topic")
where = f2.segmented_control("Jurisdiction", ["EU", "Germany"], selection_mode="multi", default=["EU", "Germany"])
channels = f3.multiselect("Channel to prices", sorted(profiles["channels"]), placeholder="all channels")
shown = table[table["Type"].str.startswith("German").map({True: "Germany", False: "EU"}).isin(where or [])]
if channels:
    shown = shown[shown["Channel"].isin(channels)]
if search:
    text = search.lower()
    shown = shown[shown.apply(lambda r: text in f"{r['Law']} {r['Title']} {laws[r['id']]['summary']}".lower(), axis=1)]

wanted = st.query_params.get("law")
left, right = st.columns([1, 2], gap="large")
with left:
    picked = st.dataframe(shown[["id", "Law", "Adopted", "Relevance", "Rules"]].set_index("id"), hide_index=True, width="stretch",
                          height=min(760, 36 * (len(shown) + 1)), on_select="rerun", selection_mode="single-row",
                          column_config={"Relevance": st.column_config.TextColumn(help="How directly it can move construction prices")})
    rows = picked.selection.rows
    if rows:
        wanted = shown.iloc[rows[0]]["id"]
        st.query_params["law"] = wanted
    st.caption(f"{len(shown)} of {len(table)} laws. Relevance: ●●● direct effect on prices, ●○○ background.")

law_id = wanted if wanted in laws else (shown.iloc[0]["id"] if len(shown) else None)
with right:
    if law_id is None:
        st.info("No law matches the filters.")
        st.stop()
    p = laws[law_id]
    with st.container(border=True):
        st.markdown(f"<span style='font-size:0.8rem;color:#6B7280;font-weight:600'>{p['type'].upper()} · {p['adopted']} · "
                    f"{p['channel'].upper()}</span>", unsafe_allow_html=True)
        st.subheader(f"{p['short']}: {p['title']}")
        st.write(p["summary"])
        st.markdown(f"**How it can reach prices.** {p['price_link']}")
        if p.get("note"):
            st.warning(p["note"])
        related = [e for e in edges if law_id in (e["source"], e["target"]) and e["type"] in ("implements", "amends", "regulates", "sets")]
        name = lambda nid: nodes[nid]["label"] if nid in nodes else nid
        facts = {"Identifier": law_id.removeprefix("law_").replace("de_", "").upper() if law_id.startswith("law_de_") else law_id.removeprefix("law_"),
                 "Main channel": profiles["channels"][p["channel"]],
                 "Relevance for prices": f"{RELEVANCE[p['relevance']]} {p['relevance']}",
                 "Rules extracted from the text": len(rules_of[law_id]) or "none yet",
                 "Implements": ", ".join(name(e["target"]) for e in related if e["type"] == "implements" and e["source"] == law_id),
                 "Implemented by": ", ".join(name(e["source"]) for e in related if e["type"] == "implements" and e["target"] == law_id),
                 "Amends": ", ".join(name(e["target"]) for e in related if e["type"] == "amends" and e["source"] == law_id),
                 "Regulates directly": ", ".join(sorted({name(e["target"]) for e in related if e["type"] == "regulates"}))}
        st.markdown("\n".join(f"- **{k}:** {v}" for k, v in facts.items() if v not in ("", None)))
        b1, b2 = st.columns(2)
        if b1.button("Show in the graph", width="stretch"):
            st.switch_page("views/graph.py", query_params={"focus": law_id})
        sources = sorted({e["evidence"]["url"] for e in edges if e["source"] == law_id and (e.get("evidence") or {}).get("url")})
        if sources:
            b2.link_button("Official text (stored copy's source)", sources[0], width="stretch")

    numbers = [nodes[e["target"]] for e in related if e["type"] == "sets" and e["target"] in nodes]
    for n in numbers:
        st.markdown(f"**Numbers set by this law: {n['label']}** ({n['facts'].get('Unit', '')}, {n['facts'].get('Provision', '')})")
        points = pd.DataFrame(n.get("entries", []))
        if not points.empty:
            st.altair_chart(alt.Chart(points).mark_line(point=True, color="#B45309").encode(
                x=alt.X("period:O", title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("value:Q", title=n["facts"].get("Unit", "")),
                tooltip=["period:N", "value:Q", alt.Tooltip("quote:N", title="Quoted from the law")]).properties(height=200), width="stretch")
            st.caption("Each value is stored only if its quote is found in the official text (hover a point for the quote).")

    rule_ids = rules_of[law_id]
    if rule_ids:
        st.markdown(f"**{len(rule_ids)} rules extracted from the text**")
        regulated = {}
        for e in edges:
            if e["type"] == "regulates" and e["source"] in rule_ids:
                regulated.setdefault(e["source"], []).append(name(e["target"]))
        for rid in sorted(rule_ids, key=lambda r: nodes[r]["facts"].get("Article", "")):
            r = nodes[rid]
            f = r["facts"]
            with st.expander(f"{f.get('Article', '')}: {r['description'] or 'rule'}"):
                st.markdown("\n".join(line for line in (
                    f"- **If:** {f['If']}" if f.get("If") else "",
                    f"- **Then:** {f['Then']}" if f.get("Then") else "",
                    f"- **Binding:** {'yes' if f.get('Binding') == 'HARD' else 'guidance'}" if f.get("Binding") else "",
                    f"- **Applies to:** {', '.join(regulated[rid])}" if rid in regulated else "",
                    f"- **Context:** {f['Context']}" if f.get("Context") else "") if line))
        st.caption("Rules were extracted from the official text by a language model and anchored to the article; "
                   "treat them as a reading aid, not legal advice.")
    elif not numbers:
        st.caption("No rules extracted from this law yet; it is stored because another law refers to it.")
