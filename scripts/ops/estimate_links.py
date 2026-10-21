"""Test hypothesised material -> trade links against price data, and look for links nobody hypothesised.

For each trade: quarterly growth of its price index (excluding VAT) regressed on its own previous growth, season and
the average growth of all other materials (the common factor: without it every price "explains" every other one in
2021-23), plus one material's growth in the same and two previous quarters. A link is supported when the material
terms are jointly significant (F test, p < 0.05) and their summed pass-through is positive. Results are model output, not facts:
they go to data/<pilot>/reports/graph-links-latest.json, never into the canonical store.

Usage: python -m scripts.ops.estimate_links [--since 2005-01-01]
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import stats

from src.config import REPO_ROOT, get_settings
from src.storage.dual_layer_store import DualLayerStore
from src.utils.taxonomy import is_german_series

LAGS = 3  # material growth at t, t-1, t-2
FX_SERIES = "EXR.M.USD.EUR.SP00.A"  # ECB reference rate, US dollars per euro, monthly average


def quarterly_growth(levels: dict) -> dict:
    """{period 'YYYY-MM'} -> percent change on the previous quarter, from monthly or quarterly levels."""
    quarters = {}
    for period, value in levels.items():
        quarter = (int(period[:4]), (int(period[5:7]) - 1) // 3)
        quarters.setdefault(quarter, []).append(value)
    means = {q: np.mean(v) for q, v in quarters.items() if len(v) in (1, 3)}
    previous = lambda q: (q[0], q[1] - 1) if q[1] else (q[0] - 1, 3)
    return {q: 100 * (v / means[previous(q)] - 1) for q, v in means.items() if previous(q) in means}


def link_test(trade: dict, material: dict, common: dict, since: tuple) -> dict | None:
    lag = lambda q, k: (q[0] + (q[1] - k) // 4, (q[1] - k) % 4)
    rows = [q for q in sorted(trade) if q >= since and lag(q, 1) in trade
            and all(lag(q, k) in material and lag(q, k) in common for k in range(LAGS))]
    if len(rows) < 30:
        return None
    y = np.array([trade[q] for q in rows])
    base = np.column_stack([np.ones(len(rows)), [trade[lag(q, 1)] for q in rows]]
                           + [[float(q[1] == s) for q in rows] for s in (1, 2, 3)]
                           + [[common[lag(q, k)] for q in rows] for k in range(LAGS)])
    extra = np.column_stack([[material[lag(q, k)] for q in rows] for k in range(LAGS)])
    full = np.column_stack([base, extra])
    rss = lambda X: float(np.sum((y - X @ np.linalg.lstsq(X, y, rcond=None)[0]) ** 2))
    rss_base, rss_full = rss(base), rss(full)
    dof = len(rows) - full.shape[1]
    f = ((rss_base - rss_full) / LAGS) / (rss_full / dof)
    coef = np.linalg.lstsq(full, y, rcond=None)[0][-LAGS:]
    return {"n": len(rows), "pass_through": float(coef.sum()), "f": float(f), "p": float(stats.f.sf(f, LAGS, dof)),
            "r2_gain": float((rss_base - rss_full) / max(np.sum((y - y.mean()) ** 2), 1e-12))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="2005-01-01")
    args = parser.parse_args()
    since = (int(args.since[:4]), (int(args.since[5:7]) - 1) // 3)
    store = DualLayerStore()
    now = datetime.now(timezone.utc)
    names = json.loads((REPO_ROOT / "config" / "series_names.json").read_text(encoding="utf-8"))["series"]
    hypotheses = json.loads((REPO_ROOT / "config" / "trade_inputs.json").read_text(encoding="utf-8"))["trades"]
    node_series = {}  # sector/commodity node -> series id (price series only)
    for series_id, info in names.items():
        measures = info.get("measures") or {}
        if isinstance(measures, list):  # policy numbers acting on several nodes are not prices
            continue
        node = measures.get("commodity") or measures.get("sector")
        if (info["status"] == "active" and measures.get("metric") in ("producer_price", "output_price") and node
                and is_german_series(series_id)):
            node_series.setdefault(node, series_id)
    world = {measures["commodity"]: series_id for series_id, info in names.items()
             if isinstance(measures := info.get("measures"), dict) and measures.get("metric") == "world_price"}
    levels, fx_days = {}, {}
    for row in store.query_observations(cutoff=now, mode="public_as_of").to_pylist():
        if row["value"] is None:
            continue
        if row["series_id"] == FX_SERIES:
            fx_days.setdefault(row["period_start"][:7], []).append(row["value"])
        else:
            levels.setdefault(row["series_id"], {})[row["period_start"][:7]] = row["value"]
    usd_per_eur = {month: float(np.mean(v)) for month, v in fx_days.items()}
    growth = {node: quarterly_growth(levels[sid]) for node, sid in node_series.items() if sid in levels}
    materials = [m for m in json.loads((REPO_ROOT / "config" / "sectors.json").read_text(encoding="utf-8"))["commodities"] if m in growth]
    results = []
    for trade in sorted(t for t in growth if t.startswith("F_BPNB")):
        tested = {}
        for m in materials:
            others = [growth[o] for o in materials if o != m]
            common = {q: float(np.mean([g[q] for g in others if q in g])) for q in growth[m]}
            tested[m] = link_test(growth[trade], growth[m], common, since)
        for material, test in tested.items():
            if test is None:
                continue
            hypothesised = material in hypotheses.get(trade, [])
            supported = test["p"] < 0.05 and test["pass_through"] > 0
            # Bonferroni across all materials for links nobody proposed, so a scan of 30 candidates is not a fishing trip.
            discovered = not hypothesised and test["p"] < 0.05 / len(materials) and test["pass_through"] > 0
            if hypothesised or discovered:
                results.append({"trade": trade, "material": material, "hypothesised": hypothesised,
                                "verdict": "supported" if (supported if hypothesised else discovered) else "not supported",
                                **{k: round(v, 4) if isinstance(v, float) else v for k, v in test.items()}})
    # Chain links from config/chains.json: world prices (converted to euros) -> German material and trade prices.
    chain_results = []
    for chain in json.loads((REPO_ROOT / "config" / "chains.json").read_text(encoding="utf-8"))["chains"]:
        for link in chain["links"]:
            source, target = link["from"].split(":", 1)[1], link["to"].split(":", 1)[1]
            if link.get("from_metric") != "world_price" or world.get(source) not in levels or target not in growth:
                continue
            in_eur = {m: v / usd_per_eur[m] for m, v in levels[world[source]].items() if m in usd_per_eur}
            others = [growth[o] for o in materials if o != target]
            common = {q: float(np.mean([g[q] for g in others if q in g])) for q in growth[target]}
            test = link_test(growth[target], quarterly_growth(in_eur), common, since)
            if test:
                chain_results.append({"chain": chain["id"], "from": link["from"], "to": link["to"], "metric": "world_price_eur",
                                      "verdict": "supported" if test["p"] < 0.05 and test["pass_through"] > 0 else "not supported",
                                      **{k: round(v, 4) if isinstance(v, float) else v for k, v in test.items()}})
    out = Path(get_settings().data_dir) / "reports" / "graph-links-latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": now.isoformat(), "since": args.since, "links": results, "chain_links": chain_results,
                               "method": __doc__.split("\n\n")[1].replace("\n", " ")}, indent=2), encoding="utf-8")
    hyp = [r for r in results if r["hypothesised"]]
    print(json.dumps({"output": str(out), "hypothesised": len(hyp), "supported": sum(r["verdict"] == "supported" for r in hyp),
                      "discovered": sum(not r["hypothesised"] for r in results),
                      "chain_links": len(chain_results), "chain_supported": sum(r["verdict"] == "supported" for r in chain_results)}))


if __name__ == "__main__":
    main()
