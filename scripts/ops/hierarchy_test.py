"""Does adding up the trade forecasts beat forecasting the total directly? (index hierarchy, next release)

The target is a fixed-weight aggregate of trade price indices (IT.NRW weights, base 2021, COMPONENT_OF facts).
At every origin of the published next-release backtest (reports/backtest-*.json) each trade gets the forecast of
its own model, chosen on origins before 2018 exactly as in scripts.ops.project_series (pooled seasonal AR, the
target model's inputs, ...). Bottom-up: total growth ex VAT = sum of trade growths weighted by weight x level at
the origin, then the VAT step known at the origin. Compared on the same origins with the published direct model
and with the 50/50 average of both (the simplest reconciliation; no weights fitted). Also reports the aggregation
error: actual trade growths aggregated the same way vs the actual target growth.
Writes data/<pilot>/reports/hierarchy-latest.json.

Usage: python -m scripts.ops.hierarchy_test
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from scripts.ops.backtest_target import PRIMARY_MODEL, load_predictors
from scripts.ops.project_series import CANDIDATES, OPTIONAL, SPLIT, pooled_prior, quarterly_levels
from src.config import REPO_ROOT, get_settings, primary_target_series_id
from src.quant.forecasting import _quarter_index, _reference_date, diebold_mariano, load_vat_schedule, rolling_origin_backtest, vat_rate
from src.storage.dual_layer_store import DualLayerStore


def trade_weights(store, now, names) -> dict:
    """{trade series id: weight} for trades with an official weight."""
    by_sector = {info["measures"]["sector"]: sid for sid, info in names.items() if info.get("status") == "active"
                 and "STEMW2" in sid and isinstance(info.get("measures"), dict) and "sector" in info["measures"]}
    weights = {}
    for f in store.query_facts(now, "public_as_of"):
        payload = json.loads(f.payload_json or "{}")
        if f.predicate == "COMPONENT_OF" and f.object_id in ("sec_F_ROHBAUARB", "sec_F_AUSBAUARB") and "share_of_total" in payload:
            sid = by_sector.get(f.subject_id.removeprefix("sec_"))
            if sid:
                weights[sid] = float(payload["share_of_total"])
    return weights


def chosen_forecasts(result: dict) -> dict:
    """{origin: forecast growth} of the model chosen on origins before SPLIT (project_series rule)."""
    rows = [r for r in result["predictions"] if all(r["forecasts"][m] is not None for m in CANDIDATES)]
    candidates = CANDIDATES + tuple(m for m in OPTIONAL if rows and all(r["forecasts"].get(m) is not None for r in rows))
    train = [r for r in rows if r["origin"] < SPLIT]
    if len(train) < 8:
        return {}
    model = min(candidates, key=lambda m: np.mean([abs(r["actual"] - r["forecasts"][m]) for r in train]))
    return {r["origin"]: r["forecasts"][model] for r in result["predictions"] if r["forecasts"].get(model) is not None}


def aggregate(growth: dict, level: dict, weights: dict) -> float | None:
    """Growth of the fixed-weight aggregate from trade growths, weighted by weight x level at the base quarter."""
    share = {sid: weights[sid] * level[sid] for sid in growth if sid in level}
    total = sum(share.values())
    return sum(share[s] * growth[s] for s in share) / total if total else None


def main() -> None:
    store = DualLayerStore()
    now = datetime.now(timezone.utc)
    names = json.loads((REPO_ROOT / "config" / "series_names.json").read_text(encoding="utf-8"))["series"]
    report_path = max((Path(get_settings().data_dir) / "reports").glob("backtest-*.json"))
    published = json.loads(report_path.read_text(encoding="utf-8"))["next_release"]["predictions"]
    origins = [datetime.fromisoformat(r["origin"]) for r in published]
    revisions = store.query_observation_revisions(now, "system_as_of")
    weights = trade_weights(store, now, names)
    levels = {sid: quarterly_levels(revisions, sid, None, store) for sid in weights}
    prior = pooled_prior(levels)
    inputs = {n: p for n, p in load_predictors(store, revisions).items() if p.get("in_forecast")}
    forecasts, actual_growth = {}, {}
    for sid, lv in levels.items():
        result = rolling_origin_backtest(lv, origins, fit_start="1995-01-01", target_rule="next_release",
                                         panel_prior=prior, predictors=inputs, predictor_sets={"inputs": list(inputs)})
        forecasts[sid] = chosen_forecasts(result)
        actual_growth[sid] = {r["origin"]: r["actual"] for r in result["predictions"]}
    quarter_level = {sid: {_quarter_index(r["period_start"]): r["value"] for r in lv} for sid, lv in levels.items()}
    schedule = load_vat_schedule()
    rows = []
    for r in published:
        origin = datetime.fromisoformat(r["origin"])
        base, target = _quarter_index(r["denominator_period_start"]), _quarter_index(r["target_period_start"])
        level = {sid: q[base] for sid, q in quarter_level.items() if base in q}
        trade = {sid: f[origin] for sid, f in forecasts.items() if origin in f}
        if len(trade) < 0.9 * len(weights) or r["forecasts"].get(PRIMARY_MODEL) is None:
            continue
        factor = ((1 + vat_rate(schedule, _reference_date(target), known_at=origin))
                  / (1 + vat_rate(schedule, _reference_date(base), known_at=origin)))
        bottom_up = 100 * ((1 + aggregate(trade, level, weights) / 100) * factor - 1)
        realised = {sid: g[origin] for sid, g in actual_growth.items() if origin in g}
        exact = 100 * ((1 + aggregate(realised, level, weights) / 100) * (1 + vat_rate(schedule, _reference_date(target)))
                       / (1 + vat_rate(schedule, _reference_date(base))) - 1)
        direct = r["forecasts"][PRIMARY_MODEL]
        rows.append({"origin": r["origin"], "actual": r["actual"], "direct": direct, "bottom_up": bottom_up,
                     "average": (direct + bottom_up) / 2, "aggregated_actual": exact, "trades": len(trade)})
    actual = [r["actual"] for r in rows]
    mae = lambda key, sample: float(np.mean([abs(x["actual"] - x[key]) for x in sample])) if sample else None
    periods = {"all": rows, "before_2018": [r for r in rows if r["origin"] < SPLIT.isoformat()],
               "from_2018": [r for r in rows if r["origin"] >= SPLIT.isoformat()]}
    summary = {name: {"n": len(s), **{k: mae(k, s) for k in ("direct", "bottom_up", "average")},
                      "aggregation_error": mae("aggregated_actual", s)} for name, s in periods.items()}
    summary["diebold_mariano_vs_direct"] = {k: diebold_mariano(actual, [r[k] for r in rows], [r["direct"] for r in rows], 1)
                                            for k in ("bottom_up", "average")}
    out = Path(get_settings().data_dir) / "reports" / "hierarchy-latest.json"
    out.write_text(json.dumps({"generated_at": now.isoformat(), "backtest_report": report_path.name, "trades": len(weights),
                               "weight_covered": sum(weights.values()), "summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
