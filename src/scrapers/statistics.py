"""
Official statistics and economic time-series ingestion adapters.
Provides normalized extraction for Eurostat (JSON-stat), Destatis GENESIS (CSV/JSON),
ECB SDMX, Bundesbank SDMX, and SMARD electricity market data.
Ensures observation immutability, date boundary precision, and unit normalization.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from src.schemas.ingestion import Observation, SeriesDefinition, stable_id

logger = logging.getLogger(__name__)

# Quarter mapping for Destatis reporting months
DESTATIS_QUARTER_MAP = {
    "MONAT02": ("01-01", "04-01", "Q1"),
    "MONAT05": ("04-01", "07-01", "Q2"),
    "MONAT08": ("07-01", "10-01", "Q3"),
    "MONAT11": ("10-01", "01-01", "Q4"),
    "Februar": ("01-01", "04-01", "Q1"),
    "Mai": ("04-01", "07-01", "Q2"),
    "August": ("07-01", "10-01", "Q3"),
    "November": ("10-01", "01-01", "Q4"),
}

EUROSTAT_UNIT_MAP = {
    "I21": "2021=100",
    "I15": "2015=100",
    "PCH_PRE": "PERCENT_PREV_PERIOD",
    "PCH_SM": "PERCENT_YEAR_AGO",
}


def _clean_number(text: str) -> Optional[float]:
    """Parse a number. A comma marks German notation ("1.234,5"); otherwise "." is the decimal point (SDMX)."""
    if not text:
        return None
    val = text.strip()
    if val in ("-", ".", "...", "nan", "NaN", "None", ""):
        return None
    if "," in val:
        val = val.replace(".", "").replace(",", ".")
    try:
        return float(val)
    except ValueError:
        return None


# Destatis GENESIS legend ("Zeichenerklärung"). "-" means nothing exists ("nichts vorhanden"):
# for an index that is "no index for this cell", so it stays null rather than becoming 0.
DESTATIS_SYMBOLS = {
    "...": "NOT_YET_PUBLISHED",          # Angabe fällt später an
    ".": "UNKNOWN_OR_CONFIDENTIAL",      # Zahlenwert unbekannt oder geheim zu halten
    "-": "NOTHING_REPORTED",             # nichts vorhanden
    "x": "NOT_APPLICABLE",               # Tabellenfach gesperrt, weil Aussage nicht sinnvoll
    "/": "UNRELIABLE",                   # keine Angaben, da Zahlenwert nicht sicher genug
}
DESTATIS_FLAGS = {"p", "r", "s"}         # vorläufig, berichtigt, geschätzt


def _destatis_value(raw: str) -> Tuple[Optional[float], Optional[str], Optional[str], str]:
    """Return (value, missing_reason, provider_status, quality_state) for one Destatis cell."""
    text = raw.strip()
    if not text:
        return None, "NOT_AVAILABLE", None, "accepted"
    if text in DESTATIS_SYMBOLS:
        return None, DESTATIS_SYMBOLS[text], None, "accepted"
    status = None
    if text.startswith("(") and text.endswith(")"):
        text, status = text[1:-1].strip(), "LIMITED_RELIABILITY"   # Aussagewert eingeschränkt
    elif len(text) > 1 and text[-1] in DESTATIS_FLAGS:
        text, status = text[:-1].strip(), text[-1]
    value = _clean_number(text)
    if value is None:
        return None, "UNPARSEABLE_PROVIDER_VALUE", status, "needs_review"
    return value, None, status, "accepted"


def _period_bounds(time_period: str) -> Tuple[str, str]:
    """[start, exclusive end) for YYYY, YYYY-MM, YYYY-MM-DD, YYYY-Qn and YYYYQn; anything else is rejected."""
    import re
    from datetime import date, timedelta
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", time_period):
        day = date.fromisoformat(time_period)
        return day.isoformat(), (day + timedelta(days=1)).isoformat()
    if re.fullmatch(r"\d{4}-?Q[1-4]", time_period):
        return _quarter_bounds("", f"{time_period[:4]}-Q{time_period[-1]}")
    if re.fullmatch(r"\d{4}-\d{2}", time_period):
        return _month_bounds("", time_period)
    if re.fullmatch(r"\d{4}", time_period):
        year = int(time_period)
        return f"{year:04d}-01-01", f"{year + 1:04d}-01-01"
    raise ValueError(f"Unsupported provider period format: {time_period!r}")


def _sdmx_unit(row: Dict[str, Any]) -> Tuple[str, str]:
    """(source_unit, normalized unit) from explicit SDMX attributes only; never from key text."""
    source_unit = (row.get("UNIT") or row.get("UNIT_MEASURE") or "").strip()
    if row.get("CURRENCY") and row.get("CURRENCY_DENOM"):
        return source_unit, f"{row['CURRENCY'].strip()}_PER_{row['CURRENCY_DENOM'].strip()}"
    if source_unit:
        return source_unit, {"PCPA": "PERCENT", "PC": "PERCENT"}.get(source_unit, source_unit)
    return "", "UNSPECIFIED"


def _quarter_bounds(year_str: str, quarter_str: str) -> Tuple[str, str]:
    """Compute [inclusive_start, exclusive_end) for a quarter string like '2026-Q3'."""
    parts = quarter_str.split("-Q")
    if len(parts) == 2:
        year = int(parts[0])
        q = int(parts[1])
    else:
        year = int(year_str)
        q = int(quarter_str.replace("Q", ""))
    
    if q == 1:
        return f"{year:04d}-01-01", f"{year:04d}-04-01"
    elif q == 2:
        return f"{year:04d}-04-01", f"{year:04d}-07-01"
    elif q == 3:
        return f"{year:04d}-07-01", f"{year:04d}-10-01"
    elif q == 4:
        return f"{year:04d}-10-01", f"{year + 1:04d}-01-01"
    raise ValueError(f"Invalid quarter format: {quarter_str}")


def _month_bounds(year_str: str, month_str: str) -> Tuple[str, str]:
    """Compute [inclusive_start, exclusive_end) for a month."""
    if "-" in month_str:
        parts = month_str.split("-")
        year = int(parts[0])
        month = int(parts[1])
    elif month_str.startswith("MONAT"):
        year = int(year_str)
        month = int(month_str.replace("MONAT", ""))
    else:
        year = int(year_str)
        month = int(month_str)
    
    start = f"{year:04d}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"
    return start, end


# =====================================================================
# Eurostat JSON-stat Parser
# =====================================================================
def parse_eurostat_jsonstat(payload: bytes, fetched_at: datetime, requested_key: str = "") -> List[Observation]:
    """Parse Eurostat JSON-stat v2.0 response into Observation objects."""
    data = json.loads(payload.decode("utf-8"))
    dim_ids = data.get("id", [])
    dim_sizes = data.get("size", [])
    dimensions = data.get("dimension", {})
    values_map = data.get("value", {})
    status_map = data.get("status", {})
    no_data_positions = data.get("extension", {}).get("positions-with-no-data", {})
    if not dim_ids or len(dim_ids) != len(dim_sizes) or "time" not in dim_ids or not dimensions:
        raise ValueError("Eurostat provider response is missing the JSON-stat dimension contract")

    # Build dimension coordinate lists
    coords = []
    for dim_id in dim_ids:
        dim_info = dimensions.get(dim_id, {})
        categories = dim_info.get("category", {}).get("index", {})
        # Sort by index
        sorted_cats = categories if isinstance(categories, list) else [k for k, v in sorted(categories.items(), key=lambda x: x[1])]
        coords.append(sorted_cats)

    # Multipliers for flat index
    multipliers = [1] * len(dim_sizes)
    for i in range(len(dim_sizes) - 2, -1, -1):
        multipliers[i] = multipliers[i + 1] * dim_sizes[i + 1]

    observations = []
    import itertools
    dataset_id = requested_key if (requested_key and not requested_key.startswith("EUROSTAT")) else data.get("extension", {}).get("datasetId", "sts_copi_q")

    for indices in itertools.product(*[range(s) for s in dim_sizes]):
        flat_idx = sum(idx * mult for idx, mult in zip(indices, multipliers))
        str_idx = str(flat_idx)

        dim_vals = {dim_ids[d]: coords[d][indices[d]] for d in range(len(dim_ids))}
        unit_code = dim_vals.get("unit")
        p_start, p_end = _period_bounds(dim_vals.get("time", ""))

        norm_unit = EUROSTAT_UNIT_MAP.get(unit_code, unit_code) if unit_code else "UNSPECIFIED"
        dim_parts = [f"{d}={dim_vals[d]}" for d in sorted(dim_ids) if d != "time" and dim_vals.get(d)]
        series_id = f"EUROSTAT_STS|{dataset_id}|{'|'.join(dim_parts)}"

        val = values_map[flat_idx] if isinstance(values_map, list) and flat_idx < len(values_map) else values_map.get(str_idx) if isinstance(values_map, dict) else None
        provider_status = status_map[flat_idx] if isinstance(status_map, list) and flat_idx < len(status_map) else status_map.get(str_idx) if isinstance(status_map, dict) else status_map or None
        # Eurostat flag "c" is confidential; any other absent cell is simply not available.
        missing_reason = None if val is not None else ("CONFIDENTIAL" if provider_status == "c" else "NOT_AVAILABLE")
        num_val = float(val) if val is not None else None

        obs = Observation(
            series_id=series_id,
            period_start=p_start,
            period_end_exclusive=p_end,
            value=num_val,
            original_value_text=str(val) if val is not None else None,
            source_unit=unit_code or "",
            unit=norm_unit,
            vintage_id=data.get("updated"),
            published_at=datetime.fromisoformat(data["updated"].replace("Z", "+00:00")) if data.get("updated") else None,
            source_updated_at=datetime.fromisoformat(data["updated"].replace("Z", "+00:00")) if data.get("updated") else None,
            available_at=fetched_at,
            recorded_at=datetime.now(timezone.utc),
            provider_status=provider_status,
            missing_reason=missing_reason,
            batch_id="batch_temp",
            data_origin="live",
        )
        observations.append(obs)

    return observations


# =====================================================================
# Destatis GENESIS Parsers (CSV and Table JSON)
# =====================================================================
def parse_destatis_csv(payload: bytes, fetched_at: datetime, requested_key: str = "") -> List[Observation]:
    """Parse Destatis flat semicolon-delimited CSV (UTF-8; decoding errors fail instead of corrupting labels)."""
    text = payload.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = next(reader, None)
    if not header:
        return []

    observations = []
    # Identify key column positions
    col_map = {col.strip(): idx for idx, col in enumerate(header)}
    if not {"value", "value_unit", "time", "value_variable_code"}.issubset(col_map):
        raise ValueError("Destatis provider CSV schema is missing required columns")

    val_idx = col_map.get("value")
    val_unit_idx = col_map.get("value_unit")
    time_idx = col_map.get("time")
    val_var_code_idx = col_map.get("value_variable_code")

    for row in reader:
        if not row or len(row) <= (val_idx or 0):
            continue
        raw_val = row[val_idx].strip() if val_idx is not None else ""
        unit_str = row[val_unit_idx].strip() if val_unit_idx is not None else "INDEX"
        year_str = row[time_idx].strip()
        val_var_code = row[val_var_code_idx].strip()

        # Search for quarter or month in variable attributes
        quarter_info = None
        month_info = None
        dim_parts = []
        for i in range(1, 6):
            code_col = f"{i}_variable_code"
            attr_col = f"{i}_variable_attribute_code"
            if code_col in col_map and attr_col in col_map:
                v_code = row[col_map[code_col]].strip()
                v_attr = row[col_map[attr_col]].strip()
                if v_code == "QUARM1":
                    quarter_info = DESTATIS_QUARTER_MAP.get(v_attr)
                elif v_code == "MONAT":
                    month_info = v_attr
                else:
                    dim_parts.append(f"{v_code}={v_attr}")

        if quarter_info:
            start_m, end_m, _ = quarter_info
            y = int(year_str)
            p_start = f"{y:04d}-{start_m}"
            if start_m == "10-01":
                p_end = f"{y + 1:04d}-{end_m}"
            else:
                p_end = f"{y:04d}-{end_m}"
        elif month_info:
            p_start, p_end = _month_bounds(year_str, month_info)
        else:
            y = int(year_str)
            p_start, p_end = f"{y:04d}-01-01", f"{y + 1:04d}-01-01"

        # The flat file only names the statistic family (e.g. 61261); the requested table ID is authoritative.
        requested_table = requested_key.split("|")[0] if requested_key else ""
        table_code = requested_table if "-" in requested_table else row[0].strip()

        series_id = f"{table_code}|{'|'.join(dim_parts)}|{val_var_code}|{unit_str}"

        num_val, missing_reason, provider_status, quality_state = _destatis_value(raw_val)

        obs = Observation(
            series_id=series_id,
            period_start=p_start,
            period_end_exclusive=p_end,
            value=num_val,
            original_value_text=raw_val,
            source_unit=unit_str,
            unit=unit_str,
            available_at=fetched_at,
            recorded_at=datetime.now(timezone.utc),
            provider_status=provider_status,
            quality_state=quality_state,
            missing_reason=missing_reason,
            batch_id="batch_temp",
            data_origin="live",
        )
        observations.append(obs)

    return observations


def parse_destatis_table_json(payload: bytes, fetched_at: datetime) -> List[Observation]:
    """Parse Destatis table JSON with embedded CSV content."""
    data = json.loads(payload.decode("utf-8"))
    content = data.get("Object", {}).get("Content", "")
    if not content:
        return []

    lines = [line.strip() for line in content.split("\n") if line.strip()]
    header_months = []
    header_tax = []

    for line in lines:
        if "Februar" in line and "Mai" in line:
            header_months = [part.strip() for part in line.split(";")]
        elif "Indizes" in line and "Umsatzsteuer" in line:
            header_tax = [part.strip() for part in line.split(";")]
            break

    observations = []
    for line in lines:
        if re.match(r"^\d{4};", line):
            parts = [p.strip() for p in line.split(";")]
            if len(parts) < 4:
                continue
            year = parts[0]
            building_type = parts[1]
            work_type = parts[2]

            # Values start at index 3
            val_idx = 3
            for col_idx in range(val_idx, len(parts)):
                if col_idx >= len(header_months) or col_idx >= len(header_tax):
                    continue
                month_name = header_months[col_idx]
                tax_type = header_tax[col_idx]
                raw_val = parts[col_idx]

                q_info = DESTATIS_QUARTER_MAP.get(month_name)
                if not q_info:
                    continue
                start_m, end_m, _ = q_info
                y = int(year)
                p_start = f"{y:04d}-{start_m}"
                p_end = f"{y + 1:04d}-{end_m}" if start_m == "10-01" else f"{y:04d}-{end_m}"

                tax_code = "STEMW1" if "einschließlich" in tax_type else "STEMW2"
                b_dimension = "BAUAR1=BPNBK" if building_type == "Wohngebäude" else f"BAUAR1_LABEL={building_type}"
                w_dimension = "BAUAR4=BAULEISTBW" if work_type == "Bauleistungen am Bauwerk" else f"BAUAR4_LABEL={work_type}"

                series_id = f"61261-0002|DINSG=DG|STEMW1={tax_code}|{b_dimension}|{w_dimension}|PRE002|2021=100"
                num_val, missing_reason, provider_status, quality_state = _destatis_value(raw_val)

                obs = Observation(
                    series_id=series_id,
                    period_start=p_start,
                    period_end_exclusive=p_end,
                    value=num_val,
                    original_value_text=raw_val,
                    source_unit="2021=100",
                    unit="2021=100",
                    available_at=fetched_at,
                    recorded_at=datetime.now(timezone.utc),
                    provider_status=provider_status,
                    quality_state=quality_state,
                    missing_reason=missing_reason,
                    batch_id="batch_temp",
                    data_origin="live",
                )
                observations.append(obs)

    return observations


# =====================================================================
# SDMX CSV Parsers (ECB, Bundesbank)
# =====================================================================
SDMX_MISSING = {"", "-", ".", "...", "M", "NaN"}
BERLIN = ZoneInfo("Europe/Berlin")


def _sdmx_observation(key: str, time_period: str, obs_val: str, status: Optional[str], source_unit: str, unit: str,
                      fetched_at: datetime, **extra: Any) -> Observation:
    p_start, p_end = _period_bounds(time_period)
    missing = obs_val in SDMX_MISSING
    return Observation(
        series_id=key, period_start=p_start, period_end_exclusive=p_end,
        value=None if missing else _clean_number(obs_val), original_value_text=obs_val or None,
        source_unit=source_unit or unit, unit=unit, provider_status=status or None,
        missing_reason="NOT_AVAILABLE" if missing else None,
        available_at=fetched_at, recorded_at=datetime.now(timezone.utc),
        batch_id="batch_temp", data_origin="live", **extra)


def parse_sdmx_csv(payload: bytes, fetched_at: datetime) -> List[Observation]:
    """Parse ECB/Bundesbank SDMX CSV (comma or semicolon) or the Bundesbank wide export.

    Units come only from explicit attributes; a missing status stays None.
    """
    text = payload.decode("utf-8", errors="replace").lstrip("\ufeff")

    # 1./2. Tabular SDMX CSV with one observation per row.
    for delimiter in (",", ";"):
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        fields = set(reader.fieldnames or [])
        key_col = "KEY" if "KEY" in fields else "BBK_ID" if "BBK_ID" in fields else None
        if not key_col or not {"TIME_PERIOD", "OBS_VALUE"}.issubset(fields):
            continue
        observations = []
        for row in reader:
            time_period = (row.get("TIME_PERIOD") or "").strip()
            if not time_period:
                continue
            release = row.get("LAST_UPDATE") or row.get("LAST_UPDATED") or row.get("UPDATE_DATE")
            source_unit, unit = _sdmx_unit(row)
            observations.append(_sdmx_observation(
                (row.get(key_col) or "").strip(), time_period, (row.get("OBS_VALUE") or "").strip(),
                (row.get("OBS_STATUS") or "").strip(), source_unit, unit, fetched_at,
                vintage_id=release or None,
                source_updated_at=datetime.fromisoformat(release.replace("Z", "+00:00")) if release else None,
                conversion_lineage_json=json.dumps({"source_attributes": {k: v for k, v in row.items()
                                                    if k not in {"TIME_PERIOD", "OBS_VALUE"}}}, sort_keys=True)))
        return observations

    # 3. Bundesbank wide export: key header row, metadata rows, then "YYYY-MM;value;flags" rows.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and ";" in lines[0]:
        first_cols = [c.strip().strip('"') for c in lines[0].split(";")]
        series_id = first_cols[1] if len(first_cols) > 1 and first_cols[1] else "BUNDESBANK_SERIES"
        source_unit, unit, vintage, updated_at = "", "UNSPECIFIED", None, None
        observations = []
        for line in lines[1:]:
            parts = [c.strip().strip('"') for c in line.split(";")]
            if len(parts) < 2:
                continue
            label, value = parts[0], parts[1]
            if label.upper() in ("BBK_UNIT_ENG", "EINHEIT") and not source_unit:
                source_unit = value
                unit = "PERCENT" if value.upper() in ("PERCENT", "PROZENT") else value.upper()
            elif label == "Stand vom":
                # Provider release timestamp, Berlin local time: "01.10.2026 10:32:50 Uhr".
                stamp = datetime.strptime(value.replace("Uhr", "").strip(), "%d.%m.%Y %H:%M:%S").replace(tzinfo=BERLIN)
                vintage, updated_at = stamp.isoformat(), stamp.astimezone(timezone.utc)
            elif re.fullmatch(r"\d{4}(?:-\d{2}){0,2}", label):
                observations.append(_sdmx_observation(
                    series_id, label, value, parts[2] if len(parts) > 2 else None, source_unit, unit, fetched_at,
                    vintage_id=vintage, source_updated_at=updated_at))
        if observations:
            return observations

    raise ValueError("SDMX provider CSV schema is missing required columns")


# =====================================================================
# SMARD Electricity Market Parser
# =====================================================================
def parse_smard_csv(payload: bytes, fetched_at: datetime) -> List[Observation]:
    """Parse SMARD day-ahead electricity prices CSV."""
    text = payload.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header = next(reader, None)
    if not header:
        return []

    observations = []
    for row in reader:
        if len(row) < 3:
            continue
        start_str = row[0].strip()
        end_str = row[1].strip()
        val_str = row[2].strip()

        # Parse date: "01.05.2024 00:00"
        try:
            dt_start = datetime.strptime(start_str, "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)
            dt_end = datetime.strptime(end_str, "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)
            p_start = dt_start.strftime("%Y-%m-%dT%H:%M:%SZ")
            p_end = dt_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        num_val = _clean_number(val_str)
        obs = Observation(
            series_id="SMARD|MARKET_DATA_DAY_AHEAD_DE_LU",
            period_start=p_start,
            period_end_exclusive=p_end,
            value=num_val,
            original_value_text=val_str,
            source_unit="EUR/MWh",
            unit="EUR_PER_MWH",
            available_at=fetched_at,
            recorded_at=datetime.now(timezone.utc),
            batch_id="batch_temp",
            data_origin="live",
        )
        observations.append(obs)

    return observations


def parse_smard_json(payload: bytes, fetched_at: datetime, requested_key: str = "") -> List[Observation]:
    """Parse a SMARD chart_data file. Each timestamp is local midnight in Berlin for daily resolution."""
    from datetime import timedelta
    data = json.loads(payload.decode("utf-8"))
    created = data.get("meta_data", {}).get("created")
    updated_at = datetime.fromtimestamp(created / 1000.0, tz=timezone.utc) if created else None
    series_id = requested_key or "SMARD|MARKET_DATA_DAY_AHEAD_DE_LU"
    if not series_id.startswith("SMARD|"):
        series_id = f"SMARD|{series_id}"
    observations = []
    for item in data.get("series", []):
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        ts_ms, val = item[0], item[1]
        day = datetime.fromtimestamp(ts_ms / 1000.0, tz=BERLIN).date()
        observations.append(Observation(
            series_id=series_id,
            period_start=day.isoformat(),
            period_end_exclusive=(day + timedelta(days=1)).isoformat(),
            value=_clean_number(str(val)) if val is not None else None,
            original_value_text=str(val) if val is not None else None,
            source_unit="EUR/MWh",
            unit="EUR_PER_MWH",
            missing_reason="NOT_AVAILABLE" if val is None else None,
            source_updated_at=updated_at,
            available_at=fetched_at,
            recorded_at=datetime.now(timezone.utc),
            batch_id="batch_temp",
            data_origin="live",
        ))
    return observations


# =====================================================================
# Unified Series Normalizer and Fetcher
# =====================================================================
def normalize_series_response(
    source_id: str,
    series_key: str,
    payload: bytes,
    fetched_at: datetime
) -> List[Observation]:
    """Normalize raw bytes from any supported provider into canonical Observation records."""
    if source_id == "EUROSTAT_STS":
        return parse_eurostat_jsonstat(payload, fetched_at, requested_key=series_key)
    elif source_id == "DESTATIS_GENESIS":
        if payload.startswith(b"PK"):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                entries = archive.infolist()
                csv_entries = [entry for entry in entries if entry.filename.lower().endswith(".csv")]
                if len(csv_entries) != 1 or len(entries) > 32 or sum(entry.file_size for entry in entries) > 25 * 1024 * 1024:
                    raise ValueError("Destatis provider ZIP envelope exceeds bounded CSV contract")
                with archive.open(csv_entries[0]) as stream:
                    payload = stream.read(25 * 1024 * 1024 + 1)
                if len(payload) > 25 * 1024 * 1024:
                    raise ValueError("Destatis provider ZIP expanded size exceeds limit")
        # Check if JSON or CSV
        if payload.strip().startswith(b"{"):
            envelope = json.loads(payload)
            if str(envelope.get("Status", {}).get("Code", "0")) != "0" or not envelope.get("Object", {}).get("Content"):
                raise ValueError("Destatis provider returned an error or incomplete response envelope")
            return parse_destatis_table_json(payload, fetched_at)
        return parse_destatis_csv(payload, fetched_at, requested_key=series_key)
    elif source_id == "ECB_SDMX":
        return parse_sdmx_csv(payload, fetched_at)
    elif source_id == "BUNDESBANK_SDMX":
        return parse_sdmx_csv(payload, fetched_at)
    elif source_id == "SMARD_DE":
        if payload.strip().startswith(b"{"):
            return parse_smard_json(payload, fetched_at, requested_key=series_key)
        return parse_smard_csv(payload, fetched_at)
    else:
        raise ValueError(f"Unsupported statistics source_id: {source_id!r}")


def fetch_series(
    source_id: str,
    series_key: str,
    start: str,
    end: str
) -> List[Observation]:
    """Fetch series observations from local fixtures in offline/test mode."""
    from src.config import get_settings
    settings = get_settings()
    if settings.mode == "production":
        raise RuntimeError(f"fetch_series cannot return test fixtures in production mode for {source_id}")

    now = datetime.now(timezone.utc)
    fixtures_dir = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "ingestion"
    if source_id == "DESTATIS_GENESIS":
        target = "destatis_61261_0002_2025_official.csv" if "61261" in series_key else "destatis_61241_0004_2025_official.csv"
        path = fixtures_dir / target
        if path.exists():
            return normalize_series_response(source_id, series_key, path.read_bytes(), now)
    elif source_id == "EUROSTAT_STS":
        path = fixtures_dir / "eurostat_sts_copi_q_de_official.json"
        if path.exists():
            return normalize_series_response(source_id, series_key, path.read_bytes(), now)
    elif source_id == "ECB_SDMX":
        path = fixtures_dir / "ecb_exr_sdmx_sample.csv"
        if path.exists():
            return normalize_series_response(source_id, series_key, path.read_bytes(), now)
    elif source_id == "BUNDESBANK_SDMX":
        path = fixtures_dir / "bundesbank_bbsis_pfandbrief_1y_official.csv"
        if path.exists():
            return normalize_series_response(source_id, series_key, path.read_bytes(), now)
    elif source_id == "SMARD_DE":
        path = fixtures_dir / "smard_de_lu_power_sample.csv"
        if path.exists():
            return normalize_series_response(source_id, series_key, path.read_bytes(), now)
    return []

