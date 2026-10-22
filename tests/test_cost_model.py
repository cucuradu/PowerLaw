"""Cost build-up: prices that follow costs are reproduced when costs are known; scenarios use only public costs."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.quant.cost_model import cost_scenarios

UTC = timezone.utc
SCHEDULE = [{"effective_from": "1968-07-01", "promulgated_at": "1968-01-01", "rate": 0.19}]


def quarters(values, lag_days, start=1995):
    out = []
    for i, v in enumerate(values):
        begin = datetime(start + i // 4, 3 * (i % 4) + 1, 1, tzinfo=UTC)
        out.append({"period_start": begin.date().isoformat(), "value": float(v), "available_at": begin + timedelta(days=90 + lag_days)})
    return out


def test_conditional_reproduces_prices_that_follow_costs_and_scenarios_ignore_unpublished_costs():
    rng = np.random.default_rng(2)
    m = 100 * np.exp(np.cumsum(rng.normal(0.01, 0.02, 120)))
    lab = 100 * np.exp(np.cumsum(rng.normal(0.007, 0.003, 120)))
    price = np.exp(0.6 * np.log(m) + 0.4 * np.log(lab)) * 1.19
    origin = datetime(2015, 2, 1, tzinfo=UTC)
    run = lambda material: cost_scenarios(quarters(price, 10), material, quarters(lab, 120), [origin], 4, SCHEDULE,
                                          live_origin=origin)
    result = run(quarters(m, 120))
    row = result["predictions"][0]
    assert row["conditional"] == pytest.approx(row["actual"], abs=1e-9)
    assert row["scenarios"]["low"] < row["scenarios"]["central"] < row["scenarios"]["high"] <= row["scenarios"]["record"]
    shocked = [{**r, "value": r["value"] * 2} if r["available_at"] > origin else r for r in quarters(m, 120)]
    assert run(shocked)["live"]["scenarios"] == result["live"]["scenarios"]
