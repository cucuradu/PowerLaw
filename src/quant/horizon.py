"""Cumulative price change over 1-3 years (4, 8 or 12 quarters) from the last released quarter.

What a budget or escalation clause needs is the level h quarters out relative to today's, so the label is
100 * (level[last + h] / level[last] - 1), with `last` the latest quarter released at the origin. Models work on
growth excluding VAT when a VAT schedule is given and apply the rates the law said would apply, as known then.

  no_change      0
  drift_10y      the last 40 quarters' mean log growth, compounded
  sar            seasonal AR(4) (forecasting._sar_forecast) iterated h quarters
  direct_ar      OLS of h-quarter log growth on the last 4 quarters' log growth
  direct_inputs  direct_ar + input features at their release-aligned lag (forecasting._feature_at_origin)

Intervals: split conformal on absolute errors of earlier origins whose labels were released by the origin
(`interval_half_width`), and a history range (`history_range`): the 5th and 95th percentiles of every h-quarter
change in the series since `range_start` that was public at the origin, with the known VAT step applied. The
history range ignores the point forecast; it says what has happened over h quarters before.
"""
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from src.quant.forecasting import (_feature_at_origin, _quarter_index, _quarter_start, _reference_date, _sar_forecast,
                                   calibrate_residual_interval, vat_rate)

MODELS = ("no_change", "drift_10y", "sar", "direct_ar", "direct_inputs")


def _utc(value):
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _direct(logs: Dict[int, float], last: int, horizon: int, fit_from: int, features: list) -> Optional[float]:
    """h-quarter log growth on [1, last 4 quarters' log growth, features]; rows whose label is known (r + h <= last)."""
    def row(r):
        if not all(q in logs for q in (r, r - 4)):
            return None
        extra = [f(r) for f in features]
        return None if any(v is None for v in extra) else [1.0, logs[r] - logs[r - 4]] + extra
    x, y = [], []
    for r in range(fit_from + 4, last - horizon + 1):
        values = row(r)
        if values is not None and r + horizon in logs:
            x.append(values)
            y.append(logs[r + horizon] - logs[r])
    current = row(last)
    if current is None or len(y) < len(current) + 20:
        return None
    coef, *_ = np.linalg.lstsq(np.array(x), np.array(y), rcond=None)
    return float(np.dot(coef, current))


def horizon_backtest(levels: List[Dict[str, Any]], origins: List[Any], horizon: int, *, fit_start: str = "1991-01-01",
                     vat_schedule=None, predictors: Optional[Dict[str, Dict[str, Any]]] = None,
                     inputs: tuple = (), live_origin=None, alpha: float = 0.10, min_calibration: int = 20,
                     range_start: Optional[str] = None) -> Dict[str, Any]:
    """Rows {origin, base/target period, forecasts, actual, interval_half_width}; `live` for live_origin."""
    level = {}
    for r in levels:
        if r.get("value") is not None:
            q, when = _quarter_index(r["period_start"]), _utc(r["available_at"])
            if q not in level or when < level[q][1]:
                level[q] = (float(r["value"]), when)
    fit_from = _quarter_index(fit_start)
    predictors = {name: {**p, "levels": [{**r, "available_at": _utc(r["available_at"])} for r in p["levels"]
                                         if r.get("value") is not None]} for name, p in (predictors or {}).items()}
    rows, live = [], None
    schedule = [(_utc(o), False) for o in origins] + ([(_utc(live_origin), True)] if live_origin is not None else [])
    for origin, is_live in sorted(schedule, key=lambda item: item[0]):
        visible = {q: v for q, (v, when) in level.items() if when <= origin and q >= fit_from - 1}
        if not visible:
            continue
        last = max(visible)
        target = last + horizon
        if not is_live and target not in level:
            continue
        rate = (lambda q, known: vat_rate(vat_schedule, _reference_date(q), known_at=known)) if vat_schedule else (lambda q, known: 0.0)
        ex = {q: v / (1 + rate(q, None)) for q, v in visible.items()}  # past rates in force
        logs = {q: 100 * math.log(v) for q, v in ex.items()}
        growth = {q: 100 * (ex[q] / ex[q - 1] - 1) for q in ex if q - 1 in ex}
        factor = (1 + rate(target, origin)) / (1 + rate(last, origin))
        recent = [logs[q] - logs[q - 1] for q in range(last - 39, last + 1) if q in logs and q - 1 in logs]
        sar_path = [_sar_forecast(growth, last, q, fit_from, 4) for q in range(last + 1, target + 1)]
        origin_quarter = origin.year * 4 + (origin.month - 1) // 3
        d = origin_quarter - last  # same release lag for training rows as at the origin
        feats = [_feature_at_origin(predictors[n], origin, origin_quarter) for n in inputs]
        shifted = [None if f is None else (lambda r, f=f: f(r + d)) for f in feats]
        log_forecasts = {
            "no_change": None,
            "drift_10y": horizon * float(np.mean(recent)) if len(recent) == 40 else None,
            "sar": None if None in sar_path else 100 * math.log(np.prod([1 + g / 100 for g in sar_path])),
            "direct_ar": _direct(logs, last, horizon, fit_from, []),
            "direct_inputs": None if not inputs or None in shifted else _direct(logs, last, horizon, fit_from, shifted),
        }
        forecasts = {m: None if v is None else 100 * (math.exp(v / 100) * factor - 1) for m, v in log_forecasts.items()}
        forecasts["no_change"] = 0.0
        known = [r for r in rows if r["label_available_at"] <= origin]
        widths = {}
        for m in MODELS:
            residuals = [abs(r["actual"] - r["forecasts"][m]) for r in known if r["forecasts"][m] is not None]
            widths[m] = (calibrate_residual_interval(residuals, alpha)["interval_half_width"]
                         if len(residuals) >= min_calibration and forecasts[m] is not None else None)
        first = _quarter_index(range_start or fit_start)
        long = {q: v / (1 + rate(q, None)) for q, (v, when) in level.items() if when <= origin and q >= first}
        windows = [100 * math.log(long[q + horizon] / long[q]) for q in long if q + horizon in long]
        history = ([100 * (math.exp(x / 100) * factor - 1) for x in np.percentile(windows, [100 * alpha / 2, 100 - 100 * alpha / 2])]
                   if len(windows) >= min_calibration else None)
        row = {"origin": origin, "base_period_start": _quarter_start(last), "base_level": visible[last],
               "target_period_start": _quarter_start(target), "horizon": horizon, "forecasts": forecasts,
               "interval_half_width": widths, "history_range": history}
        if is_live:
            live = row
            continue
        rows.append({**row, "actual": 100 * (level[target][0] / level[last][0] - 1),
                     "label_available_at": max(level[target][1], level[last][1])})
    return {"predictions": rows, "live": live}


def panel_direct(panel: Dict[str, Dict[int, tuple]], origin, horizon: int, own: str, last: int) -> Optional[float]:
    """EU-wide direct model: h-quarter log growth on [1, last-year growth, last-10-year mean annual growth], fitted on
    every country-quarter of `panel` ({geo: {quarter: (100 log level, public)}}) whose outcome was public at the
    origin; applied to `own` at its last released quarter `last`. Returns the log change (x100), VAT not applied."""
    def feats(logs, r):
        return [1.0, logs[r] - logs[r - 4], (logs[r] - logs[r - 40]) / 10] if all(q in logs for q in (r, r - 4, r - 40)) else None
    x, y, current = [], [], None
    for geo, series in panel.items():
        logs = {q: v for q, (v, when) in series.items() if when <= origin}
        for r in logs:
            f = feats(logs, r)
            if f and r + horizon in logs:
                x.append(f)
                y.append(logs[r + horizon] - logs[r])
        if geo == own and last in logs:
            current = feats(logs, last)
    if current is None or len(y) < 100:
        return None
    return float(np.dot(np.linalg.lstsq(np.array(x), np.array(y), rcond=None)[0], current))
