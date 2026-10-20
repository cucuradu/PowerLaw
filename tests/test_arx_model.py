"""ARX on growth excluding VAT: uses a leading predictor, and only the predictor values public at the origin."""
import math
from datetime import date, datetime, timedelta, timezone

import numpy as np

from src.quant.forecasting import diebold_mariano, load_vat_schedule, rolling_origin_backtest, vat_rate


def _quarter_end(year, quarter):
    return datetime(year + (quarter == 4), 1 if quarter == 4 else 3 * quarter + 1, 1, tzinfo=timezone.utc)


def _synthetic(n=100, seed=1):
    """Ex-VAT growth follows a quarterly predictor two quarters earlier; predictor released 30 days after its quarter."""
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 1, n)
    schedule = load_vat_schedule()
    target, predictor, ex_vat, level = [], [], 100.0, 100.0
    for i in range(n):
        year, quarter = 1995 + i // 4, i % 4 + 1
        start = f"{year}-{3 * quarter - 2:02d}-01"
        level *= math.exp(x[i] / 100)
        predictor.append({"period_start": start, "value": level, "available_at": _quarter_end(year, quarter) + timedelta(days=30)})
        growth = 0.5 + (0.8 * x[i - 2] if i >= 2 else 0) + rng.normal(0, 0.05)
        ex_vat *= 1 + growth / 100
        rate = vat_rate(schedule, date(year, 3 * quarter - 1, 15))
        target.append({"period_start": start, "value": ex_vat * (1 + rate), "available_at": _quarter_end(year, quarter) + timedelta(days=10)})
    return target, predictor, schedule


def _origins(first_year, last_year):
    return [_quarter_end(y, q) - timedelta(seconds=1) for y in range(first_year, last_year + 1) for q in range(1, 5)]


def test_arx_recovers_a_leading_predictor():
    target, predictor, schedule = _synthetic()
    result = rolling_origin_backtest(target, _origins(2010, 2018), fit_start="1995-01-01", vat_schedule=schedule,
                                     predictors={"x": {"levels": predictor, "frequency": "QUARTERLY", "transform": "qoq"}},
                                     predictor_sets={"x": ["x"]})
    models = result["models"]
    assert models["arx_vat_x"]["n"] >= 30
    assert models["arx_vat_x"]["mae"] < 0.1 < models["ar4_seasonal_vat_direct"]["mae"]


def test_arx_ignores_predictor_values_released_after_the_origin():
    target, predictor, schedule = _synthetic()
    origin = datetime(2012, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
    run = lambda levels: rolling_origin_backtest(
        target, [origin], fit_start="1995-01-01", vat_schedule=schedule,
        predictors={"x": {"levels": levels, "frequency": "QUARTERLY", "transform": "qoq"}},
        predictor_sets={"x": ["x"]})["predictions"][0]["forecasts"]["arx_vat_x"]
    public = [r for r in predictor if r["available_at"] <= origin]
    future = [{**r, "value": r["value"] * 3} for r in predictor if r["available_at"] > origin]
    assert run(predictor) is not None
    assert run(predictor) == run(public) == run(public + future)


def test_diebold_mariano_detects_a_clearly_better_forecast():
    rng = np.random.default_rng(0)
    actual = rng.normal(0, 1, 80)
    good, bad = actual + rng.normal(0, 0.1, 80), actual + rng.normal(0, 1, 80)
    test = diebold_mariano(actual, good, bad, horizon=2)
    assert test["statistic"] < 0 and test["p_value"] < 0.001
