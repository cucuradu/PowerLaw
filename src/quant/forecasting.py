"""
Quantitative Forecasting Baselines and Conformal Residual Calibration for PowerLaw.

Implements:
1. No-change, seasonal, and tabular autoregressive baselines for quarterly construction PPI growth.
2. Honest evaluation metrics: MAE, RMSE, Directional Accuracy, sample counts.
3. Conformal 90% symmetric prediction interval calibrated on held-out absolute residuals
   from at least 20 eligible historical origins using rank ceil((n+1)*0.9).
"""

import math
from typing import Dict, List, Optional, Tuple, Any, Union
import numpy as np


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, Any]:
    if len(y_true) == 0:
        return {"mae": None, "rmse": None, "directional_accuracy": None, "sample_count": 0}
    err = y_true - y_pred
    # Sign agreement; an exact zero only matches an exact zero.
    return {"mae": round(float(np.mean(np.abs(err))), 4), "rmse": round(float(np.sqrt(np.mean(err ** 2))), 4),
            "directional_accuracy": round(float(np.mean(np.sign(y_true) == np.sign(y_pred))), 4),
            "sample_count": int(len(y_true))}


def evaluate_baselines(dataset: Dict[str, Any]) -> Dict[str, Any]:
    """No-change, seasonal and lag-1 baselines on a forecast dataset.

    A lag baseline only predicts when its lagged label was released by the origin; otherwise the
    sample is skipped for that baseline (it never silently falls back to the no-change forecast).
    """
    from datetime import datetime, timezone

    def instant(value):
        if value is None:
            return None
        value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    samples = dataset.get("samples", [])
    valid = sorted([s for s in samples if s.get("y_mask") is not None and s["y_mask"][0, 0]],
                   key=lambda s: instant(s.get("origin")) or datetime.min.replace(tzinfo=timezone.utc))
    if not valid:
        return {"status": "no_valid_labels", "sample_count": len(samples), "valid_count": 0, "baselines": {}}

    actual = np.array([float(s["y"][0, 0]) for s in valid])
    lagged = {1: [], 4: []}
    for i, sample in enumerate(valid):
        origin, target = instant(sample.get("origin")), sample.get("target_period_start")
        for lag in lagged:
            value = None
            for previous in valid[:i]:
                released = instant(previous.get("first_release_available_at"))
                if (origin is not None and target and previous.get("target_period_start") and released is not None
                        and released <= origin and previous.get("target_series_id") == sample.get("target_series_id")
                        and _quarter_index(target) - _quarter_index(previous["target_period_start"]) == lag):
                    value = float(previous["y"][0, 0])
            lagged[lag].append(value)

    def scored(predictions):
        keep = [i for i, p in enumerate(predictions) if p is not None]
        return _metrics(actual[keep], np.array([predictions[i] for i in keep], dtype=np.float64))

    return {"status": "success", "sample_count": len(samples), "valid_count": len(valid), "baselines": {
        "no_change": _metrics(actual, np.zeros(len(actual))),
        "seasonal_lag4": scored(lagged[4]),
        "autoregressive_lag1": scored(lagged[1]),
    }}


def _quarter_index(period_start: str) -> int:
    return int(period_start[:4]) * 4 + (int(period_start[5:7]) - 1) // 3


def _quarter_start(index: int) -> str:
    year, quarter = divmod(index, 4)
    return f"{year:04d}-{3 * quarter + 1:02d}-01"


def _ar_forecast(growth: Dict[int, float], last: int, target: int, fit_from: int, order: int) -> Optional[float]:
    """OLS AR(order) with intercept on the contiguous growth history ending at `last`, iterated to `target`."""
    history = []
    q = last
    while q in growth and q >= fit_from:
        history.append(growth[q])
        q -= 1
    history = history[::-1]
    if len(history) < order + 12:
        return None
    y = np.array(history[order:])
    x = np.column_stack([np.ones(len(y))] + [np.array(history[order - k:len(history) - k]) for k in range(1, order + 1)])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    path = list(history[-order:])
    for _ in range(target - last):
        path.append(float(coef[0] + sum(coef[k] * path[-k] for k in range(1, order + 1))))
    return path[-1]


def _sar_forecast(growth: Dict[int, float], last: int, target: int, fit_from: int, order: int) -> Optional[float]:
    """AR(order) with intercept and quarter-of-year dummies, fitted by OLS and iterated to `target`."""
    start = last
    while start - 1 in growth and start - 1 >= fit_from:
        start -= 1
    quarters = list(range(start, last + 1))
    if last not in growth or len(quarters) < order + 16:
        return None

    def row(q, lags):
        return [1.0] + lags + [float(q % 4 == k) for k in (1, 2, 3)]

    x = [row(q, [growth[q - k] for k in range(1, order + 1)]) for q in quarters[order:]]
    y = [growth[q] for q in quarters[order:]]
    coef, *_ = np.linalg.lstsq(np.array(x), np.array(y), rcond=None)
    path = {q: growth[q] for q in quarters}
    for q in range(last + 1, target + 1):
        path[q] = float(np.dot(coef, row(q, [path[q - k] for k in range(1, order + 1)])))
    return path[target]


def load_vat_schedule(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """German standard VAT schedule from config/de_vat_schedule.json (verified against Destatis incl./excl. indices)."""
    import json
    from pathlib import Path
    path = path or str(Path(__file__).resolve().parents[2] / "config" / "de_vat_schedule.json")
    return sorted(json.loads(Path(path).read_text(encoding="utf-8"))["entries"], key=lambda e: e["effective_from"])


def vat_rate(schedule: List[Dict[str, Any]], on, known_at=None) -> float:
    """Rate in force on date `on`, using only entries knowable at `known_at` (None: full hindsight)."""
    from datetime import date, datetime, time, timezone
    from zoneinfo import ZoneInfo
    day = on.date() if isinstance(on, datetime) else on
    rate = None
    for entry in schedule:
        effective = date.fromisoformat(entry["effective_from"])
        if effective > day:
            break
        if known_at is not None:
            announced = date.fromisoformat(entry["promulgated_at"] or entry["effective_from"])
            public = datetime.combine(announced, time(23, 59, 59), ZoneInfo("Europe/Berlin")).astimezone(timezone.utc)
            if entry["promulgated_at"] is None:  # unverified promulgation: known only once in force
                public = datetime.combine(effective, time(0, 0), ZoneInfo("Europe/Berlin")).astimezone(timezone.utc)
            if public > known_at:
                continue
        rate = entry["rate"]
    if rate is None:
        raise ValueError(f"No VAT rate known for {day}")
    return rate


def _reference_date(quarter_index: int):
    """Destatis 61261 reference month (Feb/May/Aug/Nov), mid-month."""
    from datetime import date
    year, quarter = divmod(quarter_index, 4)
    return date(year, 3 * quarter + 2, 15)


def _period_index(period_start: str, frequency: str) -> int:
    year, month = int(period_start[:4]), int(period_start[5:7])
    return year * 12 + month - 1 if frequency == "MONTHLY" else year * 4 + (month - 1) // 3


def _transform(values: Dict[int, float], p: int, transform: str) -> Optional[float]:
    """Stationary feature at period index p from levels (None when an input is missing)."""
    try:
        if transform == "yoy_3m_sum":  # removes the monthly seasonal pattern and smooths noise
            return 100 * math.log(sum(values[p - k] for k in range(3)) / sum(values[p - 12 - k] for k in range(3)))
        if transform == "yoy":
            return 100 * math.log(values[p] / values[p - 4])
        if transform == "qoq":
            return 100 * math.log(values[p] / values[p - 1])
        if transform == "diff_12m":
            return values[p] - values[p - 12]
    except (KeyError, ValueError, ZeroDivisionError):
        return None
    raise ValueError(f"Unknown transform {transform!r}")


def _feature_at_origin(predictor: Dict[str, Any], origin, origin_quarter: int):
    """Feature function of a pseudo-origin quarter, aligned to what was public at `origin`.

    The last period released by the origin sets a lag k behind the origin's own period; training rows use the
    same lag behind their pseudo-origin, so the model is fitted on the ragged edge it is applied on.
    """
    frequency, transform = predictor["frequency"], predictor["transform"]
    visible = {_period_index(r["period_start"], frequency): float(r["value"])
               for r in predictor["levels"] if r["available_at"] <= origin}
    if not visible:
        return None
    per_quarter = 3 if frequency == "MONTHLY" else 1
    last_of = lambda q: per_quarter * q + per_quarter - 1  # last period index of quarter q
    k = last_of(origin_quarter) - max(visible)
    return lambda q: _transform(visible, last_of(q) - k, transform)


def _direct_forecast(growth: Dict[int, float], last: int, target: int, origin_quarter: int, fit_from: int,
                     order: int, features: List[Any]) -> Optional[float]:
    """Direct h-step OLS: growth[r+h] on growth[r..r-order+1], target-quarter dummies and features at r+d.

    h = target - last and d = origin_quarter - last, so every training row has the same information
    timing as the forecast. Requires 20 more rows than coefficients.
    """
    h, d = target - last, origin_quarter - last

    def row(r):
        lags = [growth.get(r - k) for k in range(order)]
        extra = [f(r + d) for f in features]
        if any(v is None for v in lags + extra):
            return None
        return [1.0] + lags + [float((r + h) % 4 == k) for k in (1, 2, 3)] + extra

    x, y = [], []
    for r in range(fit_from + order, last - h + 1):
        values = row(r)
        if values is not None and r + h in growth:
            x.append(values)
            y.append(growth[r + h])
    current = row(last)
    if current is None or len(y) < len(current) + 20:
        return None
    coef, *_ = np.linalg.lstsq(np.array(x), np.array(y), rcond=None)
    return float(np.dot(coef, current))


def diebold_mariano(actual, forecast_a, forecast_b, horizon: int = 1) -> Dict[str, Any]:
    """Two-sided DM test on squared errors with Newey-West variance (horizon - 1 lags) and the
    Harvey-Leybourne-Newbold small-sample correction; negative statistic: forecast_a is more accurate."""
    d = (np.asarray(actual) - np.asarray(forecast_a)) ** 2 - (np.asarray(actual) - np.asarray(forecast_b)) ** 2
    n = len(d)
    if n < 10:
        return {"n": n, "statistic": None, "p_value": None}
    centred = d - d.mean()
    variance = float(np.mean(centred ** 2)) + 2 * sum(float(np.mean(centred[k:] * centred[:-k])) for k in range(1, horizon))
    if variance <= 0:
        return {"n": n, "statistic": None, "p_value": None}
    statistic = float(d.mean() / math.sqrt(variance / n))
    statistic *= math.sqrt((n + 1 - 2 * horizon + horizon * (horizon - 1) / n) / n)
    return {"n": n, "statistic": statistic, "p_value": math.erfc(abs(statistic) / math.sqrt(2))}


def rolling_origin_backtest(levels: List[Dict[str, Any]], origins: List[Any], *, fit_start: str = "1991-01-01",
                            alpha: float = 0.10, min_calibration: int = 20, ar_order: int = 4,
                            vat_schedule: Optional[List[Dict[str, Any]]] = None,
                            predictors: Optional[Dict[str, Dict[str, Any]]] = None,
                            predictor_sets: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """Expanding-window backtest of next-quarter growth on release-dated quarterly levels.

    `levels`: {period_start, value, available_at}. At each origin only levels released by then are used.
    Label: growth of the quarter after the origin's quarter over the origin's quarter (both unknown at
    the origin). Intervals: split-conformal on absolute residuals of earlier origins whose labels were
    already released, once at least `min_calibration` exist. Model metrics use the common sample where
    every model has a forecast, so they are comparable.
    """
    from datetime import datetime, timezone

    def utc(value):
        value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    level = {}
    for row in levels:
        if row.get("value") is None:
            continue
        q, when = _quarter_index(row["period_start"]), utc(row["available_at"])
        if q not in level or when < level[q][1]:
            level[q] = (float(row["value"]), when)
    fit_from = _quarter_index(fit_start)
    names = ("no_change", "last_growth", "seasonal_naive", "mean_4q", "ar4", "ar4_seasonal")
    if vat_schedule is not None:
        names += ("ar4_seasonal_vat",)
    if predictor_sets:
        if vat_schedule is None:
            raise ValueError("Predictor models are fitted on growth excluding VAT and need vat_schedule")
        names += ("ar4_seasonal_vat_direct",) + tuple(f"arx_vat_{name}" for name in predictor_sets)
        predictors = {name: {**p, "levels": [{**r, "available_at": utc(r["available_at"])} for r in p["levels"]
                                             if r.get("value") is not None]}
                      for name, p in (predictors or {}).items()}
    rows: List[Dict[str, Any]] = []
    for origin in sorted(utc(o) for o in origins):
        denominator = origin.year * 4 + (origin.month - 1) // 3
        target = denominator + 1
        if target not in level or denominator not in level:
            continue
        visible = {q: v for q, (v, when) in level.items() if when <= origin}
        if not visible:
            continue
        last = max(visible)
        growth = {q: 100 * (visible[q] / visible[q - 1] - 1) for q in visible if q - 1 in visible}
        recent = [growth.get(q) for q in range(last - 3, last + 1)]
        forecasts = {
            "no_change": 0.0,
            "last_growth": growth.get(last),
            "seasonal_naive": growth.get(target - 4),
            "mean_4q": float(np.mean(recent)) if all(g is not None for g in recent) else None,
            "ar4": _ar_forecast(growth, last, target, fit_from, ar_order),
            "ar4_seasonal": _sar_forecast(growth, last, target, fit_from, ar_order),
        }
        if vat_schedule is not None:
            # Model growth excluding VAT (past rates in force), then apply the rates the law said would apply
            # in the target and origin quarters, as knowable at the origin.
            ex_vat = {q: v / (1 + vat_rate(vat_schedule, _reference_date(q)))
                      for q, v in visible.items() if q >= fit_from - 1}  # VAT exists only since 1968
            ex_growth = {q: 100 * (ex_vat[q] / ex_vat[q - 1] - 1) for q in ex_vat if q - 1 in ex_vat}
            ex_forecast = _sar_forecast(ex_growth, last, target, fit_from, ar_order)
            factor = ((1 + vat_rate(vat_schedule, _reference_date(target), known_at=origin))
                      / (1 + vat_rate(vat_schedule, _reference_date(denominator), known_at=origin)))
            forecasts["ar4_seasonal_vat"] = None if ex_forecast is None else 100 * ((1 + ex_forecast / 100) * factor - 1)
            if predictor_sets:
                features = {name: _feature_at_origin(p, origin, denominator) for name, p in predictors.items()}
                runs = {"ar4_seasonal_vat_direct": []}
                runs.update({f"arx_vat_{s}": [features[n] for n in members] for s, members in predictor_sets.items()})
                for model, feats in runs.items():
                    ex = (None if any(f is None for f in feats) else
                          _direct_forecast(ex_growth, last, target, denominator, fit_from, ar_order, feats))
                    forecasts[model] = None if ex is None else 100 * ((1 + ex / 100) * factor - 1)
        known = [r for r in rows if r["label_available_at"] <= origin]
        widths = {}
        for name in names:
            residuals = [abs(r["actual"] - r["forecasts"][name]) for r in known if r["forecasts"][name] is not None]
            widths[name] = (calibrate_residual_interval(residuals, alpha)["interval_half_width"]
                            if len(residuals) >= min_calibration and forecasts[name] is not None else None)
        rows.append({"origin": origin, "target_period_start": _quarter_start(target),
                     "denominator_period_start": _quarter_start(denominator),
                     "last_observed_period_start": _quarter_start(last),
                     "actual": 100 * (level[target][0] / level[denominator][0] - 1),
                     "label_available_at": max(level[target][1], level[denominator][1]),
                     "last_growth": growth.get(last), "forecasts": forecasts,
                     "calibration_count": len(known), "interval_half_width": widths})

    common = [r for r in rows if all(r["forecasts"][n] is not None for n in names)]
    models = {}
    for name in names:
        actual = np.array([r["actual"] for r in common])
        predicted = np.array([r["forecasts"][name] for r in common])
        err = actual - predicted
        moved = [r for r in common if r["last_growth"] is not None and r["actual"] != r["last_growth"]]
        models[name] = {
            "n": len(common),
            "mae": float(np.mean(np.abs(err))) if common else None,
            "rmse": float(np.sqrt(np.mean(err ** 2))) if common else None,
            # Did the forecast call the direction of change in growth (acceleration vs deceleration)?
            "acceleration_hit_rate": float(np.mean([np.sign(r["forecasts"][name] - r["last_growth"])
                                                    == np.sign(r["actual"] - r["last_growth"]) for r in moved]))
                                     if moved else None,
        }
    intervals = {}
    for name in names:
        scored = [r for r in rows if r["interval_half_width"][name] is not None]
        hits = [abs(r["actual"] - r["forecasts"][name]) <= r["interval_half_width"][name] for r in scored]
        intervals[name] = {"n": len(scored), "nominal": 1 - alpha,
                           "coverage": float(np.mean(hits)) if scored else None,
                           "mean_half_width": float(np.mean([r["interval_half_width"][name] for r in scored])) if scored else None}
    return {"predictions": rows, "models": models, "intervals": intervals,
            "settings": {"fit_start": fit_start, "alpha": alpha, "min_calibration": min_calibration, "ar_order": ar_order,
                         "predictor_sets": predictor_sets or {}}}


def calibrate_residual_interval(
    residuals: Union[List[float], np.ndarray],
    alpha: float = 0.10
) -> Dict[str, Any]:
    """
    Calibrates a symmetric (1 - alpha) prediction interval on absolute residuals
    using finite-sample conformal prediction with rank ceil((n + 1) * (1 - alpha)).

    Enforces minimum 20 eligible calibration origins:
    If n < 20, calibration status is 'insufficient_calibration_history' and returns None.
    """
    if not 0 < alpha < 1 or any(not math.isfinite(float(r)) for r in residuals):
        raise ValueError("Calibration requires finite residuals and 0 < alpha < 1")
    n = len(residuals)
    if n < 20:
        return {
            "is_calibrated": False,
            "status": "insufficient_calibration_history",
            "interval_half_width": None,
            "alpha": alpha,
            "sample_count": n,
            "min_required_samples": 20,
            "message": f"At least 20 eligible calibration origins required; received {n}."
        }

    abs_residuals = sorted([abs(float(r)) for r in residuals])
    rank = math.ceil((n + 1) * (1.0 - alpha))
    # Rank is 1-indexed; array is 0-indexed
    idx = min(rank - 1, n - 1)
    q_val = float(abs_residuals[idx])

    return {
        "is_calibrated": True,
        "status": "calibrated",
        "interval_half_width": q_val,
        "alpha": alpha,
        "sample_count": n,
        "calibration_rank": rank,
    }


def evaluate_interval_coverage(
    actuals: Union[List[float], np.ndarray],
    predictions: Union[List[float], np.ndarray],
    interval_half_width: float
) -> Dict[str, Any]:
    """
    Evaluates empirical coverage and width of calibrated prediction intervals on test outcomes.
    """
    act = np.asarray(actuals, dtype=np.float64)
    pred = np.asarray(predictions, dtype=np.float64)
    if len(act) == 0:
        return {"sample_count": 0, "coverage": 0.0, "mean_width": 0.0}

    lower = pred - interval_half_width
    upper = pred + interval_half_width
    covered = (act >= lower) & (act <= upper)
    coverage = float(np.mean(covered))
    width = float(2.0 * interval_half_width)

    return {
        "sample_count": len(act),
        "coverage": round(coverage, 4),
        "mean_width": round(width, 4),
        "lower_bounds": lower.tolist(),
        "upper_bounds": upper.tolist(),
    }

