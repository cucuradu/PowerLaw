"""VAT-aware forecasting: the schedule matches official data and is only used once it was knowable."""
import csv
import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.quant.forecasting import load_vat_schedule, rolling_origin_backtest, vat_rate

FIXTURE = Path(__file__).parent / "fixtures" / "ingestion" / "destatis_61261_0002_incl_excl_vat_1991_official.zip"
REFERENCE_MONTH = {"MONAT02": 2, "MONAT05": 5, "MONAT08": 8, "MONAT11": 11}


def test_schedule_matches_destatis_incl_excl_vat_index_ratio():
    archive = zipfile.ZipFile(FIXTURE)
    rows = list(csv.DictReader(io.StringIO(archive.read(archive.namelist()[0]).decode("utf-8-sig")), delimiter=";"))
    values = {}
    for row in rows:
        value = row["value"].replace(",", ".")
        if value.replace(".", "").isdigit():
            values[(int(row["time"]), REFERENCE_MONTH[row["1_variable_attribute_code"]], row["3_variable_attribute_code"])] = float(value)
    schedule = load_vat_schedule()
    compared = 0
    for (year, month, tax), incl in values.items():
        if tax != "STEMW1" or (year, month, "STEMW2") not in values:
            continue
        expected = (1 + vat_rate(schedule, date(year, month, 15))) / 1.19  # both indices are 2021 = 100
        # One-decimal rounding of both indices bounds the ratio error by about 0.1 / index. Up to 2004-Q4 the
        # series are chain-linked from older bases (Destatis/Bundesbank: linked via 2005-Q1), adding rounding.
        tolerance = 0.004 if year < 2005 else 0.1 / values[(year, month, "STEMW2")]
        assert incl / values[(year, month, "STEMW2")] == pytest.approx(expected, abs=tolerance), (year, month)
        compared += 1
    assert compared >= 140


def test_rate_change_is_only_known_after_promulgation():
    schedule = load_vat_schedule()
    feb_2007 = date(2007, 2, 15)
    assert vat_rate(schedule, feb_2007) == 0.19
    assert vat_rate(schedule, feb_2007, known_at=datetime(2006, 6, 29, 12, tzinfo=timezone.utc)) == 0.16
    assert vat_rate(schedule, feb_2007, known_at=datetime(2006, 7, 1, tzinfo=timezone.utc)) == 0.19
    aug_2020 = date(2020, 8, 15)
    assert vat_rate(schedule, aug_2020, known_at=datetime(2020, 6, 15, tzinfo=timezone.utc)) == 0.19
    assert vat_rate(schedule, aug_2020, known_at=datetime(2020, 7, 1, tzinfo=timezone.utc)) == 0.16


def test_vat_model_anticipates_a_legislated_rate_step():
    schedule = load_vat_schedule()
    rows, ex_vat = [], 100.0
    for i in range(80):  # 1995-Q1 .. 2014-Q4, smooth seasonal ex-VAT growth
        year, quarter = 1995 + i // 4, i % 4 + 1
        ex_vat *= 1 + (0.4 + 0.3 * (quarter == 1)) / 100
        rate = vat_rate(schedule, date(year, 3 * quarter - 1, 15))
        end = datetime(year + (quarter == 4), 1 if quarter == 4 else 3 * quarter + 1, 1, tzinfo=timezone.utc)
        rows.append({"period_start": f"{year}-{3 * quarter - 2:02d}-01", "value": ex_vat * (1 + rate),
                     "available_at": end + timedelta(days=10)})
    origin = datetime(2006, 12, 31, 23, 59, 59, tzinfo=timezone.utc)  # target 2007-Q1, after the 2006 law
    result = rolling_origin_backtest(rows, [origin], fit_start="1995-01-01", vat_schedule=schedule)
    (row,) = result["predictions"]
    assert row["actual"] == pytest.approx(100 * (1.007 * 1.19 / 1.16 - 1), abs=1e-6)
    assert abs(row["forecasts"]["ar4_seasonal_vat"] - row["actual"]) < 0.05
    assert abs(row["forecasts"]["ar4"] - row["actual"]) > 2.0
