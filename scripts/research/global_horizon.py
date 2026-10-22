"""Research (2026-10-22, decisions.md "EU-wide 1-3 year model"): does a 1-3 year model fitted across EU countries beat Germany-only models for the German target?

Global direct model: h-quarter log growth on [1, last-year log growth, last-10-year mean annual log growth], fitted on
every country-quarter whose h-quarter outcome was public at the origin (other countries: quarter end + 100 days).
Germany's target enters ex VAT; the VAT step known at the origin is applied after. Compared on the same German
origins with drift_10y and direct_ar from src.quant.horizon. Dev < 2013, holdout >= 2013.
"""
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np

from scripts.ops.backtest_target import PANEL_LAG_DAYS, quarter_end_origins
from scripts.ops.project_series import quarterly_levels
from src.config import load_data_sources_registry, primary_target_series_id
from src.quant.forecasting import _quarter_index, _reference_date, load_vat_schedule, vat_rate
from src.quant.horizon import horizon_backtest
from src.storage.dual_layer_store import DualLayerStore

store = DualLayerStore()
now = datetime.now(timezone.utc)
context = load_data_sources_registry()["sources"]["EUROSTAT_STS"]["context_series"]
ids = {s["provider_series_key"]: s["dimensions"]["geo"] for n, s in context.items()
       if n.startswith("eu_new_home_prices_") and s["dimensions"]["geo"] != "DE"}
panel = defaultdict(dict)  # geo -> {q: (log level, public)}
for row in store.query_observations(now, "public_as_of", series_ids=list(ids)).to_pylist():
    if row["value"]:
        q = _quarter_index(row["period_start"])
        end = datetime(q // 4 + (q % 4 == 3), (q % 4 + 1) % 4 * 3 + 1, 1, tzinfo=timezone.utc)
        panel[ids[row["series_id"]]][q] = (100 * math.log(row["value"]), end + timedelta(days=PANEL_LAG_DAYS))
revisions = store.query_observation_revisions(now, "system_as_of")
target = quarterly_levels(revisions, primary_target_series_id(), None, store)
schedule = load_vat_schedule()
de = {}
for r in target:
    q = _quarter_index(r["period_start"])
    if q >= 1968 * 4 + 2:
        de[q] = (100 * math.log(r["value"] / (1 + vat_rate(schedule, _reference_date(q)))), r["available_at"])
print("countries", len(panel), "first quarters", sorted(min(v) // 4 for v in panel.values())[:5], "...")


def features(logs, r):
    if not all(q in logs for q in (r, r - 4, r - 40)):
        return None
    return [1.0, logs[r] - logs[r - 4], (logs[r] - logs[r - 40]) / 10]


origins = [o + timedelta(days=20) for o in quarter_end_origins("1998-03-31", now)]
for h in (4, 8, 12):
    local = {r["origin"]: r for r in horizon_backtest(target, origins, h, fit_start="1991-01-01", vat_schedule=schedule)["predictions"]}
    rows = []
    for origin in origins:
        if origin not in local:
            continue
        x, y = [], []
        for geo, series in list(panel.items()) + [("DE", de)]:
            logs = {q: v for q, (v, when) in series.items() if when <= origin}
            for r in logs:
                f = features(logs, r)
                if f and r + h in logs:
                    x.append(f)
                    y.append(logs[r + h] - logs[r])
        logs_de = {q: v for q, (v, when) in de.items() if when <= origin}
        last = max(logs_de)
        f = features(logs_de, last)
        if f is None or len(y) < 100:
            continue
        coef = np.linalg.lstsq(np.array(x), np.array(y), rcond=None)[0]
        factor = (1 + vat_rate(schedule, _reference_date(last + h), known_at=origin)) / (1 + vat_rate(schedule, _reference_date(last), known_at=origin))
        g = 100 * (math.exp(float(np.dot(coef, f)) / 100) * factor - 1)
        row = local[origin]
        rows.append({"origin": origin, "actual": row["actual"], "global": g, "n_fit": len(y), "coef": coef,
                     **{m: row["forecasts"][m] for m in ("drift_10y", "direct_ar", "sar")}})
    for label, sample in (("dev", [r for r in rows if r["origin"].year < 2013]), ("holdout", [r for r in rows if r["origin"].year >= 2013])):
        sample = [r for r in sample if all(r[m] is not None for m in ("drift_10y", "direct_ar", "sar"))]
        maes = {m: np.mean([abs(r["actual"] - r[m]) for r in sample]) for m in ("global", "drift_10y", "direct_ar", "sar")}
        print(h, label, len(sample), {m: round(v, 2) for m, v in maes.items()}, "fit rows", sample[-1]["n_fit"] if sample else None,
              "coef", np.round(sample[-1]["coef"], 2) if sample else None)
