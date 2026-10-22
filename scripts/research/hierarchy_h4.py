"""Research (2026-10-22, decisions.md "Index hierarchy"): bottom-up 1-year forecast from trades (direct_inputs, the trades' group choice at h4) vs the target's models."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np

from scripts.ops.backtest_target import load_predictors, quarter_end_origins
from scripts.ops.hierarchy_test import aggregate, trade_weights
from scripts.ops.horizon_forecast import DEV_END
from scripts.ops.project_series import quarterly_levels
from src.config import REPO_ROOT, primary_target_series_id
from src.quant.forecasting import _quarter_index, _reference_date, load_vat_schedule, vat_rate
from src.quant.horizon import horizon_backtest
from src.storage.dual_layer_store import DualLayerStore

store = DualLayerStore(); now = datetime.now(timezone.utc)
names = json.loads((REPO_ROOT / "config" / "series_names.json").read_text(encoding="utf-8"))["series"]
revisions = store.query_observation_revisions(now, "system_as_of")
weights = trade_weights(store, now, names)
inputs = {n: p for n, p in load_predictors(store, revisions).items() if p.get("in_forecast")}
origins = [o + timedelta(days=20) for o in quarter_end_origins("1998-03-31", now)]
schedule = load_vat_schedule()
H = 4
target = {r["origin"]: r for r in horizon_backtest(quarterly_levels(revisions, primary_target_series_id(), None, store), origins, H,
          fit_start="1991-01-01", vat_schedule=schedule, predictors=inputs, inputs=tuple(inputs))["predictions"]}
trades = {}
levels = {}
for sid in weights:
    lv = quarterly_levels(revisions, sid, None, store)
    levels[sid] = {_quarter_index(r["period_start"]): r["value"] for r in lv}
    trades[sid] = {r["origin"]: r for r in horizon_backtest(lv, origins, H, fit_start="1995-01-01", predictors=inputs, inputs=tuple(inputs))["predictions"]}
for model in ("direct_inputs", "drift_10y"):
    rows = []
    for origin, t in target.items():
        base = _quarter_index(t["base_period_start"])
        g = {sid: tr[origin]["forecasts"][model] for sid, tr in trades.items() if origin in tr and tr[origin]["forecasts"][model] is not None}
        if len(g) < 0.9 * len(weights) or None in (t["forecasts"]["direct_inputs"], t["forecasts"]["drift_10y"], t["forecasts"]["sar"]):
            continue
        factor = (1 + vat_rate(schedule, _reference_date(base + H), known_at=origin)) / (1 + vat_rate(schedule, _reference_date(base), known_at=origin))
        bu = 100 * ((1 + aggregate(g, {s: levels[s][base] for s in g if base in levels[s]}, weights) / 100) * factor - 1)
        rows.append({"origin": origin, "actual": t["actual"], "bottom_up": bu, **{m: t["forecasts"][m] for m in ("direct_inputs", "drift_10y", "sar")}})
    for label, sample in (("dev", [r for r in rows if r["origin"] < DEV_END]), ("holdout", [r for r in rows if r["origin"] >= DEV_END])):
        print(model, label, len(sample), {k: round(float(np.mean([abs(r["actual"] - r[k]) for r in sample])), 2)
                                          for k in ("bottom_up", "direct_inputs", "drift_10y", "sar")})
