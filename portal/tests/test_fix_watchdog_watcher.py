"""Watchdog fix: the Watcher must not cry "should have gone to sleep" while the watchdog is still counting down.

`idle_watch.warnings()` warned as soon as the VM was up more than 2 h and the report said nobody was playing, ignoring
the `idle_min` and `limit` printed in the same WATCHDOG line. After every long session (friends leave, the idle clock
runs 0 -> 30 min) the dashboard showed "auto-sleep is set to 30 min, so it should have gone to sleep", which is false
until the clock has reached the limit.

The fix must not blind the Watcher to the failure it exists for, though. `idle_min` is the watchdog's own clock: it only
moves when the timer's check runs. A watchdog that never runs (the first version's `iptables -I -t` bug) leaves it at 0
for ever, so the report now also says how long ago the last check was (`last_check_s`, or `never`), and the Watcher
still warns when the checks have stopped.
"""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import idle_watch
from gcp import VmStatus

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra" / "gcp-games"
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)

spec = importlib.util.spec_from_file_location("idle_watchdog_fix_watcher", INFRA / "idle_watchdog.py")
iw = importlib.util.module_from_spec(spec)
sys.modules["idle_watchdog_fix_watcher"] = iw          # dataclasses need the module registered
spec.loader.exec_module(iw)


class Vm:
    def __init__(self, up_hours, idle="30", max_hours="8", games=("valheim",)):
        started = NOW - timedelta(hours=up_hours)
        self.state = VmStatus(status="RUNNING", machine_type="e2-standard-2", external_ip="35.1.2.3", games=list(games),
                              idle_minutes=idle, started_at=started, max_hours=max_hours)

    def status(self):
        return self.state


def snap(verdict="idle", idle_min=5, limit=30, last_check=None, players=None, up=200):
    """A snapshot with the WATCHDOG line as idle_watchdog.py prints it. last_check: None = the line has no such key (a
    report from before it existed), "never", or seconds."""
    lines = ["=== skippy-diag t ==="]
    lines += [f"PLAYERS {g}={n}" for g, n in (players or {}).items()]
    key = "" if last_check is None else f" last_check_s={last_check}"
    lines.append(f'WATCHDOG verdict={verdict} idle_min={idle_min} limit={limit} up_min={up} max_hours=8 unknown_min=0'
                 f'{key} reason="no player evidence"')
    return "\n".join(lines + ["=== end ==="]) + "\n"


def warn(vm, snapshot):
    return idle_watch.warnings(vm, snapshot, NOW)


# ── the false alarm ──────────────────────────────────────────────────────────

def test_the_grace_period_after_a_long_session_is_not_a_warning():
    # Friends played 3 h and left 5 minutes ago: the watchdog is doing exactly what it should.
    assert warn(Vm(up_hours=3.3), snap(idle_min=5, last_check=120)) == []
    assert warn(Vm(up_hours=3.3), snap(idle_min=25, last_check=200)) == []


def test_the_clock_at_the_limit_is_a_sleep_in_progress_not_a_failure():
    # The check that reaches the limit saves the worlds (up to a few minutes) and only then powers off.
    assert warn(Vm(up_hours=3.3), snap(idle_min=30, last_check=90)) == []
    assert warn(Vm(up_hours=3.3), snap(idle_min=34, last_check=90)) == []


def test_a_clock_one_check_past_the_limit_means_the_sleep_did_not_happen():
    lines = warn(Vm(up_hours=3.3), snap(idle_min=35, last_check=90))
    assert lines == ["On for 3.3 h with nobody playing: auto-sleep is set to 30 min, so it should have gone to sleep."]


def test_the_limit_is_the_watchdogs_own_when_the_line_has_one():
    # An admin's session set to sleep after 2 h: 40 idle minutes is nothing, whatever the portal's cached value says.
    assert warn(Vm(up_hours=4, idle="30"), snap(idle_min=40, limit=120, last_check=90)) == []
    assert len(warn(Vm(up_hours=4, idle="120"), snap(idle_min=125, limit=120, last_check=90))) == 1


def test_players_or_an_active_verdict_still_silence_it():
    assert warn(Vm(up_hours=3), snap(verdict="active", idle_min=90, last_check=90)) == []
    assert warn(Vm(up_hours=3), snap(idle_min=90, last_check=90, players={"valheim": 2})) == []


# ── the failure the Watcher exists for is still caught ───────────────────────

def test_a_watchdog_that_has_never_checked_is_still_reported_after_two_hours():
    lines = warn(Vm(up_hours=3), snap(idle_min=0, last_check="never"))
    assert len(lines) == 1
    assert "3.0 h with nobody playing" in lines[0] and "should have gone to sleep" in lines[0]
    assert "has not run a check since boot" in lines[0]


def test_a_watchdog_whose_checks_stopped_is_reported():
    lines = warn(Vm(up_hours=3), snap(idle_min=10, last_check=45 * 60))
    assert len(lines) == 1 and "45 min ago" in lines[0] and "should have gone to sleep" in lines[0]
    assert warn(Vm(up_hours=3), snap(idle_min=10, last_check=6 * 60)) == []                 # a slow check is not a stopped one


def test_a_report_without_the_new_key_falls_back_to_the_clock():
    assert warn(Vm(up_hours=3), snap(idle_min=10, last_check=None)) == []
    assert len(warn(Vm(up_hours=3), snap(idle_min=40, last_check=None))) == 1


def test_never_sleep_has_no_grace_period():
    line = warn(Vm(up_hours=5, idle="0"), snap(idle_min=0, limit=0, last_check=60))[0]
    assert "5.0 h with nobody playing" in line and "auto-sleep is off" in line


def test_without_a_watchdog_line_the_old_rule_holds():
    plain = "=== skippy-diag t ===\nPLAYERS valheim=0\n=== end ===\n"
    assert len(warn(Vm(up_hours=3), plain)) == 1


# ── the report says how long ago the last check was ──────────────────────────

def counters_listing():
    rows = ["Chain PREROUTING (policy ACCEPT 0 packets, 0 bytes)",
            "    pkts      bytes target     prot opt in     out     source               destination"]
    for comment in (iw.RULE_ASSURED, iw.RULE_RAW):
        rows.append(f"{0:>8} {0:>8}            17   --  *      *       0.0.0.0/0            0.0.0.0/0"
                    f"            multiport dports {iw.port_spec()} /* {comment} */")
    return "\n".join(rows) + "\n"


def quiet_vm(argv, timeout=60, merge=False):
    if argv[0] == "iptables":
        return (0, counters_listing()) if "-L" in argv else (0, "")
    if argv[:2] == ["docker", "ps"]:
        return 0, "valheim\n"
    if argv[:2] == ["docker", "logs"]:
        return 0, "Valheim version: l-1.0.16\n"
    return 0, ""


@pytest.fixture
def vm_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(iw, "STATE", tmp_path / "state")
    monkeypatch.setattr(iw, "metadata", lambda key, default="": {"idle-minutes": "30", "max-hours": "8"}.get(key, default))
    monkeypatch.setattr(iw, "read_flows", lambda path="/proc/net/nf_conntrack": iw.Flows())
    t = {"up": 3 * 3600.0, "now": 9_000_000.0}
    monkeypatch.setattr(iw, "uptime_s", lambda: t["up"])
    return t


def snapshot_of_report(now):
    lines = []
    iw.report(quiet_vm, out=lines.append, now=now)
    return "=== skippy-diag t ===\n" + "\n".join(lines) + "\n=== end ===\n"


def test_the_report_says_it_has_never_checked_and_the_watcher_reads_it(vm_clock):
    text = snapshot_of_report(vm_clock["now"])
    assert idle_watch.parse(text)["last_check_s"] == "never"
    assert len(warn(Vm(up_hours=3), text)) == 1                     # 3 h up, nobody there, no check ever ran


def test_the_report_says_how_long_ago_the_last_check_was_and_a_healthy_countdown_is_quiet(vm_clock):
    for _ in range(2):                                              # two 5-minute checks with nobody there
        vm_clock["up"] += 300
        vm_clock["now"] += 300
        iw.tick(quiet_vm, now=vm_clock["now"])
    text = snapshot_of_report(vm_clock["now"] + 30)
    parsed = idle_watch.parse(text)
    clock = iw.load_state().idle_min
    assert 0 < clock < 30
    assert parsed["last_check_s"] == "30" and parsed["idle_min"] == str(clock) and parsed["verdict"] == "idle"
    assert warn(Vm(up_hours=3), text) == []                         # on its way to 30, not overdue
    late = snapshot_of_report(vm_clock["now"] + 40 * 60)
    assert idle_watch.parse(late)["last_check_s"] == str(40 * 60)
    assert len(warn(Vm(up_hours=3), late)) == 1                     # 40 minutes with no check: the timer is gone
