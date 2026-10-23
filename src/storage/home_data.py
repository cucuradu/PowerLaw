"""Everything the home page shows, read from the report files the weekly update writes (never from the store).

Each function turns report JSON into plain rows for one chart, so the page only draws and stays fast.
"""
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import get_settings, primary_target_series_id

REPORTS = {"forecast": "forecast-latest.json", "projections": "projections-latest.json", "horizon": "horizon-latest.json",
           "costs": "cost-scenarios-latest.json", "exposure": "trade-cost-sensitivity-latest.json",
           "scores": "forecast-ledger-scores.json"}
HORIZONS = {4: "1 year", 8: "2 years", 12: "3 years"}
SCENARIOS = {"low": "Materials weak", "central": "Materials typical", "high": "Materials strong", "record": "Like 2021-23"}


def reports_dir() -> Path:
    return Path(get_settings().data_dir) / "reports"


def report_versions(folder: Optional[Path] = None) -> tuple:
    """Modification times of the report files (cache key: the page reloads data only when a report changes)."""
    folder = folder or reports_dir()
    return tuple((folder / name).stat().st_mtime if (folder / name).exists() else 0.0
                 for name in (*REPORTS.values(), "update-log.jsonl"))


def load_reports(folder: Optional[Path] = None) -> Dict[str, Any]:
    """{key: report dict or None} for REPORTS, plus 'failed_steps' and 'last_update' from the update log."""
    folder = folder or reports_dir()
    out: Dict[str, Any] = {}
    for key, name in REPORTS.items():
        try:
            out[key] = json.loads((folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out[key] = None
    out["failed_steps"], out["last_update"] = [], None
    log = folder / "update-log.jsonl"
    if log.exists() and log.stat().st_size:
        last = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        out["last_update"] = last["run_at"]
        out["failed_steps"] = [s["step"] for s in last["steps"] if s.get("exit_code", 1) != 0]
    return out


def quarter_label(period_start: str) -> str:
    return f"{period_start[:4]}-Q{(int(period_start[5:7]) - 1) // 3 + 1}"


def headline(reports: Dict[str, Any]) -> Dict[str, Any]:
    """Latest level and change, next-quarter forecast, and the published 1-3 year changes for the target."""
    forecast = reports.get("forecast") or {}
    history = forecast.get("history") or []
    out: Dict[str, Any] = {"latest": None, "next": None, "years": {}}
    if len(history) >= 2:
        last, before = history[-1], history[-2]
        out["latest"] = {"quarter": quarter_label(last["period_start"]), "value": last["value"],
                         "change_pct": 100 * (last["value"] / before["value"] - 1)}
    if forecast.get("forecast_growth_pct") is not None:
        out["next"] = {"quarter": quarter_label(forecast["target_period_start"]), "change_pct": forecast["forecast_growth_pct"],
                       "range": forecast.get("interval_growth_pct")}
    for f in published_horizon(reports):
        out["years"][f["horizon_quarters"]] = {"change_pct": f["growth_pct"], "range": f["interval_growth_pct"],
                                               "quarter": quarter_label(f["target_period_start"])}
    return out


def published_horizon(reports: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The target's published 1-3 year forecasts (not the shadow models)."""
    horizon = reports.get("horizon") or {}
    return sorted((f for f in horizon.get("forecasts", []) if f["series_id"] == primary_target_series_id()
                   and not f.get("shadow")), key=lambda f: f["horizon_quarters"])


def _quarter_date(period_start: str) -> str:
    """Middle of the quarter, for plotting."""
    year, month = int(period_start[:4]), int(period_start[5:7])
    return date(year, month + 1, 15).isoformat()


def price_path(reports: Dict[str, Any], since: str = "2000-01-01") -> Dict[str, List[Dict[str, Any]]]:
    """Rows for the main chart: 'history' (index level), 'forecast' (central path from the last release) and 'band'
    (likely range in index points), all with an ISO 'date'."""
    forecast = reports.get("forecast") or {}
    history = [h for h in forecast.get("history") or [] if h["period_start"] >= since]
    out = {"history": [{"date": _quarter_date(h["period_start"]), "quarter": quarter_label(h["period_start"]),
                        "level": h["value"]} for h in history], "forecast": [], "band": []}
    if not history:
        return out
    base = history[-1]
    start = {"date": _quarter_date(base["period_start"]), "quarter": quarter_label(base["period_start"]),
             "level": base["value"], "low": base["value"], "high": base["value"], "label": "Latest release"}
    points = []
    if forecast.get("forecast_growth_pct") is not None:
        g, rng = forecast["forecast_growth_pct"], forecast.get("interval_growth_pct") or [None, None]
        points.append({"period_start": forecast["target_period_start"], "growth": g, "range": rng, "label": "Next quarter"})
    for f in published_horizon(reports):
        if f["base_period_start"] == base["period_start"]:
            points.append({"period_start": f["target_period_start"], "growth": f["growth_pct"],
                           "range": f["interval_growth_pct"] or [None, None], "label": HORIZONS.get(f["horizon_quarters"], "")})
    level = lambda pct: None if pct is None else base["value"] * (1 + pct / 100)
    out["forecast"] = [start] + [{"date": _quarter_date(p["period_start"]), "quarter": quarter_label(p["period_start"]),
                                  "level": level(p["growth"]), "low": level(p["range"][0]), "high": level(p["range"][1]),
                                  "change_pct": p["growth"], "label": p["label"]} for p in points]
    out["band"] = [r for r in out["forecast"] if r["low"] is not None and r["high"] is not None]
    return out


def scenario_rows(reports: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Long rows {horizon, scenario, change_pct} for the cost what-if chart (target)."""
    costs = reports.get("costs") or {}
    rows = []
    for f in costs.get("forecasts", []):
        label = HORIZONS.get(f["horizon_quarters"])
        if f["model"] == "cost_inputs":
            rows.append({"horizon": label, "scenario": "Current signals", "change_pct": f["growth_pct"]})
        for key, value in (f.get("scenarios") or {}).items():
            rows.append({"horizon": label, "scenario": SCENARIOS[key], "change_pct": value})
    return rows


def accuracy_rows(reports: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Typical miss (MAE, points) of the published forecast vs simple rules, per horizon, on past data."""
    rows = []
    forecast = reports.get("forecast") or {}
    record = forecast.get("track_record") or {}
    published = forecast.get("model")
    for name, label in ((published, "Our forecast"), ("mean_4q", "Last year's average"), ("no_change", "No change")):
        if name in record and record[name].get("mae") is not None:
            rows.append({"horizon": "Next quarter", "method": label, "miss": record[name]["mae"]})
    horizon = reports.get("horizon") or {}
    for key, h in (horizon.get("target") or {}).items():
        label = HORIZONS.get(int(key[1:]))
        mae = (h.get("holdout") or {}).get("mae") or {}
        for name, method in ((h.get("chosen"), "Our forecast"), ("drift_10y", "10-year trend"), ("no_change", "No change")):
            if mae.get(name) is not None:
                rows.append({"horizon": label, "method": method, "miss": mae[name]})
    return rows


def projection_rows(reports: Dict[str, Any], metric: str) -> List[Dict[str, Any]]:
    """Next-quarter projections of one metric ('output_price' trades, 'producer_price' materials and energy)."""
    projections = (reports.get("projections") or {}).get("projections", [])
    rows = []
    for p in projections:
        if p["measures"].get("metric") != metric or p["growth_pct"] is None:
            continue
        test = p["test"]
        rng = p.get("interval_growth_pct") or [None, None]
        rows.append({"name": p["name"].replace("Construction prices excl. VAT: ", "").replace("Material prices: ", ""),
                     "change_pct": p["growth_pct"], "low": rng[0], "high": rng[1], "quarter": quarter_label(p["target_period_start"]),
                     "reliable": test["mae"] < test["mae_no_change"], "miss": test["mae"]})
    return sorted(rows, key=lambda r: -r["change_pct"])


def exposure_rows(reports: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-trade change for +10% material or labour costs over a year (long rows for a grouped bar chart)."""
    exposure = reports.get("exposure") or {}
    rows = []
    for t in exposure.get("trades", []):
        rows.append({"trade": t["name"], "cost": "Materials +10%", "change_pct": 10 * t["material"], "order": -t["material"]})
        rows.append({"trade": t["name"], "cost": "Labour +10%", "change_pct": 10 * t["labour"], "order": -t["material"]})
    return rows
