"""The automatic update is a weekly scheduled task that runs the wrapper script, and only when turned on."""
from types import SimpleNamespace

from scripts.ops import schedule_updates


def test_on_registers_the_weekly_wrapper_and_off_removes_it(monkeypatch):
    calls, registered = [], {"on": False}

    def fake(*args):
        calls.append(args)
        if args[0] == "/Create":
            registered["on"] = True
        if args[0] == "/Delete":
            registered["on"] = False
        return SimpleNamespace(returncode=0 if args[0] != "/Query" or registered["on"] else 1, stdout="", stderr="")

    monkeypatch.setattr(schedule_updates, "_schtasks", fake)
    assert not schedule_updates.is_enabled()
    schedule_updates.enable("THU", "06:30")
    create = next(c for c in calls if c[0] == "/Create")
    assert create[create.index("/SC") + 1] == "WEEKLY" and create[create.index("/D") + 1] == "THU"
    assert create[create.index("/ST") + 1] == "06:30"
    assert create[create.index("/TR") + 1].endswith('update_forecast.cmd"')
    assert schedule_updates.is_enabled()
    assert schedule_updates.disable() == "Off." and not schedule_updates.is_enabled()
