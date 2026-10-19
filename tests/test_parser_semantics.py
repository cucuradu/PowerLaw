"""Provider value semantics: symbols, decimals, units, statuses, vintages and calendar dates.

Each parser must keep what the provider said and leave unknown things unknown.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.scrapers.statistics import (
    _clean_number,
    normalize_series_response,
    parse_eurostat_jsonstat,
    parse_sdmx_csv,
    parse_smard_json,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ingestion"
FETCHED = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)
TARGET_ROW_PREFIX = "BPNBK;Wohngebäude;BAUAR4;Bauarbeiten (Hochbau);BAULEISTBW;"


def test_clean_number_handles_german_and_sdmx_decimal_conventions():
    assert _clean_number("132,6") == 132.6
    assert _clean_number("1.234,5") == 1234.5
    assert _clean_number("1.0845") == 1.0845
    assert _clean_number("-0,4") == -0.4


def _destatis_csv_with_target_values(values):
    """Official 61261-0002 flat file with the target row's value replaced, one row per value."""
    lines = (FIXTURES / "destatis_61261_0002_2025_official.csv").read_bytes().decode("utf-8-sig").splitlines()
    header = lines[0]
    target = next(line for line in lines[1:] if TARGET_ROW_PREFIX in line and ";STEMW1;" in line)
    cols = header.split(";")
    value_idx = cols.index("value")
    rows = []
    for value in values:
        parts = target.split(";")
        parts[value_idx] = value
        rows.append(";".join(parts))
    return ("﻿" + "\n".join([header, *rows]) + "\n").encode("utf-8")


@pytest.mark.parametrize("raw,value,missing_reason,status", [
    ("...", None, "NOT_YET_PUBLISHED", None),
    (".", None, "UNKNOWN_OR_CONFIDENTIAL", None),
    ("-", None, "NOTHING_REPORTED", None),
    ("x", None, "NOT_APPLICABLE", None),
    ("/", None, "UNRELIABLE", None),
    ("132,6p", 132.6, None, "p"),
    ("132,6r", 132.6, None, "r"),
    ("(132,6)", 132.6, None, "LIMITED_RELIABILITY"),
    ("1.234,5", 1234.5, None, None),
])
def test_destatis_symbols_follow_the_official_legend(raw, value, missing_reason, status):
    (obs,) = normalize_series_response("DESTATIS_GENESIS", "61261-0002", _destatis_csv_with_target_values([raw]), FETCHED)
    assert obs.value == value
    assert obs.missing_reason == missing_reason
    assert obs.provider_status == status
    assert obs.original_value_text == raw


def test_destatis_unparseable_value_goes_to_review_instead_of_failing_the_batch():
    (obs,) = normalize_series_response("DESTATIS_GENESIS", "61261-0002", _destatis_csv_with_target_values(["n/a?"]), FETCHED)
    assert obs.value is None
    assert obs.missing_reason == "UNPARSEABLE_PROVIDER_VALUE"
    assert obs.quality_state == "needs_review"


def test_destatis_table_json_keeps_years_before_2000():
    envelope = json.loads((FIXTURES / "destatis_61261_0002_table_official.json").read_text(encoding="utf-8"))
    content = envelope["Object"]["Content"]
    envelope["Object"]["Content"] = content.replace("\n2025;Wohngebäude;Bauleistungen am Bauwerk;",
                                                    "\n1995;Wohngebäude;Bauleistungen am Bauwerk;", 1)
    observations = normalize_series_response("DESTATIS_GENESIS", "61261-0002", json.dumps(envelope).encode(), FETCHED)
    assert any(o.period_start == "1995-01-01" for o in observations)


def test_sdmx_does_not_invent_status_or_guess_unit_from_key_text():
    # "ZAR" is the South African rand, not an interest-rate marker.
    raw = b"KEY,FREQ,TIME_PERIOD,OBS_VALUE\nEXR.M.ZAR.EUR.SP00.A,M,2024-05,20.1\n"
    (obs,) = parse_sdmx_csv(raw, FETCHED)
    assert obs.provider_status is None
    assert obs.unit == "UNSPECIFIED"
    assert obs.value == 20.1


def test_bundesbank_official_wide_format_reads_unit_vintage_and_flags():
    content = (FIXTURES / "bundesbank_bbsis_pfandbrief_1y_official.csv").read_bytes()
    key = "BBSIS.M.I.ZAR.GD.EUR.S122.B.A100.R01XX.R.A.A._Z._Z.A"
    observations = normalize_series_response("BUNDESBANK_SDMX", key, content, FETCHED)
    assert {o.series_id for o in observations} == {key}
    first = min(observations, key=lambda o: o.period_start)
    assert (first.period_start, first.value, first.unit) == ("2025-10-01", 2.41, "PERCENT")
    # "Stand vom;01.10.2026 10:32:50 Uhr" is the provider's release timestamp (Europe/Berlin, CEST).
    assert first.source_updated_at == datetime(2026, 10, 1, 8, 32, 50, tzinfo=timezone.utc)
    assert first.vintage_id == "2026-10-01T10:32:50+02:00"
    assert all(o.provider_status is None for o in observations)  # empty _FLAGS column


def test_smard_daily_values_are_dated_by_berlin_calendar_day():
    content = (FIXTURES / "smard_4169_de_day_2025_official.json").read_bytes()
    observations = parse_smard_json(content, FETCHED, requested_key="SMARD|MARKET_DATA_DAY_AHEAD_DE_LU")
    assert len(observations) == 365
    first = min(observations, key=lambda o: o.period_start)
    last = max(observations, key=lambda o: o.period_start)
    assert (first.period_start, first.period_end_exclusive, first.value) == ("2025-01-01", "2025-01-02", 0.95)
    assert last.period_start == "2025-12-31"
    # meta_data.created is the provider file timestamp.
    assert first.source_updated_at == datetime.fromtimestamp(1767183881820 / 1000, tz=timezone.utc)


def _jsonstat(dimensions, values, status=None):
    ids = list(dimensions)
    return json.dumps({
        "id": ids, "size": [len(v) for v in dimensions.values()],
        "dimension": {d: {"category": {"index": {c: i for i, c in enumerate(cats)}}} for d, cats in dimensions.items()},
        "value": values, "status": status or {}, "updated": "2026-10-16T11:00:00+0200",
    }).encode()


def test_eurostat_missing_unit_dimension_is_not_assumed_to_be_2021_base():
    payload = _jsonstat({"geo": ["DE"], "time": ["2025-Q1"]}, {"0": 130.0})
    (obs,) = parse_eurostat_jsonstat(payload, FETCHED, requested_key="sts_copi_q")
    assert obs.source_unit == ""
    assert obs.unit == "UNSPECIFIED"


def test_eurostat_missing_values_use_provider_status():
    payload = _jsonstat({"geo": ["DE"], "unit": ["I21"], "time": ["2025-Q1", "2025-Q2"]}, {}, status={"0": "c"})
    obs = parse_eurostat_jsonstat(payload, FETCHED, requested_key="sts_copi_q")
    assert [o.missing_reason for o in obs] == ["CONFIDENTIAL", "NOT_AVAILABLE"]


def test_eurostat_unknown_period_format_is_rejected_not_guessed():
    payload = _jsonstat({"geo": ["DE"], "unit": ["I21"], "time": ["2025W07"]}, {"0": 1.0})
    with pytest.raises(ValueError, match="period"):
        parse_eurostat_jsonstat(payload, FETCHED, requested_key="sts_copi_q")


def test_destatis_series_kind_comes_from_the_registry_selection():
    from src.config import load_data_sources_registry
    from src.ingestion import _make_stats_parser
    from src.schemas.ingestion import SourceDocument
    registry = load_data_sources_registry()
    selection = registry["sources"]["DESTATIS_GENESIS"]["context_series"]["wood_products"]
    content = (FIXTURES / "destatis_61241_0004_2025_official.csv").read_bytes()
    document = SourceDocument.from_content("DESTATIS_GENESIS", "https://genesis.destatis.de/x", "Destatis",
                                           content, "b", data_origin="test", available_at=FETCHED)
    parsed = _make_stats_parser("DESTATIS_GENESIS")(
        {"series_key": "61241-0004", "required_series_keys": [selection["provider_series_key"]]}, content, document)
    (definition,) = parsed["series"]
    assert definition.measurement_kind == "MATERIAL_PPI_CONTEXT"
    assert definition.frequency == "MONTHLY"


def test_destatis_calendar_quarter_variable_is_quarterly_not_annual():
    """61261-0014 and 62221-0002 use QUARTG (calendar quarters), not QUARM1 (reference months)."""
    header = ("statistics_code;statistics_label;time_code;time_label;time;1_variable_code;1_variable_label;"
              "1_variable_attribute_code;1_variable_attribute_label;2_variable_code;2_variable_label;"
              "2_variable_attribute_code;2_variable_attribute_label;value;value_unit;value_variable_code;value_variable_label")
    rows = [f"61261;x;JAHR;Jahr;2025;QUARTG;Quartale;QUART{q};{q}. Quartal;DINSG;Deutschland;DG;Deutschland;"
            f"12{q},5;2021=100;KOS022;Baukostenindizes" for q in (1, 2, 3, 4)]
    payload = ("\n".join([header, *rows]) + "\n").encode("utf-8")
    obs = sorted(normalize_series_response("DESTATIS_GENESIS", "61261-0014", payload, FETCHED), key=lambda o: o.period_start)
    assert [(o.period_start, o.period_end_exclusive) for o in obs] == [
        ("2025-01-01", "2025-04-01"), ("2025-04-01", "2025-07-01"), ("2025-07-01", "2025-10-01"), ("2025-10-01", "2026-01-01")]
    assert obs[0].series_id == "61261-0014|DINSG=DG|KOS022|2021=100"
