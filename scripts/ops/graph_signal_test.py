"""Do the training graph's edges carry predictive signal? The precondition for training a graph neural network.

For every growth node of the latest export (scripts.ops.export_training_graph): next-quarter growth from
  ar          own growth lags 1-4 + season
  ar_struct   + weighted mean growth of structural in-neighbours last quarter (cost_share, component, belongs_to,
                tested_link, acts_on)
  ar_peer     + mean growth of the same indicator in the other EU countries last quarter (same_sector)
Each node's fit is shrunk towards the pooled fit over all nodes (partial pooling, prior worth 40 quarters), refitted
at every origin on earlier quarters (expanding window, origins from --since). Each signal is compared with
the AR on the node-quarters where that signal exists (structural edges are German, peer edges are the EU panel). Inputs at lag 1 quarter, release timing ignored: a model comparison, not a
live forecast. Report: data/<pilot>/reports/graph-signal-latest.json.

Usage: python -m scripts.ops.graph_signal_test [--since 2012]
"""
import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import stats

from scripts.ops.panel_backtest import shrunk_fit
from src.config import get_settings

STRUCT = {"cost_share", "component", "belongs_to", "tested_link", "acts_on"}


def qindex(label: str) -> int:
    return int(label[:4]) * 4 + int(label[-1]) - 1


def growth_by_node(nodes: dict, values: list) -> dict:
    levels = defaultdict(dict)
    for v in values:
        levels[v["series_id"]][qindex(v["quarter"])] = float(v["level"])
    out = {}
    for sid, lv in levels.items():
        if nodes[sid]["transform"] == "growth":
            out[sid] = {q: 100 * (x / lv[q - 1] - 1) for q, x in lv.items() if q - 1 in lv and lv[q - 1] and abs(x / lv[q - 1] - 1) < 0.5}
    return out


def neighbour_signal(edges: list, growth: dict, kinds: set) -> dict:
    """{node: {quarter: weighted mean growth of in-neighbours of these edge kinds}} (latest edge year per pair)."""
    weights = defaultdict(dict)
    for e in edges:
        if e["type"] in kinds and e["source"] in growth and e["target"] in growth:
            weights[e["target"]][e["source"]] = abs(float(e["weight"]))  # ponytail: one weight per pair, not per year
    out = {}
    for node, nbrs in weights.items():
        series = {}
        for q in growth[node]:
            vals = [(w, growth[s][q]) for s, w in nbrs.items() if q in growth[s]]
            if vals and sum(w for w, _ in vals) > 0:
                series[q] = sum(w * g for w, g in vals) / sum(w for w, _ in vals)
        out[node] = series
    return out


def compare(growth: dict, signal: dict, nodes: dict, extra: tuple, since: int) -> dict:
    """AR vs AR + `extra` neighbour signals on the node-quarters where those signals exist."""
    rows = []
    for node, g in growth.items():
        for q in g:
            lags = [g.get(q - k) for k in range(1, 5)]
            feats = {k: signal[k].get(node, {}).get(q - 1) for k in extra}
            if None not in lags and None not in feats.values():
                rows.append({"node": node, "q": q, "y": g[q], "ar": lags + [float(q % 4 == s) for s in (1, 2, 3)], **feats})
    design = lambda r, use: [1.0] + r["ar"] + [r[k] for k in use]
    models = {"ar": (), "with_signal": extra}
    errors, keys = {m: [] for m in models}, []
    for origin in sorted({r["q"] for r in rows if r["q"] >= since * 4}):
        train = [r for r in rows if r["q"] < origin]
        test = [r for r in rows if r["q"] == origin]
        by_node = defaultdict(list)
        for r in train:
            by_node[r["node"]].append(r)
        for name, use in models.items():
            pooled = np.linalg.lstsq(np.array([design(r, use) for r in train]), np.array([r["y"] for r in train]), rcond=None)[0]
            for r in test:
                own = by_node[r["node"]]
                beta = pooled if len(own) < 12 else shrunk_fit(np.array([design(t, use) for t in own]), np.array([t["y"] for t in own]), pooled, 40)
                errors[name].append(abs(float(np.dot(beta, design(r, use))) - r["y"]))
        keys.extend((r["node"], r["q"]) for r in test)
    err = {m: np.array(v) for m, v in errors.items()}
    by_q = defaultdict(list)
    for i, (_, q) in enumerate(keys):  # errors correlate within a quarter: average per quarter, then test
        by_q[q].append(err["with_signal"][i] - err["ar"][i])
    de = [i for i, (node, _) in enumerate(keys) if nodes[node]["geo"] == "DE"]
    better = defaultdict(list)
    for i, (node, _) in enumerate(keys):
        better[node].append(err["ar"][i] - err["with_signal"][i])
    return {"nodes": len({n for n, _ in keys}), "node_quarters": len(keys),
            "mae": {m: round(float(v.mean()), 4) for m, v in err.items()},
            "p_value": round(float(stats.ttest_1samp([np.mean(v) for v in by_q.values()], 0).pvalue), 4),
            "nodes_improved": int(sum(np.mean(v) > 0 for v in better.values())),
            "mae_de": {m: round(float(v[de].mean()), 4) for m, v in err.items()} if de else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", type=int, default=2012)
    args = parser.parse_args()
    root = max((Path(get_settings().data_dir) / "datasets").glob("training-graph-*"))
    read = lambda name: list(csv.DictReader(open(root / f"{name}.csv", encoding="utf-8")))
    nodes = {n["series_id"]: n for n in read("nodes")}
    growth = growth_by_node(nodes, read("values"))
    edges = read("edges")
    signal = {"struct": neighbour_signal(edges, growth, STRUCT), "peer": neighbour_signal(edges, growth, {"same_sector"})}
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "export": root.name, "since": args.since,
              "comparisons": {}, "method": __doc__.split("\n\n")[1].replace("\n", " ")}
    for label, extra in (("struct", ("struct",)), ("peer", ("peer",))):
        report["comparisons"][label] = compare(growth, signal, nodes, extra, args.since)
    out = Path(get_settings().data_dir) / "reports" / "graph-signal-latest.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["comparisons"]))


if __name__ == "__main__":
    main()
