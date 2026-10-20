"""Next-quarter projections for every linked price series (materials, energy, building costs, wages).

Each series gets the same rolling-origin test as the target, on data public at each past origin: the model is
chosen on origins before SPLIT and scored on later ones, so the reported error is not flattered by the choice.
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
from src.quant.forecasting import rolling_origin_backtest
from src.storage.dual_layer_store import DualLayerStore

from src.storage.dashboard_data import PRICE_METRICS
CANDIDATES = ("ar4_seasonal", "ar4", "mean_4q", "no_change")
SPLIT = datetime(2018, 1, 1, tzinfo=timezone.utc)


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


def project(levels: list, origins: list, now: datetime) -> dict | None:
    result = rolling_origin_backtest(levels, origins, fit_start="1995-01-01", target_rule="next_release", live_origin=now)
    rows = [r for r in result["predictions"] if all(r["forecasts"][m] is not None for m in CANDIDATES)]
    train, test = [r for r in rows if r["origin"] < SPLIT], [r for r in rows if r["origin"] >= SPLIT]
    live = result["live"]
    if len(train) < 8 or len(test) < 8 or live is None:
        return None
    mae = lambda model, sample: float(np.mean([abs(r["actual"] - r["forecasts"][model]) for r in sample]))
    model = min(CANDIDATES, key=lambda m: mae(m, train))
    width = live["interval_half_width"].get(model)
    scored = [r for r in test if r["interval_half_width"].get(model) is not None]
    growth = live["forecasts"].get(model)
    return {"model": model, "target_period_start": live["target_period_start"],
            "last_period_start": live["last_observed_period_start"], "growth_pct": growth,
            "interval_growth_pct": None if growth is None or width is None else [growth - width, growth + width],
            "test": {"from": SPLIT.date().isoformat(), "n": len(test), "mae": mae(model, test),
                     "mae_no_change": mae("no_change", test), "mae_mean_4q": mae("mean_4q", test),
                     "coverage": float(np.mean([abs(r["actual"] - r["forecasts"][model]) <= r["interval_half_width"][model]
                                                for r in scored])) if scored else None}}


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
    projections = []
    for series_id, info in names.items():
        metric = (info.get("measures") or {}).get("metric")
        if info["status"] != "active" or metric not in PRICE_METRICS or series_id == primary_target_series_id():
            continue
        levels = quarterly_levels(revisions, series_id, rules.get(series_id), store)
        outcome = project(levels, origins, now) if levels else None
        if outcome:
            projections.append({"series_id": series_id, "name": info["name"], "measures": info["measures"],
                                "dating": "rule" if series_id in rules else "evidence",
                                "last_level": next(l["value"] for l in levels if l["period_start"] == outcome["last_period_start"]),
                                **outcome})
    out = Path(get_settings().data_dir) / "reports" / "projections-latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": now.isoformat(), "projections": projections,
                               "notes": ["Growth is next quarter's average level over the latest complete quarter, in percent.",
                                         "Model chosen on origins before 2018, scored on 2018 onward; 90% split-conformal intervals.",
                                         "Series dated by rule assume a fixed publication lag (see config/predictor_release_lags.json)."]},
                              indent=2), encoding="utf-8")
    beat = sum(p["test"]["mae"] < p["test"]["mae_no_change"] for p in projections)
    print(json.dumps({"output": str(out), "series": len(projections), "beat_no_change": beat}))


if __name__ == "__main__":
    main()
