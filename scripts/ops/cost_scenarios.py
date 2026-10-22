"""1-3 year price scenarios for the target from the cost build-up (src.quant.cost_model), with their track record.

Evaluation on the origins of scripts.ops.horizon_forecast (dev before 2013, holdout from 2013):
  central       price change with the median material scenario and trend labour (a forecast)
  low-high      share of outcomes inside the 10th-90th material scenarios (nominal 80%)
  inputs        price change with materials forecast from world prices, energy, surveys and momentum (equal-weight
                average of one regression per input set, src.quant.cost_model) and labour on trend
  conditional   price change implied by the costs that actually followed (how good the model is when costs are known)
  drift_10y     the target's own 10-year trend, for comparison
Writes data/<pilot>/reports/cost-scenarios-latest.json.

Usage: python -m scripts.ops.cost_scenarios
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from scripts.ops.backtest_target import BERLIN, load_predictors, quarter_end_origins
from scripts.ops.horizon_forecast import DEV_END, HORIZONS
from scripts.ops.project_series import quarterly_levels
from src.config import REPO_ROOT, get_settings, primary_target_series_id
from src.quant.cost_model import SCENARIOS, WEIGHT, cost_scenarios
from src.quant.forecasting import load_vat_schedule
from src.quant.horizon import horizon_backtest
from src.storage.dual_layer_store import DualLayerStore

WORLD_LAG_DAYS = 5  # Pink Sheet and ECB monthly rates: published in the first days of the next month (rule)
MATERIAL_SETS = {"momentum": [], "world": ["brent_eur", "metals_eur"], "energy": ["energy"],
                 "survey": ["price_expectations", "material_shortage"]}
MATERIAL = "61261-0014|DINSG=DG|KOSAT1=KOST011|KOS022|2021=100"
LABOUR = "61261-0014|DINSG=DG|KOSAT1=KOST012|KOS022|2021=100"


def material_inputs(store, revisions, now) -> dict:
    """Monthly inputs for the material cost forecast: euro world prices (rule-dated), energy producer prices and the
    builders' surveys as configured in config/predictor_release_lags.json."""
    def monthly(sid):
        return {r["period_start"]: (r["value"], r["period_end_exclusive"]) for r in
                store.query_observations(now, "public_as_of", series_ids=[sid]).to_pylist() if r["value"]}

    def rule_dated(values):
        out = []
        for start, (value, end) in sorted(values.items()):
            day = datetime.fromisoformat(end).date() + timedelta(days=WORLD_LAG_DAYS)
            out.append({"period_start": start, "value": value,
                        "available_at": datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=BERLIN).astimezone(timezone.utc)})
        return out
    fx = monthly("EXR.M.USD.EUR.SP00.A")
    euro = lambda sid: {m: (v / fx[m][0], end) for m, (v, end) in monthly(sid).items() if m in fx}
    metals = [euro(f"WB_PINK|{name}") for name in ("copper", "aluminium", "zinc")]
    metal = {m: (sum(x[m][0] for x in metals) / 3, metals[0][m][1]) for m in metals[0] if all(m in x for x in metals)}
    configured = load_predictors(store, revisions)
    return {"brent_eur": {"frequency": "MONTHLY", "transform": "yoy_3m_sum", "levels": rule_dated(euro("WB_PINK|brent"))},
            "metals_eur": {"frequency": "MONTHLY", "transform": "yoy_3m_sum", "levels": rule_dated(metal)},
            "energy": {**configured["energy"], "transform": "yoy_3m_sum"},
            "price_expectations": configured["price_expectations"], "material_shortage": configured["material_shortage"]}


def summarise(rows: list) -> dict:
    if not rows:
        return {"n": 0}
    err = lambda key: float(np.mean([abs(r["actual"] - r[key]) for r in rows]))
    return {"n": len(rows), "mae_central": err("central"), "mae_inputs": err("inputs"), "mae_drift_10y": err("drift_10y"),
            "mae_conditional": err("conditional"),
            "low_high_coverage": float(np.mean([r["low"] <= r["actual"] <= r["high"] for r in rows])),
            "mean_low_high_width": float(np.mean([r["high"] - r["low"] for r in rows]))}


def main() -> None:
    store = DualLayerStore()
    now = datetime.now(timezone.utc)
    revisions = store.query_observation_revisions(now, "system_as_of")
    lags = {spec["series_id"]: spec["lag_days"] for spec in
            json.loads((REPO_ROOT / "config" / "predictor_release_lags.json").read_text(encoding="utf-8"))["predictors"].values()
            if spec.get("availability") == "rule" and "series_id" in spec}
    target = quarterly_levels(revisions, primary_target_series_id(), None, store)
    material = quarterly_levels(revisions, MATERIAL, lags[MATERIAL], store)
    labour = quarterly_levels(revisions, LABOUR, lags[LABOUR], store)
    origins = [o + timedelta(days=20) for o in quarter_end_origins("1998-03-31", now)]
    inputs = material_inputs(store, revisions, now)
    schedule = load_vat_schedule()
    report = {"generated_at": now.isoformat(), "weight_material": WEIGHT, "scenario_percentiles": SCENARIOS,
              "method": __doc__.split("\n\n")[1], "evaluation": {}, "forecasts": []}
    for h in HORIZONS:
        result = cost_scenarios(target, material, labour, origins, h, schedule, live_origin=now,
                                material_inputs=inputs, material_sets=MATERIAL_SETS)
        trend = {r["origin"]: r["forecasts"]["drift_10y"] for r in
                 horizon_backtest(target, origins, h, fit_start="1991-01-01", vat_schedule=schedule)["predictions"]}
        rows = [{"origin": r["origin"], "actual": r["actual"], "conditional": r["conditional"], "inputs": r["inputs"], **r["scenarios"],
                 "drift_10y": trend.get(r["origin"])} for r in result["predictions"]]
        rows = [r for r in rows if None not in (r["conditional"], r["drift_10y"], r["inputs"])]
        report["evaluation"][f"h{h}"] = {"dev": summarise([r for r in rows if r["origin"] < DEV_END]),
                                         "holdout": summarise([r for r in rows if r["origin"] >= DEV_END])}
        live = result["live"]
        if live:
            base = next(r["value"] for r in target if r["period_start"] == live["base_period_start"])
            report["forecasts"].append({
                "series_id": primary_target_series_id(), "model": "cost_central", "horizon_quarters": h,
                "base_period_start": live["base_period_start"], "base_level": base,
                "target_period_start": live["target_period_start"], "growth_pct": live["scenarios"]["central"],
                "interval_growth_pct": [live["scenarios"]["low"], live["scenarios"]["high"]], "scenarios": live["scenarios"],
                "labour_change_pct": 100 * (np.exp(live["labour_change_log"] / 100) - 1),
                "material_change_pct": {k: 100 * (np.exp(v / 100) - 1) for k, v in live["material_change_log"].items()}})
            if live.get("inputs") is not None:
                report["forecasts"].append({**{k: report["forecasts"][-1][k] for k in ("series_id", "horizon_quarters", "base_period_start",
                                                                                     "base_level", "target_period_start")},
                                            "model": "cost_inputs", "growth_pct": live["inputs"], "interval_growth_pct": None})
    out = Path(get_settings().data_dir) / "reports" / "cost-scenarios-latest.json"
    out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps(report["evaluation"], indent=1))
    for f in [f for f in report["forecasts"] if "scenarios" in f]:
        print(f["horizon_quarters"], {k: round(v, 1) for k, v in f["scenarios"].items()}, "labour", round(f["labour_change_pct"], 1),
              "materials", {k: round(v, 1) for k, v in f["material_change_pct"].items()})


if __name__ == "__main__":
    main()
