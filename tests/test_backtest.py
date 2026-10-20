"""Rolling-origin backtest on release-dated levels: no leakage, no silent fallbacks."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.quant.forecasting import evaluate_baselines, rolling_origin_backtest


def _quarter_start(year, quarter):
    return f"{year:04d}-{3 * (quarter - 1) + 1:02d}-01"


def _levels(n_quarters=60, growth=None, start_year=2000, lag_days=10):
    """Quarterly levels, each released lag_days after its quarter ends."""
    growth = growth if growth is not None else [0.5 + 0.3 * np.sin(i / 3.0) for i in range(n_quarters)]
    level, rows = 100.0, []
    for i in range(n_quarters):
        year, quarter = start_year + i // 4, i % 4 + 1
        level *= 1 + growth[i] / 100
        end = datetime(year + (quarter == 4), 1 if quarter == 4 else 3 * quarter + 1, 1, tzinfo=timezone.utc)
        rows.append({"period_start": _quarter_start(year, quarter), "value": level,
                     "available_at": end + timedelta(days=lag_days)})
    return rows


def _origins(first_year, last_year):
    return [datetime(y, m, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
            for y in range(first_year, last_year + 1) for m in (4, 7, 10)]


def test_label_is_next_quarter_growth_and_forecasts_use_only_released_levels():
    levels = _levels()
    origin = datetime(2010, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
    clean = rolling_origin_backtest(levels, [origin], fit_start="2000-01-01")
    # A level released after the origin must not change any forecast.
    poisoned = [dict(r, value=r["value"] * 5) if r["period_start"] == "2010-04-01" else r for r in levels]
    leaked = rolling_origin_backtest(poisoned, [origin], fit_start="2000-01-01")
    (row,) = clean["predictions"]
    assert row["target_period_start"] == "2010-07-01" and row["denominator_period_start"] == "2010-04-01"
    assert row["last_observed_period_start"] == "2010-01-01"  # Q2 is not yet released at the origin
    assert row["forecasts"] == leaked["predictions"][0]["forecasts"]
    expected = 100 * (levels[42]["value"] / levels[41]["value"] - 1)
    assert row["actual"] == pytest.approx(expected)


def test_seasonal_naive_is_missing_rather_than_zero_without_history():
    levels = _levels(n_quarters=8)
    origin = datetime(2000, 6, 30, 23, 59, 59, tzinfo=timezone.utc)  # target 2000-Q3; 1999-Q3 does not exist
    (row,) = rolling_origin_backtest(levels, [origin], fit_start="2000-01-01")["predictions"]
    assert row["forecasts"]["seasonal_naive"] is None
    assert row["forecasts"]["no_change"] == 0.0


def test_ar4_learns_a_persistent_growth_process_better_than_no_change():
    rng = np.random.default_rng(0)
    g = [0.8]
    for _ in range(99):
        g.append(0.2 + 0.75 * g[-1] + rng.normal(0, 0.15))
    result = rolling_origin_backtest(_levels(100, g), _origins(2010, 2023), fit_start="2000-01-01")
    models = result["models"]
    assert models["ar4"]["mae"] < models["no_change"]["mae"]
    assert models["ar4"]["n"] == models["no_change"]["n"] > 20


def test_conformal_interval_uses_only_residuals_whose_labels_were_known_at_the_origin():
    result = rolling_origin_backtest(_levels(100), _origins(2005, 2023), fit_start="2000-01-01")
    rows = result["predictions"]
    for row in rows:
        width = row["interval_half_width"]["ar4"]
        if row["calibration_count"] < 20:
            assert width is None
    first = next(r for r in rows if r["interval_half_width"]["ar4"] is not None)
    assert first["calibration_count"] >= 20
    assert 0.0 <= result["intervals"]["ar4"]["coverage"] <= 1.0


def test_evaluate_baselines_skips_unavailable_lags_instead_of_predicting_zero():
    samples = []
    for i, (year, quarter, value) in enumerate([(2023, 1, 1.0), (2023, 2, 2.0), (2023, 3, 3.0)]):
        samples.append({"origin": datetime(year, 3 * quarter, 28, tzinfo=timezone.utc), "target_series_id": "t",
                        "target_period_start": _quarter_start(year, quarter), "y": np.array([[value]]),
                        "y_mask": np.array([[True]]),
                        "first_release_available_at": datetime(year, 3 * quarter, 28, tzinfo=timezone.utc) + timedelta(days=60)})
    result = evaluate_baselines({"samples": samples})["baselines"]
    assert result["autoregressive_lag1"]["sample_count"] == 2  # first sample has no released lag
    assert result["seasonal_lag4"]["sample_count"] == 0
    assert result["seasonal_lag4"]["mae"] is None
