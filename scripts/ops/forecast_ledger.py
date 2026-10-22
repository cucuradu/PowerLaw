"""Append-only record of every published forecast, scored once the outcome is released (prospective evaluation).

record  appends each forecast in reports/forecast-latest.json, projections-latest.json, horizon-latest.json and
        cost-scenarios-latest.json that
        is not yet in the ledger (one entry per series, model, base quarter and target quarter: the first forecast
        made after each release). Every line carries the SHA-256 of the line before it, so edits break the chain.
score   checks the chain and scores every entry whose target quarter was released after the forecast was made:
        actual growth from the base quarter's level to the target quarter's level, both as public now (latest
        vintage), and whether the interval held. Forecasts of quarters already public when made are not scored. Writes reports/forecast-ledger-scores.json; the ledger itself is never rewritten.

Usage: python -m scripts.ops.forecast_ledger record|score
"""
import argparse
import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.config import REPO_ROOT, get_settings

GENESIS = "0" * 64


def reports() -> Path:
    return Path(get_settings().data_dir) / "reports"


def read_ledger(path: Path) -> list[dict]:
    """Entries in order; raises if any line does not chain to the one before it."""
    entries, previous = [], GENESIS
    if not path.exists():
        return entries
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        entry = json.loads(line)
        if entry["previous_sha256"] != previous:
            raise SystemExit(f"Ledger chain broken at line {number}: {path}")
        previous = hashlib.sha256(line.encode("utf-8")).hexdigest()
        entries.append(entry)
    return entries


def key(entry: dict) -> tuple:
    return entry["series_id"], entry["model"], entry["base_period_start"], entry["target_period_start"]


def append(path: Path, new: list[dict]) -> int:
    entries = read_ledger(path)
    seen = {key(e) for e in entries}
    previous = GENESIS
    if path.exists() and path.stat().st_size:
        previous = hashlib.sha256(path.read_text(encoding="utf-8").splitlines()[-1].encode("utf-8")).hexdigest()
    added = 0
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for entry in new:
            if key(entry) in seen:
                continue
            seen.add(key(entry))
            line = json.dumps({**entry, "previous_sha256": previous}, sort_keys=True)
            handle.write(line + "\n")
            previous = hashlib.sha256(line.encode("utf-8")).hexdigest()
            added += 1
    return added


def quarters_between(base: str, target: str) -> int:
    q = lambda s: int(s[:4]) * 4 + (int(s[5:7]) - 1) // 3
    return q(target) - q(base)


def collect(now: datetime) -> list[dict]:
    """Ledger entries from the current report files (missing files are skipped)."""
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    load = lambda name: json.loads((reports() / name).read_text(encoding="utf-8")) if (reports() / name).exists() else None
    base = {"recorded_at": now.isoformat(), "code_commit": commit}
    out = []
    target = load("forecast-latest.json")
    if target:
        out.append({**base, "kind": "target", "series_id": target["target_series_id"], "model": target["model"],
                    "made_at": target["generated_at"], "base_period_start": target["last_released_period_start"],
                    "base_level": target["last_released_level"], "target_period_start": target["target_period_start"],
                    "growth_pct": target["forecast_growth_pct"], "interval_growth_pct": target["interval_growth_pct"]})
    projections = load("projections-latest.json")
    for p in (projections or {}).get("projections", []):
        if p["growth_pct"] is not None:
            out.append({**base, "kind": "projection", "series_id": p["series_id"], "model": p["model"],
                        "made_at": projections["generated_at"], "base_period_start": p["last_period_start"],
                        "base_level": p["last_level"], "target_period_start": p["target_period_start"],
                        "growth_pct": p["growth_pct"], "interval_growth_pct": p["interval_growth_pct"]})
    for p in [*(load("horizon-latest.json") or {}).get("forecasts", []), *(load("cost-scenarios-latest.json") or {}).get("forecasts", [])]:
        out.append({**base, "kind": "horizon", "series_id": p["series_id"], "model": p["model"],
                    "made_at": now.isoformat(), "base_period_start": p["base_period_start"],
                    "base_level": p["base_level"], "target_period_start": p["target_period_start"],
                    "growth_pct": p["growth_pct"], "interval_growth_pct": p["interval_growth_pct"]})
    for entry in out:
        entry["horizon_quarters"] = quarters_between(entry["base_period_start"], entry["target_period_start"])
    return out


def quarterly_levels(rows: list[dict]) -> dict:
    """{quarter start: (level, first public)} from revisions {period_start, value, available_at}: latest value per
    period, quarterly as is, monthly as the mean of a complete quarter (public once its last month first was)."""
    latest, first = {}, {}
    for r in rows:
        if r["value"] is None:
            continue
        p = r["period_start"]
        if p not in latest or r["available_at"] >= latest[p][1]:
            latest[p] = (r["value"], r["available_at"])
        first[p] = min(first.get(p, r["available_at"]), r["available_at"])
    parts = defaultdict(list)
    for p, (value, _) in latest.items():
        parts[f"{p[:4]}-{(int(p[5:7]) - 1) // 3 * 3 + 1:02d}-01"].append((value, first[p]))
    monthly = sum(map(len, parts.values())) > 1.5 * len(parts)
    return {q: (sum(v for v, _ in vals) / len(vals), max(t for _, t in vals))
            for q, vals in parts.items() if not monthly or len(vals) == 3}


def score(entries: list[dict], levels: dict) -> list[dict]:
    """Scored entries whose base and target quarters both have a level now. `levels`: {series_id: {quarter: level}}."""
    scored = []
    for e in entries:
        lv = levels.get(e["series_id"], {})
        target, base = lv.get(e["target_period_start"]), lv.get(e["base_period_start"])
        if target and base and target[1] > datetime.fromisoformat(e["made_at"]):
            actual = 100 * (target[0] / base[0] - 1)
            interval = e.get("interval_growth_pct")
            scored.append({**{k: e[k] for k in ("kind", "series_id", "model", "made_at", "base_period_start",
                                                  "target_period_start", "horizon_quarters", "growth_pct")},
                           "actual_growth_pct": actual, "error": actual - e["growth_pct"],
                           "in_interval": None if not interval else interval[0] <= actual <= interval[1]})
    return scored


def summary(scored: list[dict]) -> dict:
    groups = defaultdict(list)
    for s in scored:
        groups[f"{s['kind']}|h{s['horizon_quarters']}"].append(s)
    out = {}
    for name, rows in sorted(groups.items()):
        held = [s["in_interval"] for s in rows if s["in_interval"] is not None]
        out[name] = {"n": len(rows), "mae": sum(abs(s["error"]) for s in rows) / len(rows),
                     "mae_no_change": sum(abs(s["actual_growth_pct"]) for s in rows) / len(rows),
                     "coverage": sum(held) / len(held) if held else None}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("record", "score"))
    args = parser.parse_args()
    path = reports() / "forecast-ledger.jsonl"
    now = datetime.now(timezone.utc)
    if args.command == "record":
        print(json.dumps({"ledger": str(path), "added": append(path, collect(now))}))
        return
    from src.storage.dual_layer_store import DualLayerStore
    entries = read_ledger(path)
    ids = sorted({e["series_id"] for e in entries})
    rows = defaultdict(list)
    store = DualLayerStore()
    for o in store.query_observation_revisions(now, "public_as_of", series_ids=ids):
        if not o.is_retracted:
            rows[o.series_id].append({"period_start": o.period_start, "value": o.value, "available_at": store._available(o)})
    scored = score(entries, {sid: quarterly_levels(r) for sid, r in rows.items()})
    out = reports() / "forecast-ledger-scores.json"
    out.write_text(json.dumps({"scored_at": now.isoformat(), "entries": len(entries), "scored": len(scored),
                               "summary": summary(scored), "rows": scored}, indent=1), encoding="utf-8")
    print(json.dumps({"output": str(out), "entries": len(entries), "scored": len(scored)}))


if __name__ == "__main__":
    main()
