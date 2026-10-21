"""Export the training graph: measured series as nodes, evidenced or tested links as edges, dated quarterly values.

What goes in (decisions.md, "EU country panel" and "Cross-border supply"):
  nodes   one per measured series (German prices and trades, EU panel, world prices, policy numbers, surveys), with
          its geography, the sector/commodity it measures and the metric. No law, event or company nodes.
  values  quarterly level (mean of complete months; annual policy values repeated per quarter) and the time it was
          public: the archived release date where the store has one, else period end + a stated lag rule.
  edges   cost_share (Germany's input-output shares), component (official trade weights), belongs_to (product
          classification), tested_link (material/world price links supported by scripts.ops.estimate_links), acts_on
          (policy number -> the goods it prices), same_sector (one EU panel indicator across countries).
Left out on purpose: laws and events (no values), FIGARO trade weights (no better than equal weights).
Output: data/<pilot>/datasets/training-graph-<date>/{nodes,values,edges}.csv and manifest.json.

Usage: python -m scripts.ops.export_training_graph
"""
import csv
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from src.config import REPO_ROOT, get_settings, primary_target_series_id
from src.storage.dual_layer_store import DualLayerStore

# Days after period end until a value counts as public, for series the store holds only fetch-dated.
# ponytail: one lag per source; per-series lags live in config/predictor_release_lags.json for the forecast.
RULE_LAG_DAYS = {"EUROSTAT_STS": 100, "WORLD_BANK_PINK_SHEET": 5, "ECB_SDMX": 45, "BUNDESBANK_SDMX": 45,
                 "DESTATIS_GENESIS": 120, "LAW_PARAMETERS": None, "GDELT_DOC": 2}
LEVEL_METRICS = {"price_expectations", "material_shortage", "mortgage_rate", "covered_bond_yield", "news_attention",
                 "co2_price", "free_allocation_share", "minimum_wage"}


def quarter(period_start: str) -> str:
    return f"{period_start[:4]}-Q{(int(period_start[5:7]) - 1) // 3 + 1}"


def quarterly_values(rows: list[dict], frequency: str, lag_days) -> dict:
    """{quarter: (level, public_at)} from one series' revisions: earliest availability per period, complete quarters."""
    periods = {}
    for r in rows:
        key = r["period_start"]
        # lag_days None: known from the period start (law numbers, 'known_from' in the observation lineage).
        anchor = r["period_start"] if lag_days is None else r["period_end_exclusive"]
        rule = datetime.combine(date.fromisoformat(anchor) + timedelta(days=lag_days or 0), time(), timezone.utc)
        public = r["available_at"] if r["release_dated"] else rule
        if key not in periods or public < periods[key][1]:
            periods[key] = (r["value"], public)
    out = defaultdict(list)
    for start, (value, public) in periods.items():
        if frequency == "ANNUAL":
            for q in range(4):
                out[f"{start[:4]}-Q{q + 1}"].append((value, public))
        else:
            out[quarter(start)].append((value, public))
    need = {"MONTHLY": 3, "QUARTERLY": 1, "ANNUAL": 1}.get(frequency, 1)
    return {q: (sum(v for v, _ in vals) / len(vals), max(p for _, p in vals)) for q, vals in out.items() if len(vals) == need}


def build_edges(facts: list, measured: dict, links: dict) -> list[dict]:
    """Edges between series. `measured`: {(geo, node, metric_group): [series ids]}; facts are accepted store facts."""
    payload = lambda f: json.loads(f.payload_json or "{}")
    de = lambda node, group: measured.get(("DE", node, group), [])
    edges = []
    for f in facts:
        p = payload(f)
        if f.predicate == "FLOWS_TO" and "scope" not in p and f.object_id == "sec_F" and p.get("share"):
            for s in de(f.subject_id, "price"):
                for t in de("sec_F", "output"):
                    edges.append({"source": s, "target": t, "type": "cost_share", "weight": p["share"], "year": p["episode"], "basis": "Eurostat naio_10_cp1700"})
        elif f.predicate == "COMPONENT_OF" and "share_of_total" in p:
            for s in de(f.subject_id, "output"):
                for t in de("sec_F", "output"):
                    edges.append({"source": s, "target": t, "type": "component", "weight": p["share_of_total"], "year": 2021, "basis": "IT.NRW weighting scheme"})
        elif f.predicate == "BELONGS_TO":
            for s in de(f.subject_id, "price"):
                for t in de(f.object_id, "price"):
                    edges.append({"source": s, "target": t, "type": "belongs_to", "weight": 1.0, "year": None, "basis": "CPA/GP2019 classification"})
    for link in links.get("links", []):
        if link["hypothesised"] and link["verdict"] == "supported":
            for s in de(f"commodity_{link['material']}", "price"):
                for t in de(f"sec_{link['trade']}", "output"):
                    edges.append({"source": s, "target": t, "type": "tested_link", "weight": link["pass_through"], "year": None, "basis": "estimate_links trade test"})
    for link in links.get("chain_links", []):
        if link["verdict"] == "supported":
            node = lambda ref: ("commodity_" if ref.startswith("commodity:") else "sec_") + ref.split(":", 1)[1]
            for s in measured.get(("WORLD", node(link["from"]), "world"), []):
                for t in de(node(link["to"]), "price") + de(node(link["to"]), "output"):
                    edges.append({"source": s, "target": t, "type": "tested_link", "weight": link["pass_through"], "year": None, "basis": "estimate_links chain test"})
    for (geo, node, group), ids in measured.items():
        if group == "policy":
            for s in ids:
                for t in de(node, "price"):
                    edges.append({"source": s, "target": t, "type": "acts_on", "weight": 1.0, "year": None, "basis": "law text (LAW_PARAMETERS)"})
    return edges


def same_sector_edges(panel: dict) -> list[dict]:
    """panel: {indicator: [series ids across countries]} -> edges between every pair, weight 1/(n-1)."""
    edges = []
    for indicator, ids in panel.items():
        for s in ids:
            for t in ids:
                if s != t:
                    edges.append({"source": s, "target": t, "type": "same_sector", "weight": round(1 / (len(ids) - 1), 5), "year": None, "basis": f"EU panel {indicator}"})
    return edges


def metric_group(metric: str) -> str:
    return {"output_price": "output", "producer_price": "price", "material_cost": "price", "labour_cost": "price", "wages": "price",
            "world_price": "world", "co2_price": "policy", "free_allocation_share": "policy", "minimum_wage": "policy"}.get(metric, "other")


def main() -> None:
    store = DualLayerStore()
    now = datetime.now(timezone.utc)
    names = json.loads((REPO_ROOT / "config" / "series_names.json").read_text(encoding="utf-8"))["series"]
    from src.config import load_data_sources_registry
    context = load_data_sources_registry()["sources"]["EUROSTAT_STS"]["context_series"]
    panel_slug = {s["provider_series_key"]: name[3:].rsplit("_", 1)[0] for name, s in context.items() if name.startswith("eu_")}
    definitions = {d.series_id: d for d in store.query_series(now, "public_as_of")}
    nodes, measured, panel = [], defaultdict(list), defaultdict(list)
    for sid, info in names.items():
        targets = info.get("measures")
        if info.get("status") != "active" or not targets or sid not in definitions:
            continue
        targets = targets if isinstance(targets, list) else [targets]
        d = definitions[sid]
        geo = d.geography_id or "DE"
        metric = targets[0].get("metric", "level")
        for t in targets:
            node = f"commodity_{t['commodity']}" if "commodity" in t else f"sec_{t['sector']}"
            measured[(geo, node, metric_group(t.get("metric", "level")))].append(sid)
        if sid in panel_slug:
            panel[panel_slug[sid]].append(sid)
        nodes.append({"series_id": sid, "name": info["name"], "geo": geo, "measures": ";".join(
            f"commodity_{t['commodity']}" if "commodity" in t else f"sec_{t['sector']}" for t in targets), "metric": metric,
            "transform": "level" if metric in LEVEL_METRICS else "growth", "frequency": d.frequency, "source_id": d.source_id,
            "is_target": sid == primary_target_series_id()})
    keep = {n["series_id"] for n in nodes}
    # A value is release-dated only if an archived release or provider vintage document supports it.
    dated_docs = {v for v, d in store._canonical_documents.items() if d.availability_basis in ("archived_release", "provider_vintage")}
    release_dated = {link.assertion_version_id for link in store._canonical_evidence.values()
                     if link.document_version_id in dated_docs and link.support_type == "DIRECT"}
    rows = defaultdict(list)
    for o in store.query_observation_revisions(now, "public_as_of", series_ids=keep):
        rows[o.series_id].append({"period_start": o.period_start, "period_end_exclusive": o.period_end_exclusive, "value": o.value,
                                  "available_at": store._available(o), "release_dated": o.revision_id in release_dated})
    values = []
    for n in nodes:
        lag = RULE_LAG_DAYS.get(n["source_id"], 120)
        for q, (level, public) in sorted(quarterly_values(rows[n["series_id"]], n["frequency"], lag).items()):
            values.append({"series_id": n["series_id"], "quarter": q, "level": round(level, 6), "public_at": public.isoformat()})
    facts = store.query_facts(now, "public_as_of", include_expired=True)
    links_path = Path(get_settings().data_dir) / "reports" / "graph-links-latest.json"
    links = json.loads(links_path.read_text(encoding="utf-8")) if links_path.exists() else {}
    edges = [e for e in build_edges(facts, measured, links) + same_sector_edges(panel) if e["source"] in keep and e["target"] in keep]
    out = Path(get_settings().data_dir) / "datasets" / f"training-graph-{now:%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    for name, data in (("nodes", nodes), ("values", values), ("edges", edges)):
        with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    counts = defaultdict(int)
    for e in edges:
        counts[e["type"]] += 1
    manifest = {"generated_at": now.isoformat(), "store_generation": store._generation, "nodes": len(nodes), "values": len(values),
                "edges": dict(counts), "geos": sorted({n["geo"] for n in nodes}), "rule_lag_days": RULE_LAG_DAYS,
                "method": __doc__.split("\n\n")[1].replace("\n", " ")}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), **{k: manifest[k] for k in ("nodes", "values", "edges")}}))


if __name__ == "__main__":
    main()
