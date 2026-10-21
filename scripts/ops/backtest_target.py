"""Rolling-origin backtest of next-quarter growth for the primary target on release-dated committed data.

Usage: python -m scripts.ops.backtest_target [--first-origin 2006-03-31] [--fit-start 1991-01-01] [--output PATH]
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config import REPO_ROOT, get_settings, load_data_sources_registry, primary_target_series_id
from src.quant.forecasting import diebold_mariano, load_vat_schedule, rolling_origin_backtest
from src.storage.dual_layer_store import DualLayerStore

BERLIN = ZoneInfo("Europe/Berlin")


def quarter_end_origins(first: str, last: datetime) -> list[datetime]:
    """Last second (UTC) of every quarter from the quarter containing `first` until `last`."""
    index = int(first[:4]) * 4 + (int(first[5:7]) - 1) // 3
    origins = []
    while True:
        year, quarter = divmod(index + 1, 4)
        origin = datetime(year, 3 * quarter + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
        if origin > last:
            return origins
        origins.append(origin)
        index += 1


PANEL_LAG_DAYS = 100  # assumption: Eurostat sts_copi_q is published about 90 days after the quarter (rule, not evidence)


def panel_prior_builder(store: DualLayerStore, now: datetime, order: int = 4, since: int = 2000):
    """Pooled seasonal-AR coefficients from the other EU countries' new-home price growth, using only quarters
    assumed public at the origin (quarter end + PANEL_LAG_DAYS). Germany's own Eurostat copy is excluded."""
    import numpy as np
    context = load_data_sources_registry()["sources"]["EUROSTAT_STS"]["context_series"]
    ids = {s["provider_series_key"]: s["dimensions"]["geo"] for name, s in context.items()
           if name.startswith("eu_new_home_prices_") and s["dimensions"]["geo"] != "DE"}
    levels = {}
    for row in store.query_observations(cutoff=now, mode="public_as_of", series_ids=list(ids)).to_pylist():
        if row["value"] is not None:
            q = int(row["period_start"][:4]) * 4 + (int(row["period_start"][5:7]) - 1) // 3
            levels.setdefault(ids[row["series_id"]], {})[q] = row["value"]
    growth = {geo: {q: 100 * (v / lv[q - 1] - 1) for q, v in lv.items() if q - 1 in lv} for geo, lv in levels.items()}
    public = lambda q: datetime(q // 4 + (q % 4 == 3), (q % 4 + 1) % 4 * 3 + 1, 1, tzinfo=timezone.utc) + timedelta(days=PANEL_LAG_DAYS)
    geos, cache = sorted(growth), {}

    def prior(origin):
        known = max((q for g in growth.values() for q in g if public(q) <= origin), default=None)
        if known is None:
            return None
        if known not in cache:
            x, y = [], []
            for i, geo in enumerate(geos):
                g = growth[geo]
                for q in g:
                    lags = [g.get(q - k) for k in range(1, order + 1)]
                    if since * 4 <= q <= known and None not in lags and abs(g[q]) < 50 and max(map(abs, lags)) < 50:
                        x.append([1.0] + lags + [float(q % 4 == k) for k in (1, 2, 3)] + [float(j == i) for j in range(1, len(geos))])
                        y.append(g[q])
            cache[known] = np.linalg.lstsq(np.array(x), np.array(y), rcond=None)[0][:order + 4] if len(y) > 200 else None
        return cache[known]
    return prior


def load_predictors(store: DualLayerStore, revisions: list) -> dict:
    """Predictor levels with model-layer availability from config/predictor_release_lags.json.

    'evidence': earliest committed availability of any revision (release-dated by archived vintages).
    'rule' (assumption): end of the Berlin day lag_days after the period end; the fetch date is ignored.
    """
    config = json.loads((REPO_ROOT / "config" / "predictor_release_lags.json").read_text(encoding="utf-8"))["predictors"]
    predictors = {}
    for name, spec in config.items():
        series_ids = spec.get("series_ids") or [spec["series_id"]]
        cells = {}  # (series, period) -> earliest {value, available_at}
        for o in revisions:
            if o.series_id not in series_ids or o.value is None or o.is_retracted:
                continue
            if spec["availability"] == "rule":
                day = datetime.fromisoformat(o.period_end_exclusive).date() + timedelta(days=spec["lag_days"])
                when = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=BERLIN).astimezone(timezone.utc)
            else:
                when = store._available(o)
            cell = (o.series_id, o.period_start)
            if cell not in cells or when < cells[cell]["available_at"]:
                cells[cell] = {"value": o.value, "available_at": when}
        # A composite is the mean of its component levels, public once its last component is.
        levels = []
        for period in sorted({p for _, p in cells}):
            parts = [cells.get((sid, period)) for sid in series_ids]
            if all(parts):
                levels.append({"period_start": period, "value": sum(c["value"] for c in parts) / len(parts),
                               "available_at": max(c["available_at"] for c in parts)})
        if not levels:
            raise SystemExit(f"Predictor {name} ({series_ids}) has no committed observations")
        predictors[name] = {**spec, "levels": levels}
    return predictors


def comparisons(result: dict, reference: str, horizon: int = 2) -> dict:
    """Diebold-Mariano of every model against `reference` on the common sample (horizon 2: origin quarter unreleased)."""
    names = list(result["models"])
    common = [r for r in result["predictions"] if all(r["forecasts"][n] is not None for n in names)]
    actual = [r["actual"] for r in common]
    return {name: diebold_mariano(actual, [r["forecasts"][name] for r in common],
                                  [r["forecasts"][reference] for r in common], horizon)
            for name in names if name != reference}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--first-origin", default="2006-03-31")
    parser.add_argument("--fit-start", default="1991-01-01", help="first quarter used to fit AR models (unified Germany)")
    parser.add_argument("--output")
    args = parser.parse_args()

    store = DualLayerStore()
    target = primary_target_series_id()
    now = datetime.now(timezone.utc)
    revisions = store.query_observation_revisions(now, "system_as_of")
    levels = [{"period_start": o.period_start, "value": o.value, "available_at": store._available(o)}
              for o in revisions if o.series_id == target and o.value is not None and not o.is_retracted]
    predictors = load_predictors(store, revisions)
    origins = quarter_end_origins(args.first_origin, now)
    schedule = load_vat_schedule()
    panel_prior = panel_prior_builder(store, now)
    baseline = rolling_origin_backtest(levels, origins, fit_start=args.fit_start, vat_schedule=schedule, panel_prior=panel_prior)
    evidence = [n for n, p in predictors.items() if p["availability"] == "evidence"]
    survey = [n for n, p in predictors.items() if p.get("group") == "survey"]  # near real time, published monthly
    selected = [n for n, p in predictors.items() if p.get("in_forecast")]  # inputs of the published model
    with_predictors = rolling_origin_backtest(
        levels, origins, fit_start=args.fit_start, vat_schedule=schedule, predictors=predictors,
        predictor_sets={**{name: [name] for name in evidence + survey}, "evidence": evidence,
                        "evidence_survey": evidence + survey, "selected": selected, "all": list(predictors)})

    # Next Destatis release, judged where a user stands: 20 days into each quarter (releases come 2-13 days in).
    sets = {"evidence": evidence, "selected": selected}
    next_release = rolling_origin_backtest(
        levels, [o + timedelta(days=20) for o in origins], fit_start=args.fit_start, vat_schedule=schedule,
        predictors=predictors, predictor_sets=sets, target_rule="next_release", live_origin=now, panel_prior=panel_prior)

    def section(result, reference, horizon):
        return {**{k: result[k] for k in ("settings", "models", "intervals")},
                "diebold_mariano_vs_" + reference: comparisons(result, reference, horizon),
                "predictions": [{**row, "origin": row["origin"].isoformat(),
                                 "label_available_at": row["label_available_at"].isoformat()}
                                for row in result["predictions"]]}

    report = {"target_series_id": target, "generated_at": now.isoformat(), "store": str(get_settings().lancedb_path),
              "label": "100 * (level[target] / level[denominator] - 1); release-dated latest-vintage levels",
              "revision_note": "Values are latest vintage dated by first release; PP300 QoQ revisions first->latest: mean 0.06 pp, max 0.35 pp.",
              "predictors": {n: {k: v for k, v in p.items() if k != "levels"} for n, p in predictors.items()},
              "without_predictors": section(baseline, "mean_4q", 2),
              "with_predictors": section(with_predictors, "ar4_seasonal_vat_direct", 2),
              "next_release": section(next_release, "ar4_seasonal_vat_direct", 1)}
    reports = Path(get_settings().data_dir) / "reports"
    output = Path(args.output) if args.output else reports / f"backtest-{now:%Y%m%d}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    forecast = forecast_record(next_release, report["next_release"], levels, target, now, output)
    (reports / "forecast-latest.json").write_text(json.dumps(forecast, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "forecast": forecast, **{
        key: {"models": report[key]["models"], "intervals": report[key]["intervals"],
              "dm": {n: {"p": v["p_value"], "stat": v["statistic"]} for n, v in report[key][dm].items()}}
        for key, dm in (("without_predictors", "diebold_mariano_vs_mean_4q"),
                        ("with_predictors", "diebold_mariano_vs_ar4_seasonal_vat_direct"),
                        ("next_release", "diebold_mariano_vs_ar4_seasonal_vat_direct"))}}, indent=2))


PRIMARY_MODEL = "arx_vat_selected"  # inputs flagged in_forecast; best on both backtests 2026-10-20 (decisions.md)
REFERENCE_MODELS = ("ar4_seasonal_vat", "mean_4q", "no_change")


def forecast_record(result: dict, section: dict, levels: list, target: str, now: datetime, report_path: Path) -> dict:
    """What the dashboard shows: the live next-release forecast and the measured record of the same setup."""
    live = result["live"]
    model = PRIMARY_MODEL if live and live["forecasts"].get(PRIMARY_MODEL) is not None else "ar4_seasonal_vat"
    known = {o["period_start"]: o["value"] for o in sorted(levels, key=lambda o: o["available_at"]) if o["available_at"] <= now}
    last = live["last_observed_period_start"] if live else None
    growth, width = (live["forecasts"][model], live["interval_half_width"][model]) if live else (None, None)
    return {
        "generated_at": now.isoformat(), "target_series_id": target, "backtest_report": str(report_path),
        "model": model, "alpha": result["settings"]["alpha"],
        "last_released_period_start": last, "last_released_level": known.get(last),
        "target_period_start": live["target_period_start"] if live else None,
        "forecast_growth_pct": growth,
        "interval_growth_pct": [growth - width, growth + width] if growth is not None and width is not None else None,
        "forecast_level": known[last] * (1 + growth / 100) if growth is not None and last in known else None,
        "reference_forecasts_growth_pct": {name: live["forecasts"].get(name) for name in REFERENCE_MODELS} if live else {},
        "track_record": {name: {**section["models"][name], "interval": section["intervals"][name]}
                         for name in (model,) + REFERENCE_MODELS if name in section["models"]},
        "diebold_mariano_vs_ar4_seasonal_vat_direct": section["diebold_mariano_vs_ar4_seasonal_vat_direct"].get(model),
        "limitations": [
            "Errors and coverage are measured on past releases with only data public at each origin (rolling origin).",
            "Values are latest-vintage levels dated by first release, not first-release values.",
            "The 2021-23 cost shock produced errors of about 2 pp for every model; intervals undercover in such regimes.",
            "Orders, material and energy prices are dated by Bundesbank real-time vintages; the builders' survey is "
            "taken as public 10 days after month end (it is published at the end of the month).",
        ]}

if __name__ == "__main__":
    main()
