"""Rolling-origin backtest of next-quarter growth for the primary target on release-dated committed data.

Usage: python -m scripts.ops.backtest_target [--first-origin 2006-03-31] [--fit-start 1991-01-01] [--output PATH]
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config import REPO_ROOT, get_settings, primary_target_series_id
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


def load_predictors(store: DualLayerStore, revisions: list) -> dict:
    """Predictor levels with model-layer availability from config/predictor_release_lags.json.

    'evidence': earliest committed availability of any revision (release-dated by archived vintages).
    'rule' (assumption): end of the Berlin day lag_days after the period end; the fetch date is ignored.
    """
    config = json.loads((REPO_ROOT / "config" / "predictor_release_lags.json").read_text(encoding="utf-8"))["predictors"]
    predictors = {}
    for name, spec in config.items():
        earliest = {}
        for o in revisions:
            if o.series_id != spec["series_id"] or o.value is None or o.is_retracted:
                continue
            if spec["availability"] == "rule":
                day = datetime.fromisoformat(o.period_end_exclusive).date() + timedelta(days=spec["lag_days"])
                when = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=BERLIN).astimezone(timezone.utc)
            else:
                when = store._available(o)
            if o.period_start not in earliest or when < earliest[o.period_start]["available_at"]:
                earliest[o.period_start] = {"period_start": o.period_start, "value": o.value, "available_at": when}
        if not earliest:
            raise SystemExit(f"Predictor {name} ({spec['series_id']}) has no committed observations")
        predictors[name] = {**spec, "levels": list(earliest.values())}
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
    baseline = rolling_origin_backtest(levels, origins, fit_start=args.fit_start, vat_schedule=schedule)
    evidence = [n for n, p in predictors.items() if p["availability"] == "evidence"]
    with_predictors = rolling_origin_backtest(
        levels, origins, fit_start=args.fit_start, vat_schedule=schedule, predictors=predictors,
        predictor_sets={"evidence": evidence, "all": list(predictors)})

    def section(result, reference):
        return {**{k: result[k] for k in ("settings", "models", "intervals")},
                "diebold_mariano_vs_" + reference: comparisons(result, reference),
                "predictions": [{**row, "origin": row["origin"].isoformat(),
                                 "label_available_at": row["label_available_at"].isoformat()}
                                for row in result["predictions"]]}

    report = {"target_series_id": target, "generated_at": now.isoformat(), "store": str(get_settings().lancedb_path),
              "label": "100 * (level[Q+1] / level[Q] - 1), Q = origin quarter; release-dated latest-vintage levels",
              "revision_note": "Values are latest vintage dated by first release; PP300 QoQ revisions first->latest: mean 0.06 pp, max 0.35 pp.",
              "predictors": {n: {k: v for k, v in p.items() if k != "levels"} for n, p in predictors.items()},
              "without_predictors": section(baseline, "mean_4q"),
              "with_predictors": section(with_predictors, "ar4_seasonal_vat_direct")}
    output = Path(args.output) if args.output else Path(get_settings().data_dir) / "reports" / f"backtest-{now:%Y%m%d}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **{
        key: {"models": report[key]["models"], "intervals": report[key]["intervals"],
              "dm": {n: {"p": v["p_value"], "stat": v["statistic"]} for n, v in report[key][dm].items()}}
        for key, dm in (("without_predictors", "diebold_mariano_vs_mean_4q"),
                        ("with_predictors", "diebold_mariano_vs_ar4_seasonal_vat_direct"))}}, indent=2))


if __name__ == "__main__":
    main()
