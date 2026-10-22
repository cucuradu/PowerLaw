"""Score published outside forecasts (config/external_forecasts.json) against ours made on the same day.

For each outside forecast of the annual-average price change in year Y, our models forecast the same quantity
from the data public on its publication date: the target's quarterly path (src.quant.horizon, 1-9 quarters
ahead) completes the quarters not yet released, and Y's mean level is compared with Y-1's. The outcome is the
annual-average change of the target index (Destatis 61261-0002, residential buildings incl. VAT) as released
now. Caveat: DIW forecasts the price deflator of construction volume (incl. maintenance), not this index.
Writes data/<pilot>/reports/external-benchmark-latest.json.

Usage: python -m scripts.ops.benchmark_external
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from scripts.ops.backtest_target import load_predictors
from scripts.ops.project_series import quarterly_levels
from src.config import REPO_ROOT, get_settings, primary_target_series_id
from src.quant.forecasting import _quarter_index, load_vat_schedule
from src.quant.horizon import MODELS, horizon_backtest

COMPARED = ("drift_10y", "sar", "direct_inputs")


def annual_change(levels: dict, year: int) -> float | None:
    """Percent change of the mean level of `year` over `year - 1`; `levels` {quarter index: level}."""
    this, last = [levels.get(year * 4 + k) for k in range(4)], [levels.get((year - 1) * 4 + k) for k in range(4)]
    return None if None in this + last else 100 * (sum(this) / sum(last) - 1)


def our_paths(levels: list, origin: datetime, inputs: dict, schedule) -> dict:
    """{model: {quarter index: level}}: released levels at the origin plus each model's forecasts 1-9 quarters on."""
    known = {_quarter_index(r["period_start"]): r["value"] for r in levels if r["available_at"] <= origin}
    paths = {m: dict(known) for m in COMPARED}
    for h in range(1, 10):  # up to Q4 of next year when Q4 of last year is not out yet
        live = horizon_backtest(levels, [], h, fit_start="1991-01-01", vat_schedule=schedule, predictors=inputs,
                                inputs=tuple(inputs), live_origin=origin)["live"]
        target, base = _quarter_index(live["target_period_start"]), live["base_level"]
        for m in COMPARED:
            if live["forecasts"][m] is not None:
                paths[m][target] = base * (1 + live["forecasts"][m] / 100)
    return paths


def main() -> None:
    from src.storage.dual_layer_store import DualLayerStore
    store = DualLayerStore()
    now = datetime.now(timezone.utc)
    config = json.loads((REPO_ROOT / "config" / "external_forecasts.json").read_text(encoding="utf-8"))
    revisions = store.query_observation_revisions(now, "system_as_of")
    levels = quarterly_levels(revisions, primary_target_series_id(), None, store)
    inputs = {n: p for n, p in load_predictors(store, revisions).items() if p.get("in_forecast")}
    schedule = load_vat_schedule()
    latest = {_quarter_index(r["period_start"]): r["value"] for r in levels}  # outcome: values as released now
    rows = []
    for entry in config["forecasts"]:
        origin = datetime.fromisoformat(entry["published_on"]).replace(tzinfo=timezone.utc)
        paths = our_paths(levels, origin, inputs, schedule)
        for year, value in entry["values"].items():
            year = int(year)
            rows.append({"edition": entry["edition"], "published_on": entry["published_on"], "variable": entry["variable"],
                         "check": entry["check"], "year": year, "years_ahead": year - origin.year, "outside": value,
                         "ours": {m: annual_change(paths[m], year) for m in COMPARED},
                         "actual": annual_change(latest, year)})
    scored = [r for r in rows if r["actual"] is not None and r["check"] != "unreconciled"
              and all(v is not None for v in r["ours"].values())]
    summary = {}
    for ahead in sorted({r["years_ahead"] for r in scored}):
        sample = [r for r in scored if r["years_ahead"] == ahead]
        summary[f"year_{ahead}"] = {"n": len(sample), "mae_outside": float(np.mean([abs(r["actual"] - r["outside"]) for r in sample])),
                                    **{f"mae_{m}": float(np.mean([abs(r["actual"] - r["ours"][m]) for r in sample])) for m in COMPARED}}
    out = Path(get_settings().data_dir) / "reports" / "external-benchmark-latest.json"
    out.write_text(json.dumps({"generated_at": now.isoformat(), "method": __doc__.split("\n\n")[1].replace("\n", " "),
                               "summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    for r in rows:
        print(r["edition"], r["variable"], r["year"], "DIW", r["outside"], {m: None if v is None else round(v, 1) for m, v in r["ours"].items()},
              "actual", None if r["actual"] is None else round(r["actual"], 1), r["check"])


if __name__ == "__main__":
    main()
