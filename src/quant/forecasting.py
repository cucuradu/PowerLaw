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


def rolling_origin_backtest(levels: List[Dict[str, Any]], origins: List[Any], *, fit_start: str = "1991-01-01",
                            alpha: float = 0.10, min_calibration: int = 20, ar_order: int = 4) -> Dict[str, Any]:
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
    names = ("no_change", "last_growth", "seasonal_naive", "mean_4q", "ar4")
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
        }
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
            "settings": {"fit_start": fit_start, "alpha": alpha, "min_calibration": min_calibration, "ar_order": ar_order}}


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

