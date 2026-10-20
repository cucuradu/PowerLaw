"""Turn the weekly automatic update (Windows Task Scheduler) on or off. Off unless you turn it on.

Usage: python -m scripts.ops.schedule_updates on [--day MON --time 07:00]
       python -m scripts.ops.schedule_updates off
       python -m scripts.ops.schedule_updates status
"""
import argparse
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TASK = "PowerLaw forecast update"


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def is_enabled() -> bool:
    try:
        return _schtasks("/Query", "/TN", TASK).returncode == 0
    except FileNotFoundError:  # not Windows
        return False


def enable(day: str = "MON", time: str = "07:00") -> str:
    command = f'"{REPO / "scripts" / "ops" / "update_forecast.cmd"}"'
    done = _schtasks("/Create", "/F", "/TN", TASK, "/SC", "WEEKLY", "/D", day, "/ST", time, "/TR", command)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or done.stdout.strip())
    return f"On: every {day} at {time} (task '{TASK}')."


def disable() -> str:
    if not is_enabled():
        return "Already off."
    done = _schtasks("/Delete", "/F", "/TN", TASK)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or done.stdout.strip())
    return "Off."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["on", "off", "status"])
    parser.add_argument("--day", default="MON", choices=["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"])
    parser.add_argument("--time", default="07:00", help="HH:MM, local time")
    args = parser.parse_args()
    if args.action == "on":
        print(enable(args.day, args.time))
    elif args.action == "off":
        print(disable())
    else:
        print(_schtasks("/Query", "/TN", TASK, "/FO", "LIST").stdout.strip() if is_enabled() else "Off.")


if __name__ == "__main__":
    main()
