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


def evaluate_baselines(dataset: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates no-change, seasonal, and small tabular baselines on a forecast dataset.
    Returns metrics (MAE, RMSE, Directional Accuracy, sample counts).
    """
    samples = dataset.get("samples", [])
    from datetime import datetime, timezone

    def instant(value):
        if value is None:
            return None
        value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    valid_samples = sorted([s for s in samples if s.get("y_mask") is not None and s["y_mask"][0, 0]],
                           key=lambda s: instant(s.get("origin")) or datetime.min.replace(tzinfo=timezone.utc))

    if not valid_samples:
        return {
            "status": "no_valid_labels",
            "sample_count": len(samples),
            "valid_count": 0,
            "baselines": {}
        }

    actuals = np.array([float(s["y"][0, 0]) for s in valid_samples], dtype=np.float64)
    N = len(actuals)

    # 1. No-Change Baseline: predicts 0.0% next-quarter percentage growth
    preds_no_change = np.zeros(N, dtype=np.float64)

    # 2. Seasonal Baseline: predicts growth from 4 quarters ago if available
    preds_seasonal = np.zeros(N, dtype=np.float64)
    # Each lag is a matching calendar quarter whose outcome was actually released at origin.
    preds_lag1 = np.zeros(N, dtype=np.float64)
    for i, sample in enumerate(valid_samples):
        origin = instant(sample.get("origin"))
        target = sample.get("target_period_start")
        if origin is None or not target:
            continue
        quarter = int(target[:4]) * 4 + (int(target[5:7]) - 1) // 3
        for lag, predictions in ((1, preds_lag1), (4, preds_seasonal)):
            for previous in valid_samples[:i]:
                period = previous.get("target_period_start")
                released = instant(previous.get("first_release_available_at"))
                if (not period or released is None or released > origin
                        or previous.get("target_series_id") != sample.get("target_series_id")):
                    continue
                previous_quarter = int(period[:4]) * 4 + (int(period[5:7]) - 1) // 3
                if quarter - previous_quarter == lag:
                    predictions[i] = float(previous["y"][0, 0])

    def _calc_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, Any]:
        err = y_true - y_pred
        mae = float(np.mean(np.abs(err)))
        rmse = float(np.sqrt(np.mean(err ** 2)))
        # Directional accuracy: sign match (zero counts as matching positive or non-negative)
        correct_dir = np.sum(np.sign(y_true) == np.sign(y_pred))
        dir_acc = float(correct_dir / len(y_true))
        return {
            "mae": round(mae, 4),
            "rmse": round(rmse, 4),
            "directional_accuracy": round(dir_acc, 4),
            "sample_count": len(y_true),
        }

    return {
        "status": "success",
        "sample_count": len(samples),
        "valid_count": N,
        "baselines": {
            "no_change": _calc_metrics(actuals, preds_no_change),
            "seasonal_lag4": _calc_metrics(actuals, preds_seasonal),
            "autoregressive_lag1": _calc_metrics(actuals, preds_lag1),
        }
    }


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

