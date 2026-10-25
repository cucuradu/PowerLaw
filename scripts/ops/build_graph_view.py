"""Snapshot of the store as one graph for the web app: nodes with descriptions, relations with weights and evidence.

Zones (node groups): laws and rules, construction and trades, suppliers and buyers (input-output), materials and
commodities, data series. Relations: what the store records (law -> rule, implements, regulates, input-output flows
by year, trade weights, product classification, series -> what they measure) plus the price links tested by
scripts.ops.estimate_links and the cost chains of config/chains.json. Other EU countries' series are grouped into
one node per indicator; cross-border (FIGARO) flows are counted, not drawn.
Writes data/<pilot>/reports/graph-view-latest.json. Run weekly by scripts.ops.update_forecast.

Usage: python -m scripts.ops.build_graph_view
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.config import REPO_ROOT, get_settings, load_data_sources_registry, primary_target_series_id
from src.storage.dual_layer_store import DualLayerStore
from src.utils.taxonomy import is_german_series

ZONES = {
    "laws": {"label": "Laws and rules", "color": "#DC2626",
             "about": "EU and German laws in the store and the rules extracted from their text."},
    "construction": {"label": "Construction and trades", "color": "#2563EB",
                     "about": "German construction (the forecast target) and the trades that make up its price index."},
    "economy": {"label": "Suppliers and buyers", "color": "#16A34A",
                "about": "Product groups construction buys from and sells to, from Eurostat input-output tables (2010-2022)."},
    "materials": {"label": "Materials and commodities", "color": "#F97316",
                  "about": "Building materials, energy and world commodities with stored prices."},
    "data": {"label": "Data series", "color": "#7C3AED",
             "about": "Measured series (prices, costs, orders, surveys, policy numbers) and what each measures."},
}
EDGE_TYPES = {
    "contains": {"label": "contains rule", "color": "#DC2626", "dashes": False,
                 "about": "The law contains this rule, extracted from the article quoted."},
    "implements": {"label": "implements", "color": "#991B1B", "dashes": True,
                   "about": "The German law transposes this EU law (quoted from its text)."},
    "amends": {"label": "amends", "color": "#991B1B", "dashes": True, "about": "The law amends the other law."},
    "regulates": {"label": "regulates", "color": "#F87171", "dashes": False,
                  "about": "The rule or law applies to this sector or good (quoted provision)."},
    "supplies": {"label": "supplies construction", "color": "#16A34A", "dashes": False,
                 "about": "Share of construction's output value spent on this input in the year shown (Eurostat naio_10_cp1700)."},
    "buys": {"label": "buys from construction", "color": "#0D9488", "dashes": False,
             "about": "Share of construction's total output used by this buyer in the year shown (Eurostat naio_10_cp1700)."},
    "part_of": {"label": "weight in the price index", "color": "#2563EB", "dashes": False,
                "about": "Official weight of the trade in the price index for residential buildings (Wohngebäude, IT.NRW weighting "
                         "scheme, base 2021), shown as a share of its group and of the whole index."},
    "classified_in": {"label": "belongs to product group", "color": "#9CA3AF", "dashes": True,
                      "about": "The good belongs to this product group (CPA 2.1 / GP 2019 classification)."},
    "sets": {"label": "sets this number", "color": "#B45309", "dashes": False,
             "about": "The law sets these values (each quoted from the official text; config/data_sources.json LAW_PARAMETERS)."},
    "measures": {"label": "measured by", "color": "#A78BFA", "dashes": [2, 4],
                 "about": "The data series measures this node (prices, costs, orders, policy numbers)."},
    "tested_link": {"label": "tested price link", "color": "#F97316", "dashes": False,
                    "about": "Price link confirmed by a statistical test with a common-trend control (scripts.ops.estimate_links); weight = pass-through."},
    "chain": {"label": "cost chain step", "color": "#FB923C", "dashes": [8, 4],
              "about": "A step of a cost chain (config/chains.json): how a cost passes from one stage to the next, with its test status."},
}
CHAIN_STATUS = {"tested_supported": "tested: supported", "tested_not_supported": "tested: not supported",
                "in_store": "data stored, not tested", "needs_data": "needs data", "law_text": "quoted from the law, effect not measured"}


def yearly_moves(points: list, unit: str = "") -> dict | None:
    """How much the series moved over twelve months in its history: percentiles, record rise and fall, latest.
    Percent changes for indices and prices, point changes for series already in percent (rates, shares)."""
    by_month = {str(period)[:7]: value for period, value in points if value is not None}
    in_points = "%" in (unit or "") or "percent" in (unit or "").lower()
    moves = []
    for month, value in sorted(by_month.items()):
        before = by_month.get(f"{int(month[:4]) - 1}{month[4:]}")
        if before is not None and (in_points or before > 0):
            moves.append((month, value - before if in_points else 100 * (value / before - 1)))
    if len(moves) < 8:
        return None
    values = sorted(m for _, m in moves)
    pick = lambda q: round(values[min(len(values) - 1, int(q * len(values)))], 2)
    return {"unit": "points" if in_points else "%", "p10": pick(0.1), "p50": pick(0.5), "p90": pick(0.9),
            "low": round(values[0], 2), "high": round(values[-1], 2), "latest": round(moves[-1][1], 2),
            "latest_month": moves[-1][0], "since": moves[0][0], "n": len(moves)}


def node_ref(ref: str) -> str:
    kind, name = ref.split(":", 1)
    return {"commodity": f"commodity_{name}", "new": f"planned:{name}", "law": f"law_{name}"}.get(kind, f"sec_{name}")


def main() -> None:
    store = DualLayerStore()
    now = datetime.now(timezone.utc)
    profiles = json.loads((REPO_ROOT / "config" / "law_profiles.json").read_text(encoding="utf-8"))["laws"]
    names = json.loads((REPO_ROOT / "config" / "series_names.json").read_text(encoding="utf-8"))["series"]
    chains = json.loads((REPO_ROOT / "config" / "chains.json").read_text(encoding="utf-8"))["chains"]
    links_path = Path(get_settings().data_dir) / "reports" / "graph-links-latest.json"
    tested = json.loads(links_path.read_text(encoding="utf-8")) if links_path.exists() else {}
    entities = store._canonical_entities
    facts = store.query_facts(now, "public_as_of", include_expired=True)
    evidence = {}
    for link in store._canonical_evidence.values():
        doc = store._canonical_documents.get(link.document_version_id)
        if link.support_type == "DIRECT" and link.assertion_version_id not in evidence:
            evidence[link.assertion_version_id] = {"quote": (link.quoted_text or "")[:600],
                                                   "url": doc.final_url if doc else None, "publisher": doc.publisher if doc else None}
    nodes, edges = {}, []

    def add_entity(entity_id: str) -> bool:
        if entity_id in nodes:
            return True
        e = entities.get(entity_id)
        if e is None:
            return False
        label = e.canonical_label.replace("Trade: ", "")
        if entity_id.startswith("law_"):
            p = profiles.get(entity_id, {})
            nodes[entity_id] = {"zone": "laws", "kind": "law", "label": p.get("short", label), "description": p.get("summary", ""),
                                "facts": {"Title": p.get("title", label), "Type": p.get("type"), "Adopted": p.get("adopted"),
                                          "Main channel": p.get("channel"), "Relevance for prices": p.get("relevance"),
                                          "How it reaches prices": p.get("price_link"), "Note": p.get("note")}}
        elif entity_id.startswith("sec_"):
            code = entity_id[4:]
            construction = code == "F" or code.startswith("F_")
            nodes[entity_id] = {"zone": "construction" if construction else "economy",
                                "kind": "trade" if code.startswith("F_BPNB") else "value added" if e.entity_subtype == "PRIMARY_INPUT" else "sector",
                                "label": label, "description": "", "facts": {"Code": code}}
        elif entity_id.startswith("commodity_"):
            nodes[entity_id] = {"zone": "materials", "kind": "commodity", "label": label, "description": "", "facts": {}}
        else:
            return False
        return True

    for entity_id, e in entities.items():  # every law, also those without stored relations
        if e.entity_type == "LAW":
            add_entity(entity_id)
    flows = 0
    for f in facts:
        p = json.loads(f.payload_json or "{}")
        ev = evidence.get(f.fact_version_id)
        if f.predicate == "MANDATES":
            if add_entity(f.subject_id):
                mech = p.get("mechanism") or {}
                article = (p.get("metadata") or {}).get("article_id", "").replace("ART_", "Art. ")
                nodes[f.object_id] = {"zone": "laws", "kind": "rule", "label": f"{nodes[f.subject_id]['label']} {article}".strip(),
                                      "description": p.get("summary", ""),
                                      "facts": {"Law": nodes[f.subject_id]["label"], "Article": article, "If": mech.get("condition"),
                                                "Then": mech.get("non_numeric_obligation") or mech.get("target_variable"),
                                                "Binding": (p.get("enforcement") or {}).get("strictness"),
                                                "Context": p.get("background_context")}}
                edges.append({"source": f.subject_id, "target": f.object_id, "type": "contains", "evidence": ev})
        elif f.predicate in ("IMPLEMENTS", "AMENDS"):
            if add_entity(f.subject_id) and add_entity(f.object_id):
                edges.append({"source": f.subject_id, "target": f.object_id, "type": f.predicate.lower(), "evidence": ev})
        elif f.predicate == "FLOWS_TO":
            if "scope" in p:  # cross-border FIGARO flows: counted only
                flows += 1
                continue
            if "sec_F" in (f.subject_id, f.object_id) and p.get("share") and add_entity(f.subject_id) and add_entity(f.object_id):
                edges.append({"source": f.subject_id, "target": f.object_id, "type": "supplies" if f.object_id == "sec_F" else "buys",
                              "weight": p["share"], "year": p.get("episode"), "value_mio_eur": p.get("value_mio_eur")})
        elif f.predicate == "COMPONENT_OF" and "share_of_total" in p:
            if add_entity(f.subject_id) and add_entity(f.object_id):
                edges.append({"source": f.subject_id, "target": f.object_id, "type": "part_of", "weight": p["share_of_total"], "evidence": ev})
        elif f.predicate == "BELONGS_TO":
            if add_entity(f.subject_id) and add_entity(f.object_id):
                edges.append({"source": f.subject_id, "target": f.object_id, "type": "classified_in", "evidence": ev})
    for f in facts:  # rules and laws regulate nodes that exist by now (or that we can add)
        if f.predicate == "REGULATES" and (f.subject_id in nodes or add_entity(f.subject_id)) and add_entity(f.object_id):
            p = json.loads(f.payload_json or "{}")
            edges.append({"source": f.subject_id, "target": f.object_id, "type": "regulates",
                          "note": p.get("norm"), "evidence": evidence.get(f.fact_version_id)})
    # Data series: German series one by one, other EU countries grouped by indicator.
    definitions = {d.series_id: d for d in store.query_series(now, "public_as_of")}
    spans = defaultdict(list)
    for row in store.query_observations(now, "public_as_of", series_ids=list(definitions)).to_pylist():
        if row["value"] is not None:
            spans[row["series_id"]].append((row["period_start"], row["value"]))
    law_numbers = {spec["series_id"]: (doc["law_id"], spec)
                   for doc in load_data_sources_registry()["sources"]["LAW_PARAMETERS"]["documents"].values()
                   for spec in doc.get("parameters", [])}
    panel = defaultdict(list)
    for sid, info in names.items():
        measures = info.get("measures")
        if info.get("status") != "active" or not measures or sid not in definitions:
            continue
        targets = measures if isinstance(measures, list) else [measures]
        refs = [f"commodity_{t['commodity']}" if "commodity" in t else f"sec_{t['sector']}" for t in targets]
        if not is_german_series(sid):
            panel[info["name"].split(": ", 1)[-1]].append((sid, refs))
            continue
        points = sorted(spans.get(sid, []))
        d = definitions[sid]
        nid = f"series:{sid}"
        nodes[nid] = {"zone": "data", "kind": "policy number" if sid.startswith("POLICY") else "series", "label": info["name"],
                      "description": "", "facts": {"Source": d.source_id, "Frequency": d.frequency, "Unit": d.unit,
                                                   "From": points[0][0] if points else None, "To": points[-1][0] if points else None,
                                                   "Latest value": points[-1][1] if points else None,
                                                   "Forecast target": "yes" if sid == primary_target_series_id() else None,
                                                   "Store id": sid}}
        if not sid.startswith("POLICY") and (moves := yearly_moves(points, d.unit)):
            nodes[nid]["moves"] = moves
        if sid in law_numbers:  # numbers set by law: the quoted values and the law that sets them
            law_id, spec = law_numbers[sid]
            nodes[nid]["entries"] = spec["entries"]
            nodes[nid]["facts"].update({"Set by": law_id, "Provision": spec.get("norm"), "Unit": spec.get("unit"),
                                        "What": spec.get("label")})
            if add_entity(law_id):
                edges.append({"source": law_id, "target": nid, "type": "sets", "note": spec.get("norm")})
        for ref in refs:
            if add_entity(ref):
                edges.append({"source": nid, "target": ref, "type": "measures"})
    for indicator, members in panel.items():
        nid = f"panel:{indicator}"
        nodes[nid] = {"zone": "data", "kind": "EU panel", "label": f"EU panel: {indicator}", "description": "",
                      "facts": {"Countries": len(members), "Source": "Eurostat (short-term statistics)"}}
        for ref in sorted({r for _, refs in members for r in refs}):
            if add_entity(ref):
                edges.append({"source": nid, "target": ref, "type": "measures", "note": "same indicator in other EU countries"})
    # Chain stages proposed in config/chains.json: the stored series when its source names one, else a planned node.
    chain_config = json.loads((REPO_ROOT / "config" / "chains.json").read_text(encoding="utf-8"))
    for key, spec in chain_config.get("new_nodes", {}).items():
        stored = next((nid for nid in nodes if nid.startswith("series:") and nid[7:] in spec.get("source", "")), None)
        if stored:
            nodes[f"planned:{key}"] = {**nodes[stored], "alias_of": stored}
        else:
            nodes[f"planned:{key}"] = {"zone": "data", "kind": "planned", "label": spec["label"],
                                       "description": spec.get("about") or "A stage of a cost chain without data in the store yet.",
                                       "facts": {"Where to get it": spec.get("source"), "Status": CHAIN_STATUS.get(spec.get("status"), spec.get("status"))}}
    # Tested price links and cost chains.
    for link in tested.get("links", []):
        if link.get("hypothesised") and link["verdict"] == "supported":
            s, t = f"commodity_{link['material']}", f"sec_{link['trade']}"
            if add_entity(s) and add_entity(t):
                edges.append({"source": s, "target": t, "type": "tested_link", "weight": link["pass_through"],
                              "note": f"pass-through {link['pass_through']:.3f}, p = {link['p']:.3f}, {link['n']} quarters"})
    chain_tests = {(r["chain"], r["from"], r["to"]): r for r in tested.get("chain_links", [])}
    for chain in chains:
        for link in chain["links"]:
            s, t = (nodes[n].get("alias_of", n) if n in nodes else n for n in (node_ref(link["from"]), node_ref(link["to"])))
            if (s in nodes or add_entity(s)) and (t in nodes or add_entity(t)):
                status, extra = CHAIN_STATUS.get(link.get("status"), link.get("status")), {}
                test = chain_tests.get((chain["id"], link["from"], link["to"]))
                if test:  # the latest test overrides the status written in the config
                    status = f"tested: {test['verdict']} (pass-through {test['pass_through']:.2f}, p = {test['p']:.3f}, {test['n']} quarters)"
                    extra = {"weight": test["pass_through"]} if test["verdict"] == "supported" else {}
                edges.append({"source": s, "target": t, "type": "chain", "chain": chain["id"],
                              "note": f"{link.get('mechanism', '')}; {status}", **extra,
                              **({"evidence": {"quote": link["quote"]}} if link.get("quote") else {})})
    for i, e in enumerate(edges):
        e["id"] = f"e{i}"
    degree = defaultdict(int)
    for e in edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    nodes = {nid: n for nid, n in nodes.items() if "alias_of" not in n and (not nid.startswith("planned:") or degree[nid])}
    for nid, n in nodes.items():
        n["id"] = nid
        n["degree"] = degree[nid]
        n["facts"] = {k: v for k, v in n["facts"].items() if v not in (None, "")}
    report = {"generated_at": now.isoformat(), "zones": ZONES, "edge_types": EDGE_TYPES,
              "years": sorted({e["year"] for e in edges if e.get("year")}),
              "chains": [{"id": c["id"], "label": c["label"], "why": c.get("why", "")} for c in chains],
              "inventory": {"nodes": len(nodes), "relations": len(edges), "documents": len(store._canonical_documents),
                            "series": len(definitions), "cross_border_flows_not_drawn": flows},
              "nodes": list(nodes.values()), "edges": edges}
    out = Path(get_settings().data_dir) / "reports" / "graph-view-latest.json"
    out.write_text(json.dumps(report, default=str), encoding="utf-8")
    zones = defaultdict(int)
    for n in nodes.values():
        zones[n["zone"]] += 1
    types = defaultdict(int)
    for e in edges:
        types[e["type"]] += 1
    print(json.dumps({"output": str(out), "zones": zones, "edges": types, "inventory": report["inventory"]}))


if __name__ == "__main__":
    main()
