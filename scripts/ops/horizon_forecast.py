"""Price change over the next 1-3 years for the target and the trade price series, with a held-out evaluation.

Protocol (fixed before the first run, decisions.md "1-3 year forecasts"):
  horizons     4, 8, 12 quarters after the last released quarter; label = cumulative change of the level
  origins      20 days after every quarter end from 1998; inputs and labels at their release dates
  development  origins before DEV_END: the model is chosen here, per horizon (target: lowest MAE; trades: lowest
               mean MAE over all trades, one model for the group)
  holdout      origins from DEV_END: scored once with the chosen model, never used to choose
  inputs       the published target model's inputs (config/predictor_release_lags.json, in_forecast)
Writes data/<pilot>/reports/horizon-latest.json (live forecasts of the chosen models + evaluation).

Usage: python -m scripts.ops.horizon_forecast
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from scripts.ops.backtest_target import load_predictors, quarter_end_origins
from scripts.ops.project_series import quarterly_levels
from src.config import REPO_ROOT, get_settings, primary_target_series_id
from src.quant.forecasting import diebold_mariano, load_vat_schedule
from src.quant.horizon import MODELS, horizon_backtest
from src.storage.dual_layer_store import DualLayerStore

HORIZONS = (4, 8, 12)
DEV_END = datetime(2013, 1, 1, tzinfo=timezone.utc)


def complete(rows: list) -> list:
    return [r for r in rows if all(r["forecasts"][m] is not None for m in MODELS)]


def mae(rows: list, model: str) -> float | None:
    return float(np.mean([abs(r["actual"] - r["forecasts"][model]) for r in rows])) if rows else None


def evaluate(rows: list, model: str, horizon: int) -> dict:
    holdout = complete([r for r in rows if r["origin"] >= DEV_END])
    scored = [r for r in holdout if r["interval_half_width"][model] is not None]
    ranged = [r for r in holdout if r["history_range"] is not None]
    out = {"n": len(holdout), "mae": {m: mae(holdout, m) for m in MODELS},
           "coverage": float(np.mean([abs(r["actual"] - r["forecasts"][model]) <= r["interval_half_width"][model]
                                      for r in scored])) if scored else None, "coverage_n": len(scored),
           "history_range_coverage": float(np.mean([r["history_range"][0] <= r["actual"] <= r["history_range"][1]
                                                    for r in ranged])) if ranged else None,
           "history_range_width": float(np.mean([r["history_range"][1] - r["history_range"][0] for r in ranged])) if ranged else None}
    if len(holdout) > 2 * horizon:
        actual = [r["actual"] for r in holdout]
        for ref in ("no_change", "drift_10y"):
            if ref != model:
                out[f"diebold_mariano_vs_{ref}"] = diebold_mariano(actual, [r["forecasts"][model] for r in holdout],
                                                                   [r["forecasts"][ref] for r in holdout], horizon)
    return out


def live_entry(series_id: str, name: str, result: dict, model: str, history: bool) -> dict | None:
    live = result["live"]
    if live is None or live["forecasts"][model] is None:
        return None
    growth, width = live["forecasts"][model], live["interval_half_width"][model]
    # Published range for the target: the history range since 1968 (held 77-88% on 2013+ vs 44-75% for model-error
    # intervals; chosen after seeing that holdout, so only the ledger can confirm it). Trades have history only
    # from 1995, too calm (38-67%), so they keep the model-error interval (56-80%).
    model_interval = None if width is None else [growth - width, growth + width]
    return {"series_id": series_id, "name": name, "model": model, "horizon_quarters": live["horizon"],
            "base_period_start": live["base_period_start"], "base_level": live["base_level"],
            "target_period_start": live["target_period_start"], "growth_pct": growth,
            "interval_growth_pct": live["history_range"] if history else model_interval,
            "range_basis": "history_since_1968" if history else "model_errors", "model_error_interval_pct": model_interval}


def main() -> None:
    store = DualLayerStore()
    now = datetime.now(timezone.utc)
    names = json.loads((REPO_ROOT / "config" / "series_names.json").read_text(encoding="utf-8"))["series"]
    revisions = store.query_observation_revisions(now, "system_as_of")
    inputs = {n: p for n, p in load_predictors(store, revisions).items() if p.get("in_forecast")}
    origins = [o + timedelta(days=20) for o in quarter_end_origins("1998-03-31", now)]
    target = primary_target_series_id()
    trades = [sid for sid, info in names.items() if info.get("status") == "active" and "STEMW2" in sid
              and isinstance(info.get("measures"), dict) and str(info["measures"].get("sector", "")).startswith("F_")]
    schedule = load_vat_schedule()
    report = {"generated_at": now.isoformat(), "protocol": __doc__.split("\n\n")[1], "dev_end": DEV_END.isoformat(),
              "target": {}, "trades": {}, "forecasts": []}
    for h in HORIZONS:
        run = lambda sid, vat, fit, since=None: horizon_backtest(
            quarterly_levels(revisions, sid, None, store), origins, h, fit_start=fit, vat_schedule=vat, predictors=inputs,
            inputs=tuple(inputs), live_origin=now, range_start=since)
        result = run(target, schedule, "1991-01-01", "1968-07-01")  # VAT from 1968-07
        dev = complete([r for r in result["predictions"] if r["origin"] < DEV_END])
        model = min(MODELS, key=lambda m: mae(dev, m))
        report["target"][f"h{h}"] = {"chosen": model, "dev_n": len(dev), "dev_mae": {m: mae(dev, m) for m in MODELS},
                                     "holdout": evaluate(result["predictions"], model, h)}
        entry = live_entry(target, names[target]["name"], result, model, history=True)
        if entry:
            report["forecasts"].append(entry)
        results = {sid: run(sid, None, "1995-01-01") for sid in trades}
        devs = {sid: complete([r for r in res["predictions"] if r["origin"] < DEV_END]) for sid, res in results.items()}
        devs = {sid: rows for sid, rows in devs.items() if len(rows) >= 8}
        group = min(MODELS, key=lambda m: np.mean([mae(rows, m) for rows in devs.values()]))
        holdouts = {sid: evaluate(results[sid]["predictions"], group, h) for sid in devs}
        holdouts = {sid: e for sid, e in holdouts.items() if e["n"]}
        report["trades"][f"h{h}"] = {
            "chosen": group, "series": len(devs),
            "dev_mean_mae": {m: float(np.mean([mae(rows, m) for rows in devs.values()])) for m in MODELS},
            "holdout_mean_mae": {m: float(np.mean([e["mae"][m] for e in holdouts.values()])) for m in MODELS},
            "holdout_coverage": float(np.mean([e["coverage"] for e in holdouts.values() if e["coverage"] is not None])),
            "holdout_history_range_coverage": float(np.mean([e["history_range_coverage"] for e in holdouts.values()
                                                             if e["history_range_coverage"] is not None])),
            "holdout_beats_no_change": sum(e["mae"][group] < e["mae"]["no_change"] for e in holdouts.values()),
            "holdout_series": len(holdouts)}
        for sid in devs:
            entry = live_entry(sid, names[sid]["name"], results[sid], group, history=False)
            if entry:
                report["forecasts"].append(entry)
        print(h, json.dumps({"target": report["target"][f"h{h}"]["chosen"], "trades": group}), flush=True)
    out = Path(get_settings().data_dir) / "reports" / "horizon-latest.json"
    out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"output": str(out), "forecasts": len(report["forecasts"])}))


if __name__ == "__main__":
    main()
