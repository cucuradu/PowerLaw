"""Rolling-origin backtest of next-quarter growth for the primary target on release-dated committed data.

Usage: python -m scripts.ops.backtest_target [--first-origin 2006-03-31] [--fit-start 1991-01-01] [--output PATH]
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import get_settings, primary_target_series_id
from src.quant.forecasting import rolling_origin_backtest
from src.storage.dual_layer_store import DualLayerStore


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--first-origin", default="2006-03-31")
    parser.add_argument("--fit-start", default="1991-01-01", help="first quarter used to fit AR models (unified Germany)")
    parser.add_argument("--output")
    args = parser.parse_args()

    store = DualLayerStore()
    target = primary_target_series_id()
    now = datetime.now(timezone.utc)
    levels = [{"period_start": o.period_start, "value": o.value, "available_at": store._available(o)}
              for o in store.query_observation_revisions(now, "system_as_of")
              if o.series_id == target and o.value is not None and not o.is_retracted]
    result = rolling_origin_backtest(levels, quarter_end_origins(args.first_origin, now), fit_start=args.fit_start)
    report = {"target_series_id": target, "generated_at": now.isoformat(), "store": str(get_settings().lancedb_path),
              "label": "100 * (level[Q+1] / level[Q] - 1), Q = origin quarter; release-dated latest-vintage levels",
              "revision_note": "Values are latest vintage dated by first release; PP300 QoQ revisions first->latest: mean 0.06 pp, max 0.35 pp.",
              **{k: result[k] for k in ("settings", "models", "intervals")},
              "predictions": [{**row, "origin": row["origin"].isoformat(),
                               "label_available_at": row["label_available_at"].isoformat()} for row in result["predictions"]]}
    output = Path(args.output) if args.output else Path(get_settings().data_dir) / "reports" / f"backtest-{now:%Y%m%d}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "origins": len(result["predictions"]),
                      "models": result["models"], "intervals": result["intervals"]}, indent=2))


if __name__ == "__main__":
    main()
