"""1-3 year cumulative forecasts: right label, VAT steps known by law, nothing released after the origin used."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.quant.horizon import horizon_backtest

UTC = timezone.utc


def series(n=120, growth=lambda i: 1.0, start=1990):
    level, rows = 100.0, []
    for i in range(n):
        level *= 1 + growth(i) / 100
        begin = datetime(start + i // 4, 3 * (i % 4) + 1, 1, tzinfo=UTC)
        rows.append({"period_start": begin.date().isoformat(), "value": level, "available_at": begin + timedelta(days=100)})
    return rows


def test_steady_growth_compounds_over_the_horizon():
    origins = [datetime(2012, 1, 20, tzinfo=UTC)]
    rows = horizon_backtest(series(), origins, 8, fit_start="1990-01-01")["predictions"]
    assert len(rows) == 1 and rows[0]["actual"] == pytest.approx(100 * (1.01 ** 8 - 1))
    f = rows[0]["forecasts"]
    assert f["no_change"] == 0.0
    for model in ("drift_10y", "direct_ar"):
        assert f[model] == pytest.approx(rows[0]["actual"], abs=1e-6)


def test_uses_only_values_public_at_the_origin_and_known_vat_steps():
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.5, 200)
    base = series(140, lambda i: 0.5 + noise[i])
    origin = datetime(2015, 1, 20, tzinfo=UTC)
    changed = [{**r, "value": r["value"] * 1.3} if r["available_at"] > origin else r for r in base]
    run = lambda rows: horizon_backtest(rows, [], 4, fit_start="1990-01-01", live_origin=origin)["live"]["forecasts"]
    assert run(base) == run(changed)
    schedule = [{"effective_from": "1968-01-01", "promulgated_at": "1967-06-01", "rate": 0.10},
                {"effective_from": "2015-07-01", "promulgated_at": "2014-06-01", "rate": 0.21}]
    vat = horizon_backtest(base, [], 4, fit_start="1990-01-01", vat_schedule=schedule, live_origin=origin)["live"]["forecasts"]
    assert vat["no_change"] == 0.0
    assert (1 + vat["drift_10y"] / 100) / (1 + run(base)["drift_10y"] / 100) == pytest.approx(1.21 / 1.10, rel=1e-3)


def test_outside_benchmark_compares_annual_average_levels():
    from scripts.ops.benchmark_external import annual_change
    levels = {2024 * 4 + k: 100.0 for k in range(4)} | {2025 * 4 + k: 102.0 + k for k in range(4)}
    assert annual_change(levels, 2025) == pytest.approx(3.5)
    assert annual_change(levels, 2026) is None


def test_outside_forecasts_are_sourced_and_checked():
    import json
    from src.config import REPO_ROOT
    config = json.loads((REPO_ROOT / "config" / "external_forecasts.json").read_text(encoding="utf-8"))
    for entry in config["forecasts"]:
        assert entry["url"].startswith("https://") and entry["quote"] and entry["check"] in config["check_levels"]


def test_bottom_up_growth_weights_trades_by_weight_times_level():
    from scripts.ops.hierarchy_test import aggregate
    # Laspeyres: total = sum(w * index); growth = sum(w * index * g) / sum(w * index)
    weights, level = {"a": 0.25, "b": 0.75}, {"a": 200.0, "b": 100.0}
    assert aggregate({"a": 4.0, "b": 0.0}, level, weights) == pytest.approx(4.0 * 50 / 125)
    assert aggregate({"b": 2.0}, level, weights) == pytest.approx(2.0)  # missing trades drop out, shares renormalise
