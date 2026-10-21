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


def test_next_release_rule_forecasts_the_first_unreleased_quarter_live():
    target, predictor, schedule = _synthetic()
    live_origin = datetime(2015, 7, 5, tzinfo=timezone.utc)  # 2015-Q2 is released only on 2015-07-11
    result = rolling_origin_backtest(
        target, [o + timedelta(days=20) for o in _origins(2010, 2014)], fit_start="1995-01-01", vat_schedule=schedule,
        predictors={"x": {"levels": predictor, "frequency": "QUARTERLY", "transform": "qoq"}},
        predictor_sets={"x": ["x"]}, target_rule="next_release", live_origin=live_origin)
    row = result["predictions"][0]  # origin 2010-04-20: 2010-Q1 released 2010-04-10, so next release is 2010-Q2
    assert (row["last_observed_period_start"], row["target_period_start"]) == ("2010-01-01", "2010-04-01")
    live = result["live"]
    assert "actual" not in live
    assert (live["last_observed_period_start"], live["target_period_start"]) == ("2015-01-01", "2015-04-01")
    assert live["forecasts"]["arx_vat_x"] is not None and live["interval_half_width"]["arx_vat_x"] is not None
    assert result["models"]["arx_vat_x"]["mae"] < result["models"]["ar4_seasonal_vat_direct"]["mae"]


def test_pooled_seasonal_ar_moves_only_when_the_prior_disagrees():
    import numpy as np
    from src.quant.forecasting import _sar_forecast
    rng = np.random.default_rng(3)
    growth = {q: 0.5 + 0.4 * np.sin(q) + rng.normal(0, 0.2) for q in range(8000, 8080)}
    last, target = 8079, 8080
    plain = _sar_forecast(growth, last, target, 8000, 4)
    quarters = list(range(8004, 8080))
    X = np.array([[1.0] + [growth[q - k] for k in range(1, 5)] + [float(q % 4 == k) for k in (1, 2, 3)] for q in quarters])
    ols = np.linalg.lstsq(X, np.array([growth[q] for q in quarters]), rcond=None)[0]
    assert abs(_sar_forecast(growth, last, target, 8000, 4, prior=ols, prior_quarters=1e4) - plain) < 1e-6
    assert abs(_sar_forecast(growth, last, target, 8000, 4, prior=np.zeros(8), prior_quarters=0) - plain) < 1e-9
    assert abs(_sar_forecast(growth, last, target, 8000, 4, prior=np.zeros(8), prior_quarters=1e4) - plain) > 0.05


def test_adaptive_intervals_widen_after_a_volatility_jump():
    import numpy as np
    from datetime import datetime, timedelta, timezone
    from src.quant.forecasting import rolling_origin_backtest
    rng = np.random.default_rng(5)
    level, levels = 100.0, []
    for i in range(160):
        level *= 1 + (0.005 + rng.normal(0, 0.003 if i < 110 else 0.02))
        start = datetime(1980 + i // 4, 3 * (i % 4) + 1, 1, tzinfo=timezone.utc)
        levels.append({"period_start": start.date().isoformat(), "value": level, "available_at": start + timedelta(days=100)})
    origins = [datetime(1995 + i // 4, 3 * (i % 4) + 1, 15, tzinfo=timezone.utc) for i in range(100)]
    result = rolling_origin_backtest(levels, origins, fit_start="1980-01-01", target_rule="next_release")
    after = [r for r in result["predictions"] if r["origin"].year >= 2010]
    width = lambda m: np.mean([r["interval_half_width_by"][m]["ar4"] for r in after])
    assert width("recent") > width("split") and width("aci") > width("split")


def test_projection_pool_uses_only_quarters_public_at_the_origin():
    import numpy as np
    from datetime import datetime, timedelta, timezone
    from scripts.ops.project_series import pooled_prior
    from src.quant.forecasting import rolling_origin_backtest
    rng = np.random.default_rng(7)

    def series(n, shift):
        level, out = 100.0, []
        for i in range(n):
            level *= 1 + 0.01 * (1 + np.sin(i + shift)) + rng.normal(0, 0.002)
            start = datetime(1995 + i // 4, 3 * (i % 4) + 1, 1, tzinfo=timezone.utc)
            out.append({"period_start": start.date().isoformat(), "value": level, "available_at": start + timedelta(days=150)})
        return out
    pool = {name: series(100, shift) for shift, name in enumerate("abcd")}
    origin, later = datetime(2015, 1, 15, tzinfo=timezone.utc), datetime(2020, 1, 15, tzinfo=timezone.utc)
    revised = {**pool, "b": [{**r, "value": r["value"] * (1 + rng.normal(0, 0.05))} if r["available_at"] > origin else r
                             for r in pool["b"]]}  # values published after the origin change
    assert np.allclose(pooled_prior(pool)(origin), pooled_prior(revised)(origin))
    assert not np.allclose(pooled_prior(pool)(later), pooled_prior(revised)(later))
    result = rolling_origin_backtest(pool["a"], [origin], fit_start="1995-01-01", target_rule="next_release",
                                     panel_prior=pooled_prior(pool))
    assert result["predictions"][0]["forecasts"]["ar4_seasonal_pooled"] is not None
