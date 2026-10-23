"""Overview page data: the forecast path starts at the latest release and every chart gets plain rows."""
import json

import pytest

from src.config import primary_target_series_id
from src.storage.home_data import accuracy_rows, headline, load_reports, price_path, projection_rows, scenario_rows

TARGET = primary_target_series_id()


def reports(tmp_path):
    forecast = {"generated_at": "2026-10-23T00:00:00", "model": "arx", "target_period_start": "2026-10-01",
                "forecast_growth_pct": 1.0, "interval_growth_pct": [0.0, 2.0],
                "history": [{"period_start": "2026-04-01", "value": 100.0}, {"period_start": "2026-07-01", "value": 102.0}],
                "track_record": {"arx": {"mae": 0.4}, "no_change": {"mae": 0.8}}}
    horizon = {"forecasts": [{"series_id": TARGET, "horizon_quarters": 4, "base_period_start": "2026-07-01",
                              "target_period_start": "2027-07-01", "growth_pct": 5.0, "interval_growth_pct": [0.0, 10.0]},
                             {"series_id": TARGET, "horizon_quarters": 4, "base_period_start": "2026-07-01", "shadow": True,
                              "target_period_start": "2027-07-01", "growth_pct": 9.0, "interval_growth_pct": None}],
               "target": {"h4": {"chosen": "sar", "holdout": {"mae": {"sar": 2.0, "drift_10y": 2.5, "no_change": 4.0}}}}}
    costs = {"forecasts": [{"model": "cost_central", "horizon_quarters": 4, "growth_pct": 3.0,
                            "scenarios": {"low": 1.0, "central": 3.0, "high": 6.0, "record": 14.0}},
                           {"model": "cost_inputs", "horizon_quarters": 4, "growth_pct": 4.0}]}
    projections = {"projections": [{"name": "Construction prices excl. VAT: roofing", "measures": {"metric": "output_price"},
                                    "growth_pct": 0.5, "interval_growth_pct": [-1.0, 2.0], "target_period_start": "2026-10-01",
                                    "test": {"mae": 0.6, "mae_no_change": 1.0}}]}
    for name, data in (("forecast-latest.json", forecast), ("horizon-latest.json", horizon),
                       ("cost-scenarios-latest.json", costs), ("projections-latest.json", projections)):
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    return load_reports(tmp_path)


def test_headline_and_path_start_from_the_latest_release(tmp_path):
    data = reports(tmp_path)
    top = headline(data)
    assert top["latest"]["quarter"] == "2026-Q3" and top["latest"]["change_pct"] == pytest.approx(2.0)
    assert top["next"]["quarter"] == "2026-Q4" and top["years"][4]["change_pct"] == 5.0  # shadow model ignored
    path = price_path(data)
    assert [p["label"] for p in path["forecast"]] == ["Latest release", "Next quarter", "1 year"]
    assert path["forecast"][2]["level"] == pytest.approx(107.1) and path["forecast"][2]["high"] == pytest.approx(112.2)
    assert data["exposure"] is None and data["failed_steps"] == []


def test_chart_rows(tmp_path):
    data = reports(tmp_path)
    assert {(r["scenario"], r["change_pct"]) for r in scenario_rows(data)} >= {("Current signals", 4.0), ("Like 2021-23", 14.0)}
    assert {(r["horizon"], r["method"], r["miss"]) for r in accuracy_rows(data)} == {
        ("Next quarter", "Our forecast", 0.4), ("Next quarter", "No change", 0.8),
        ("1 year", "Our forecast", 2.0), ("1 year", "10-year trend", 2.5), ("1 year", "No change", 4.0)}
    assert projection_rows(data, "output_price")[0] | {} == {"name": "roofing", "change_pct": 0.5, "low": -1.0, "high": 2.0,
                                                              "quarter": "2026-Q4", "reliable": True, "miss": 0.6}
