"""The forecast ledger only appends, records each forecast once, detects edits and scores released outcomes."""
from datetime import datetime, timezone

import pytest

from scripts.ops.forecast_ledger import append, quarterly_levels, read_ledger, score


def entry(model="m", target="2026-10-01", growth=1.0):
    return {"kind": "target", "series_id": "s", "model": model, "made_at": "2026-10-21T00:00:00+00:00", "base_period_start": "2026-07-01",
            "base_level": 100.0, "target_period_start": target, "horizon_quarters": 1, "growth_pct": growth,
            "interval_growth_pct": [0.0, 2.0]}


def test_ledger_appends_once_and_detects_edits(tmp_path):
    path = tmp_path / "ledger.jsonl"
    assert append(path, [entry(), entry(model="n")]) == 2
    assert append(path, [entry(growth=5.0)]) == 0  # same forecast slot: the first one stands
    assert [e["model"] for e in read_ledger(path)] == ["m", "n"]
    path.write_text(path.read_text(encoding="utf-8").replace('"growth_pct": 1.0', '"growth_pct": 0.5'), encoding="utf-8")
    with pytest.raises(SystemExit):
        read_ledger(path)


def test_scores_only_targets_released_after_the_forecast():
    t = lambda day: datetime(2026, 10, day, tzinfo=timezone.utc)
    months = [{"period_start": f"2026-0{m}-01", "value": v, "available_at": t(1)} for m, v in ((7, 99.0), (8, 100.0), (9, 101.0))]
    levels = quarterly_levels(months + [{"period_start": "2026-10-01", "value": 103.0, "available_at": t(1)}])
    assert levels == {"2026-07-01": (100.0, t(1))}  # Q4 has one month: not released as a quarter yet
    assert score([entry()], {"s": levels}) == []
    released = lambda day: {"s": {**levels, "2026-10-01": (101.5, t(day))}}
    assert score([entry()], released(20)) == []  # public before the forecast was made (2026-10-21)
    scored = score([entry()], released(25))
    assert scored[0]["actual_growth_pct"] == pytest.approx(1.5) and scored[0]["in_interval"] is True
    revised = quarterly_levels([{"period_start": "2026-07-01", "value": v, "available_at": t(d)} for v, d in ((100.0, 2), (101.0, 9))])
    assert revised == {"2026-07-01": (101.0, t(2))}  # latest value, first release time


def test_track_record_reports_failed_update_steps(tmp_path):
    import json
    from src.storage.dashboard_data import load_track_record
    (tmp_path / "update-log.jsonl").write_text(json.dumps({"run_at": "2026-10-22T06:00:00", "steps": [
        {"step": "ingest DESTATIS", "exit_code": 0}, {"step": "scripts.ops.horizon_forecast", "exit_code": 1},
        {"step": "scripts.ops.project_series", "skipped": "Destatis update failed"}]}) + "\n", encoding="utf-8")
    record = load_track_record(tmp_path)
    assert record["failed"] == ["scripts.ops.horizon_forecast", "scripts.ops.project_series"] and record["scores"] is None
