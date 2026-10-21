"""Next-quarter projections for every linked price series (materials, energy, building costs, wages).

Each series gets the same rolling-origin test as the target, on data public at each past origin: the model is
chosen on origins before SPLIT and scored on later ones, so the reported error is not flattered by the choice.
Candidates include a seasonal AR partially pooled over the other series of the same metric (trade output prices,
material producer prices), using only their quarters public at the origin.
Writes data/<pilot>/reports/projections-latest.json, which the dashboard reads.

Usage: python -m scripts.ops.project_series [--first-origin 2010-03-31]
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from scripts.ops.backtest_target import BERLIN, quarter_end_origins
from src.config import REPO_ROOT, get_settings, primary_target_series_id
from src.quant.forecasting import INTERVAL_METHODS, rolling_origin_backtest
from src.storage.dual_layer_store import DualLayerStore

from src.storage.dashboard_data import PRICE_METRICS
from src.utils.taxonomy import is_german_series
CANDIDATES = ("ar4_seasonal", "ar4", "mean_4q", "no_change", "ar4_seasonal_pooled")
SPLIT = datetime(2018, 1, 1, tzinfo=timezone.utc)
# Adaptive conformal: split intervals covered 78% on 2018+ across these series, ACI 83% (decisions.md).
INTERVAL_METHOD = "aci"


def quarterly_levels(revisions: list, series_id: str, rule_lag_days, store) -> list:
    """Earliest-available value per period; monthly values become complete quarterly means."""
    cells = {}
    for o in revisions:
        if o.series_id != series_id or o.value is None or o.is_retracted:
            continue
        if rule_lag_days is None:
            when = store._available(o)
        else:  # assumption from config/predictor_release_lags.json: published lag_days after the period end
            day = datetime.fromisoformat(o.period_end_exclusive).date() + timedelta(days=rule_lag_days)
            when = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=BERLIN).astimezone(timezone.utc)
        if o.period_start not in cells or when < cells[o.period_start][1]:
            cells[o.period_start] = (o.value, when)
    quarters = {}
    for period, (value, when) in cells.items():
        quarter = f"{period[:4]}-{(int(period[5:7]) - 1) // 3 * 3 + 1:02d}-01"
        quarters.setdefault(quarter, []).append((value, when))
    monthly = len(cells) > 1.5 * len(quarters)
    return [{"period_start": q, "value": float(np.mean([v for v, _ in parts])), "available_at": max(w for _, w in parts)}
            for q, parts in sorted(quarters.items()) if not monthly or len(parts) == 3]


def pooled_prior(series_levels: dict, order: int = 4):
    """callable(origin) -> seasonal-AR coefficients pooled over these series (one intercept each), fitted on the
    quarters whose levels were all public at the origin. Same layout as forecasting._sar_forecast."""
    growth = {}
    for sid, levels in series_levels.items():
        lv = {int(r["period_start"][:4]) * 4 + (int(r["period_start"][5:7]) - 1) // 3: (r["value"], r["available_at"]) for r in levels}
        growth[sid] = {q: (100 * (v / lv[q - 1][0] - 1), max(when, lv[q - 1][1])) for q, (v, when) in lv.items()
                       if q - 1 in lv and lv[q - 1][0] and abs(v / lv[q - 1][0] - 1) < 0.5}
    ids, cache = sorted(growth), {}

    def prior(origin):
        key = origin.date()
        if key not in cache:
            x, y = [], []
            for i, sid in enumerate(ids):
                g = {q: v for q, (v, when) in growth[sid].items() if when <= origin and q >= 1995 * 4}
                for q in g:
                    lags = [g.get(q - k) for k in range(1, order + 1)]
                    if None not in lags:
                        x.append([1.0] + lags + [float(q % 4 == k) for k in (1, 2, 3)] + [float(j == i) for j in range(1, len(ids))])
                        y.append(g[q])
            cache[key] = np.linalg.lstsq(np.array(x), np.array(y), rcond=None)[0][:order + 4] if len(y) > 200 else None
        return cache[key]
    return prior


def project(levels: list, origins: list, now: datetime, prior=None) -> dict | None:
    result = rolling_origin_backtest(levels, origins, fit_start="1995-01-01", target_rule="next_release", live_origin=now,
                                     panel_prior=prior, interval_method=INTERVAL_METHOD)
    rows = [r for r in result["predictions"] if all(r["forecasts"][m] is not None for m in CANDIDATES[:-1])]
    # The pooled model competes only where a pool exists at every origin (singleton metrics have none).
    candidates = CANDIDATES if rows and all(r["forecasts"].get(CANDIDATES[-1]) is not None for r in rows) else CANDIDATES[:-1]
    train, test = [r for r in rows if r["origin"] < SPLIT], [r for r in rows if r["origin"] >= SPLIT]
    live = result["live"]
    if len(train) < 8 or len(test) < 8 or live is None:
        return None
    mae = lambda model, sample: float(np.mean([abs(r["actual"] - r["forecasts"][model]) for r in sample]))
    model = min(candidates, key=lambda m: mae(m, train))
    width = live["interval_half_width"].get(model)
    scored = [r for r in test if r["interval_half_width"].get(model) is not None]
    growth = live["forecasts"].get(model)
    return {"model": model, "target_period_start": live["target_period_start"],
            "last_period_start": live["last_observed_period_start"], "growth_pct": growth,
            "interval_growth_pct": None if growth is None or width is None else [growth - width, growth + width],
            "test": {"from": SPLIT.date().isoformat(), "n": len(test), "mae": mae(model, test),
                     "mae_no_change": mae("no_change", test), "mae_mean_4q": mae("mean_4q", test),
                     "mae_by_model": {m: mae(m, test) for m in candidates},
                     "coverage": float(np.mean([abs(r["actual"] - r["forecasts"][model]) <= r["interval_half_width"][model]
                                                for r in scored])) if scored else None,
                     "coverage_by_method": {method: coverage(test, model, method) for method in INTERVAL_METHODS}}}


def coverage(rows: list, model: str, method: str) -> dict:
    scored = [r for r in rows if r["interval_half_width_by"][method].get(model) is not None]
    widths = [r["interval_half_width_by"][method][model] for r in scored]
    return {"coverage": float(np.mean([abs(r["actual"] - r["forecasts"][model]) <= w for r, w in zip(scored, widths)])) if scored else None,
            "mean_half_width": float(np.mean(widths)) if scored else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--first-origin", default="2008-03-31")
    args = parser.parse_args()
    store = DualLayerStore()
    now = datetime.now(timezone.utc)
    names = json.loads((REPO_ROOT / "config" / "series_names.json").read_text(encoding="utf-8"))["series"]
    rules = {spec["series_id"]: spec["lag_days"] for spec in
             json.loads((REPO_ROOT / "config" / "predictor_release_lags.json").read_text(encoding="utf-8"))["predictors"].values()
             if spec.get("availability") == "rule" and "series_id" in spec}
    revisions = store.query_observation_revisions(now, "system_as_of")
    origins = [o + timedelta(days=20) for o in quarter_end_origins(args.first_origin, now)]
    eligible = {}
    for series_id, info in names.items():
        measures = info.get("measures") or {}
        metric = measures.get("metric") if isinstance(measures, dict) else None  # lists: policy numbers, not prices
        if (info["status"] != "active" or metric not in PRICE_METRICS or series_id == primary_target_series_id()
                or not is_german_series(series_id)):
            continue
        levels = quarterly_levels(revisions, series_id, rules.get(series_id), store)
        if levels:
            eligible[series_id] = (metric, levels)
    # Partial pooling within one metric (trade output prices, material producer prices, ...).
    priors = {m: pooled_prior({sid: lv for sid, (metric, lv) in eligible.items() if metric == m})
              for m in {metric for metric, _ in eligible.values()}}
    projections = []
    for series_id, (metric, levels) in eligible.items():
        info = names[series_id]
        outcome = project(levels, origins, now, priors[metric])
        if outcome:
            projections.append({"series_id": series_id, "name": info["name"], "measures": info["measures"],
                                "dating": "rule" if series_id in rules else "evidence",
                                "last_level": next(l["value"] for l in levels if l["period_start"] == outcome["last_period_start"]),
                                **outcome})
    out = Path(get_settings().data_dir) / "reports" / "projections-latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": now.isoformat(), "projections": projections,
                               "notes": ["Growth is next quarter's average level over the latest complete quarter, in percent.",
                                         "Model chosen on origins before 2018, scored on 2018 onward; 90% adaptive conformal intervals (ACI).",
                                         "Series dated by rule assume a fixed publication lag (see config/predictor_release_lags.json)."]},
                              indent=2), encoding="utf-8")
    beat = sum(p["test"]["mae"] < p["test"]["mae_no_change"] for p in projections)
    print(json.dumps({"output": str(out), "series": len(projections), "beat_no_change": beat}))


if __name__ == "__main__":
    main()
