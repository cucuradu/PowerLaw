"""Pull recent releases and rebuild the forecast record (what the scheduled task runs).

Usage: python -m scripts.ops.update_forecast
Order matters: Destatis values must be committed before the Bundesbank vintages that date them.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# (source, days back): 400 days covers a revised year; 45 days re-reads vintages that came out before Destatis did.
SOURCES = [("DESTATIS_GENESIS", 400), ("BUNDESBANK_RTD", 45), ("ECB_SDMX", 400), ("BUNDESBANK_SDMX", 400),
           ("EUROSTAT_STS", 400)]


def run(*args: str) -> dict:
    started = datetime.now(timezone.utc)
    done = subprocess.run([sys.executable, "-m", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    return {"step": " ".join(args), "started": started.isoformat(), "exit_code": done.returncode,
            "tail": (done.stdout + done.stderr)[-1500:]}


def main() -> None:
    sys.path.insert(0, str(REPO))
    from src.config import get_settings
    today = datetime.now(timezone.utc).date()
    steps = [run("scripts.ops.ingest", "--source", source, "--since", (today - timedelta(days=days)).isoformat(),
                 "--until", today.isoformat(), "--limit", "500") for source, days in SOURCES]
    target_ok = all(s["exit_code"] == 0 for s in steps[:2])
    steps.append(run("scripts.ops.backtest_target") if target_ok else
                 {"step": "scripts.ops.backtest_target", "skipped": "Destatis or Bundesbank real-time update failed"})
    log = Path(get_settings().data_dir) / "reports" / "update-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"run_at": datetime.now(timezone.utc).isoformat(), "steps": steps}) + "\n")
    failed = [s["step"] for s in steps if s.get("exit_code", 1) != 0]
    print(json.dumps({"log": str(log), "failed": failed}))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
