"""Research (2026-10-22): can material building costs be forecast 1-3 years ahead? (decisions.md "Material costs")

Label: h-quarter log change of the material building-cost index (Destatis 61261-0014 KOST011, rule-dated 120 days or
fetch time) from the last quarter public at the origin. Direct OLS on rows whose outcome was public at the origin;
monthly inputs enter at the release-aligned lag of the origin (forecasting._feature_at_origin):
  drift       10-year mean change (no fit)
  momentum    [1, last-year change]
  + world     euro prices of Brent and of metals (mean of copper, aluminium, zinc), yoy of 3-month sums
  + energy    German energy producer prices, yoy of 3-month sums (Bundesbank-dated)
  + survey    builders' price expectations and material shortage, 3-month means
  + all       every input above
  average     equal-weight mean of momentum, world, energy and survey (no selection)
Origins 20 days after each quarter end from 2004; dev before 2013, holdout from 2013. Prints MAE per horizon.

Usage: python -m scripts.research.material_cost_forecast
"""
import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np

from scripts.ops.backtest_target import quarter_end_origins
from scripts.ops.cost_scenarios import material_inputs
from scripts.ops.project_series import quarterly_levels
from src.config import REPO_ROOT
from src.quant.forecasting import _feature_at_origin, _quarter_index
from src.storage.dual_layer_store import DualLayerStore

MATERIAL = "61261-0014|DINSG=DG|KOSAT1=KOST011|KOS022|2021=100"
def main() -> None:
    store = DualLayerStore()
    now = datetime.now(timezone.utc)
    revisions = store.query_observation_revisions(now, "system_as_of")
    lag = next(p["lag_days"] for p in json.loads((REPO_ROOT / "config" / "predictor_release_lags.json").read_text(encoding="utf-8"))["predictors"].values()
               if p.get("series_id") == MATERIAL)
    material = {_quarter_index(r["period_start"]): (100 * math.log(r["value"]), r["available_at"])
                for r in quarterly_levels(revisions, MATERIAL, lag, store)}
    inputs = material_inputs(store, revisions, now)
    sets = {"momentum": [], "world": ["brent_eur", "metals_eur"], "energy": ["energy"],
            "survey": ["price_expectations", "material_shortage"], "all": list(inputs)}
    origins = [o + timedelta(days=20) for o in quarter_end_origins("2004-03-31", now)]
    for h in (4, 8, 12):
        rows = []
        for origin in origins:
            m = {q: v for q, (v, when) in material.items() if when <= origin}
            last = max(m)
            if last + h not in material or last - 40 not in m:
                continue
            origin_quarter = origin.year * 4 + (origin.month - 1) // 3
            d = origin_quarter - last
            feats = {n: _feature_at_origin(p, origin, origin_quarter) for n, p in inputs.items()}
            row = {"origin": origin, "actual": material[last + h][0] - m[last], "drift": h * (m[last] - m[last - 40]) / 40}
            for name, members in sets.items():
                fs = [feats[n] for n in members]
                if None in fs:
                    row[name] = None
                    continue
                design = lambda r: (None if r - 4 not in m or r not in m else
                                    [1.0, m[r] - m[r - 4]] + [f(r + d) for f in fs])
                x, y = [], []
                for r in m:
                    v = design(r)
                    if v is not None and None not in v and r + h in m:
                        x.append(v)
                        y.append(m[r + h] - m[r])
                current = design(last)
                if current is None or None in current or len(y) < len(current) + 12:
                    row[name] = None
                    continue
                row[name] = float(np.dot(np.linalg.lstsq(np.array(x), np.array(y), rcond=None)[0], current))
            singles = [row[k] for k in ("momentum", "world", "energy", "survey")]
            row["average"] = None if None in singles else float(np.mean(singles))
            rows.append(row)
        keys = ["drift"] + list(sets) + ["average"]
        for label, sample in (("dev", [r for r in rows if r["origin"].year < 2013]), ("holdout", [r for r in rows if r["origin"].year >= 2013])):
            sample = [r for r in sample if all(r[k] is not None for k in keys)]
            print(h, label, len(sample), {k: round(float(np.mean([abs(r["actual"] - r[k]) for r in sample])), 2) for k in keys})


if __name__ == "__main__":
    main()
