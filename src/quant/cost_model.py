"""1-3 year price scenarios from the cost build-up: output prices follow input costs.

Price change ex VAT over h quarters = WEIGHT x material cost change + (1 - WEIGHT) x labour cost change (Destatis
61261-0014 building cost components), then the VAT step known at the origin. Tested 2026-10-22 (decisions.md
"Cost build-up"): with the costs that actually followed, this fixed pass-through misses by about half as much as any
forecast model; WEIGHT was chosen on origins before 2013 (0.5-0.6 all similar).

At an origin the costs are not known, so:
  labour      continues its 10-year trend (signed wage agreements would replace this)
  materials   scenarios = percentiles of every h-quarter material cost change since the series starts (2000), public
              at the origin: low (10th), central (median), high (90th), record (largest so far). Producer prices of materials (1976+) were
              tried as a longer history and did worse: they rise more slowly than the cost index.
With `material_inputs` ({name: predictor} for forecasting._feature_at_origin, grouped by `material_sets`), rows also
carry `inputs`: the price change with materials forecast by the equal-weight average of direct regressions of the
material change on [1, last-year change, the set's inputs] (one regression per set; no set is selected), labour on
its trend. Rows also carry `conditional`: the price change implied by the costs that actually followed (backtests
only), which measures the model, not the scenarios.
"""
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from src.quant.forecasting import _feature_at_origin, _quarter_index, _quarter_start, _reference_date, vat_rate

WEIGHT = 0.6
SCENARIOS = {"low": 10, "central": 50, "high": 90, "record": 100}  # record: largest h-quarter rise so far


def _logs(levels: List[Dict[str, Any]], vat_schedule=None) -> Dict[int, tuple]:
    """{quarter: (100 log level [ex VAT], first public)}."""
    out = {}
    for r in levels:
        if r.get("value") is None:
            continue
        q = _quarter_index(r["period_start"])
        if vat_schedule and _reference_date(q).isoformat() < vat_schedule[0]["effective_from"]:
            continue  # before VAT (1968-07): no ex-VAT level
        value = r["value"] / (1 + vat_rate(vat_schedule, _reference_date(q))) if vat_schedule else r["value"]
        when = r["available_at"]
        when = datetime.fromisoformat(when) if isinstance(when, str) else when
        if q not in out or when < out[q][1]:
            out[q] = (100 * math.log(value), when)
    return out


def _carry(series: Dict[int, float], to: int) -> Optional[float]:
    """Log level at quarter `to`: actual when public, else the 10-year trend from the last public quarter."""
    if to in series:
        return series[to]
    k = max(series)
    if k - 40 not in series or to < k:
        return None
    return series[k] + (to - k) * (series[k] - series[k - 40]) / 40


def material_change(m: Dict[int, float], steps: int, features: List[Any], min_rows: int = 12) -> Optional[float]:
    """Direct forecast of m[last + steps] - m[last] from [1, last-year change, features] (rows with known outcomes)."""
    if steps == 0:
        return 0.0
    last = max(m)

    def row(r):
        if r not in m or r - 4 not in m:
            return None
        extra = [f(r) for f in features]
        return None if None in extra else [1.0, m[r] - m[r - 4]] + extra
    x, y = [], []
    for r in m:
        values = row(r)
        if values is not None and r + steps in m:
            x.append(values)
            y.append(m[r + steps] - m[r])
    current = row(last)
    if current is None or len(y) < len(current) + min_rows:
        return None
    return float(np.dot(np.linalg.lstsq(np.array(x), np.array(y), rcond=None)[0], current))


def cost_scenarios(target: List[Dict[str, Any]], material: List[Dict[str, Any]], labour: List[Dict[str, Any]],
                   origins: List[Any], horizon: int, vat_schedule, weight: float = WEIGHT, live_origin=None,
                   min_windows: int = 20, material_inputs: Optional[Dict[str, Any]] = None,
                   material_sets: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """Rows {origin, base/target period, scenarios {low, central, high}, conditional, actual}; `live` for live_origin."""
    p_all, m_all, l_all = _logs(target, vat_schedule), _logs(material), _logs(labour)
    p_level = {q: (math.exp(v / 100), when) for q, (v, when) in _logs(target).items()}  # incl. VAT, for the label
    utc = lambda o: o.replace(tzinfo=timezone.utc) if o.tzinfo is None else o
    rows, live = [], None
    schedule = [(utc(o), False) for o in origins] + ([(utc(live_origin), True)] if live_origin is not None else [])
    for origin, is_live in sorted(schedule, key=lambda item: item[0]):
        p = {q: v for q, (v, when) in p_all.items() if when <= origin}
        m = {q: v for q, (v, when) in m_all.items() if when <= origin}
        lab = {q: v for q, (v, when) in l_all.items() if when <= origin}
        if not p or not m or not lab:
            continue
        last = max(p)
        target_q = last + horizon
        if not is_live and target_q not in p_level:
            continue
        m_base, l_base, l_end = _carry(m, last), _carry(lab, last), _carry(lab, target_q)
        windows = [m[q + horizon] - m[q] for q in m if q + horizon in m]
        if None in (m_base, l_base, l_end) or len(windows) < min_windows:
            continue
        factor = ((1 + vat_rate(vat_schedule, _reference_date(target_q), known_at=origin))
                  / (1 + vat_rate(vat_schedule, _reference_date(last), known_at=origin)))
        price = lambda dm, dl: 100 * (math.exp((weight * dm + (1 - weight) * dl) / 100) * factor - 1)
        scenario_change = dict(zip(SCENARIOS, np.percentile(windows, list(SCENARIOS.values()))))
        row = {"origin": origin, "base_period_start": _quarter_start(last), "target_period_start": _quarter_start(target_q),
               "horizon": horizon, "labour_change_log": l_end - l_base,
               "material_change_log": {k: float(v) for k, v in scenario_change.items()},
               "scenarios": {k: price(v, l_end - l_base) for k, v in scenario_change.items()}}
        if material_inputs:
            # Materials from the last public quarter K: change K -> last + h minus change K -> last (0 if last = K).
            k = max(m)
            origin_quarter = origin.year * 4 + (origin.month - 1) // 3
            feats = {n: _feature_at_origin(p_, origin, origin_quarter) for n, p_ in material_inputs.items()}
            shifted = {n: None if f is None else (lambda r, f=f: f(r + origin_quarter - k)) for n, f in feats.items()}
            paths = []
            for members in material_sets.values():
                fs = [shifted[n] for n in members]
                if None in fs:
                    continue
                end, start = material_change(m, target_q - k, fs), material_change(m, max(last - k, 0), fs)
                if None not in (end, start):
                    paths.append(end - start if last >= k else end - (m[last] - m[k]))
            row["inputs"] = price(float(np.mean(paths)), l_end - l_base) if len(paths) == len(material_sets) else None
        if is_live:
            live = row
            continue
        full_m, full_l = {q: v for q, (v, _) in m_all.items()}, {q: v for q, (v, _) in l_all.items()}
        known = all(q in full_m and q in full_l for q in (last, target_q))
        row["conditional"] = (price(full_m[target_q] - full_m[last], full_l[target_q] - full_l[last]) if known else None)
        row["actual"] = 100 * (p_level[target_q][0] / p_level[last][0] - 1)
        rows.append(row)
    return {"predictions": rows, "live": live}
