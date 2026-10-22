"""Research (2026-10-22, decisions.md "Cost build-up"): how much of the target's 1-3 year change do input costs explain if they were known (oracle)?

y = h-quarter log growth of the target ex VAT from base r. Regressors (fitted on windows whose outcome AND costs were
public at the origin; applied with the costs that actually followed = oracle):
  cost_oracle   [1, material_h, labour_h, margin gap at r]
  wages_oracle  [1, wages_h, last-year growth]           wages_h = agreed construction wages over the window
                                                           (proxy for signed wage agreements known in advance)
  momentum      [1, last-year growth]                     no future information (= direct_ar)
Margin gap = log target ex VAT - (log material + log labour) / 2 at r. Origins as horizon_forecast; dev < 2013.
"""
import math
from datetime import datetime, timedelta, timezone

import numpy as np

from scripts.ops.backtest_target import quarter_end_origins
from scripts.ops.project_series import quarterly_levels
from src.config import primary_target_series_id
from src.quant.forecasting import _quarter_index, _reference_date, load_vat_schedule, vat_rate
from src.quant.horizon import horizon_backtest
from src.storage.dual_layer_store import DualLayerStore

MAT = "61261-0014|DINSG=DG|KOSAT1=KOST011|KOS022|2021=100"
LAB = "61261-0014|DINSG=DG|KOSAT1=KOST012|KOS022|2021=100"
WAGE = "62221-0002|DINSG=DG|WZ08C7=WZ08-F|VST066|2020=100"
LAGS = {MAT: 120, LAB: 120, WAGE: 75}
store = DualLayerStore(); now = datetime.now(timezone.utc)
revisions = store.query_observation_revisions(now, "system_as_of")
schedule = load_vat_schedule()
series = {}
for sid in (MAT, LAB, WAGE):
    series[sid] = {_quarter_index(r["period_start"]): (100 * math.log(r["value"]), r["available_at"])
                   for r in quarterly_levels(revisions, sid, LAGS[sid], store)}
target_levels = quarterly_levels(revisions, primary_target_series_id(), None, store)
P = {_quarter_index(r["period_start"]): (100 * math.log(r["value"] / (1 + vat_rate(schedule, _reference_date(_quarter_index(r["period_start"]))))), r["available_at"])
     for r in target_levels if r["period_start"] >= "1991-01-01"}
origins = [o + timedelta(days=20) for o in quarter_end_origins("1998-03-31", now)]


def design(kind, p, s, r, h):
    m, l, w = s[MAT], s[LAB], s[WAGE]
    if not all(q in p for q in (r, r - 4)):
        return None
    if kind == "momentum":
        return [1.0, p[r] - p[r - 4]]
    if kind == "wages_oracle":
        return [1.0, w[r + h] - w[r], p[r] - p[r - 4]] if r in w and r + h in w else None
    if all(q in m and q in l for q in (r, r + h)):
        return [1.0, m[r + h] - m[r], l[r + h] - l[r], p[r] - (m[r] + l[r]) / 2]
    return None


for h in (4, 8, 12):
    local = {r["origin"]: r for r in horizon_backtest(target_levels, origins, h, fit_start="1991-01-01", vat_schedule=schedule)["predictions"]}
    rows = []
    for origin in origins:
        if origin not in local:
            continue
        p = {q: v for q, (v, when) in P.items() if when <= origin}
        s = {sid: {q: v for q, (v, when) in ser.items() if when <= origin} for sid, ser in series.items()}
        full = {sid: {q: v for q, (v, when) in ser.items()} for sid, ser in series.items()}  # oracle: costs that followed
        last = max(p)
        factor = (1 + vat_rate(schedule, _reference_date(last + h), known_at=origin)) / (1 + vat_rate(schedule, _reference_date(last), known_at=origin))
        out = {"origin": origin, "actual": local[origin]["actual"], "drift_10y": local[origin]["forecasts"]["drift_10y"],
               "sar": local[origin]["forecasts"]["sar"]}
        for kind in ("momentum", "wages_oracle", "cost_oracle"):
            x, y = [], []
            for r in p:
                d = design(kind, p, s, r, h)
                if d is not None and r + h in p:
                    x.append(d); y.append(p[r + h] - p[r])
            current = design(kind, p, {k: {**full[k], **s[k]} for k in full}, last, h)
            if current is None or len(y) < len(current) + 12:
                out[kind] = None
                continue
            coef = np.linalg.lstsq(np.array(x), np.array(y), rcond=None)[0]
            out[kind] = 100 * (math.exp(float(np.dot(coef, current)) / 100) * factor - 1)
        fm, fl = {**full[MAT], **s[MAT]}, {**full[LAB], **s[LAB]}
        out["passthrough"] = (100 * (math.exp((0.5 * (fm[last + h] - fm[last]) + 0.5 * (fl[last + h] - fl[last])) / 100) * factor - 1)
                              if all(q in fm and q in fl for q in (last, last + h)) else None)
        def drift(ser):  # 10-year mean quarterly log growth of what is public, carried from the last public quarter to last + h
            known = s[ser]; k = max(known)
            if k - 40 not in known:
                return None
            return known[k] + (last + h - k) * (known[k] - known[k - 40]) / 40, k
        def change(actual_ser, use_actual):
            if use_actual:
                ser = {**full[actual_ser], **s[actual_ser]}
                return ser[last + h] - ser[last] if last in ser and last + h in ser else None
            d = drift(actual_ser)
            if d is None:
                return None
            # base: actual if public, else drift from the last public quarter
            known = s[actual_ser]; k = d[1]
            base = known[last] if last in known else known[k] + (last - k) * (known[k] - known[k - 40]) / 40
            return d[0] - base
        for name, (m_act, l_act) in {"cost_drift": (False, False), "labour_known": (False, True), "material_known": (True, False)}.items():
            dm, dl = change(MAT, m_act), change(LAB, l_act)
            out[name] = None if None in (dm, dl) else 100 * (math.exp((0.5 * dm + 0.5 * dl) / 100) * factor - 1)
        for wm in (0.3, 0.4, 0.5, 0.6, 0.7):
            out[f"w{wm}"] = (100 * (math.exp((wm * (fm[last + h] - fm[last]) + (1 - wm) * (fl[last + h] - fl[last])) / 100) * factor - 1)
                             if all(q in fm and q in fl for q in (last, last + h)) else None)
        rows.append(out)
    keys = ("momentum", "wages_oracle", "cost_oracle", "passthrough", "material_known", "labour_known", "cost_drift",
            "w0.3", "w0.4", "w0.5", "w0.6", "w0.7", "drift_10y", "sar")
    for label, sample in (("dev", [r for r in rows if r["origin"].year < 2013]), ("holdout", [r for r in rows if r["origin"].year >= 2013])):
        # each model on the origins where it and the trend both exist (dev counts differ: the cost drift needs 10 years)
        print(h, label, {k: (len(s), round(float(np.mean([abs(r["actual"] - r[k]) for r in s])), 2))
                         for k in keys for s in [[r for r in sample if r.get(k) is not None and r["drift_10y"] is not None]] if s})
