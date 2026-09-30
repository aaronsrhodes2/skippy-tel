"""portal/idle_watch.py: the Watcher's warnings about a games VM that has been on longer than its watchdog allows."""
from datetime import datetime, timedelta, timezone

import pytest

import idle_watch
import watch
from gcp import VmStatus

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
WATCHDOG = 'WATCHDOG verdict={verdict} idle_min={idle} limit=30 up_min={up} max_hours=8 unknown_min={unknown} reason="{reason}"'


class Vm:
    """Just what the Watcher touches: status() and get_metadata()."""

    def __init__(self, up_hours, idle="30", max_hours="8", games=("valheim",), status="RUNNING", now=NOW):
        started = now - timedelta(hours=up_hours)
        self.state = VmStatus(status=status, machine_type="e2-standard-2", external_ip="35.1.2.3", games=list(games),
                              idle_minutes=idle, started_at=started, max_hours=max_hours)

    def status(self):
        return self.state

    def get_metadata(self, key):
        return ""


def snap(verdict="idle", up=180, unknown=0, reason="no player evidence", players=None, idle_min=10):
    lines = ["=== skippy-diag t ==="]
    lines += [f"PLAYERS {g}={n}" for g, n in (players or {}).items()]
    if verdict:
        lines.append(WATCHDOG.format(verdict=verdict, idle=idle_min, up=up, unknown=unknown, reason=reason))
    return "\n".join(lines + ["=== end ==="]) + "\n"


def warn(vm, snapshot):
    return idle_watch.warnings(vm, snapshot, NOW)


def test_the_watchdog_line_is_parsed_quoted_reason_and_all():
    line = snap(verdict="unknown", unknown=75, reason="cannot see valheim players: crossplay traffic")
    parsed = idle_watch.parse(line)
    assert parsed["verdict"] == "unknown" and parsed["unknown_min"] == "75"
    assert parsed["reason"] == "cannot see valheim players: crossplay traffic"
    assert idle_watch.parse("=== skippy-diag t ===\nno watchdog here\n") is None
    assert idle_watch.parse("") is None and idle_watch.parse(None) is None


def test_past_the_backstop_by_more_than_an_hour_means_the_backstop_failed():
    lines = warn(Vm(up_hours=9.5), snap(verdict="active"))
    assert len(lines) == 1
    assert "9.5 h" in lines[0] and "over its 8-hour limit by 1.5 h" in lines[0] and "should have stopped it" in lines[0]
    assert warn(Vm(up_hours=8.9), snap(verdict="active")) == []                    # within the hour of grace


def test_a_session_that_never_sleeps_has_no_limit_to_overrun():
    assert warn(Vm(up_hours=30, idle="0"), snap(verdict="active")) == []
    assert warn(Vm(up_hours=30, max_hours="0"), snap(verdict="active")) == []      # max-hours 0: the backstop is off
    assert "over its 12.5-hour limit" in warn(Vm(up_hours=14, max_hours="12.5"), snap(verdict="active"))[0]
    assert "over its 8-hour limit" in warn(Vm(up_hours=10, max_hours="junk"), snap(verdict="active"))[0]


def test_two_hours_with_nobody_playing_is_worth_a_line():
    # Only once the watchdog's own idle clock has run past its 30-minute limit by a check (35): before that it is
    # still counting down, which is normal for up to 30 minutes after every long session (the old fixture's clock
    # said 10 and still expected the line: the false alarm itself; see test_fix_watchdog_watcher.py).
    lines = warn(Vm(up_hours=3), snap(verdict="idle", idle_min=35))
    assert lines == ["On for 3.0 h with nobody playing: auto-sleep is set to 30 min, so it should have gone to sleep."]
    assert warn(Vm(up_hours=1.9), snap(verdict="idle", idle_min=35)) == []          # not yet
    assert warn(Vm(up_hours=3), snap(verdict="idle", idle_min=10)) == []            # still counting down


def test_never_sleep_and_nobody_playing_says_so():
    line = warn(Vm(up_hours=5, idle="0"), snap(verdict="idle"))[0]
    assert "5.0 h with nobody playing" in line and "auto-sleep is off" in line


def test_players_or_an_active_verdict_silence_it():
    assert warn(Vm(up_hours=3), snap(verdict="active")) == []
    assert warn(Vm(up_hours=3), snap(verdict="idle", players={"valheim": 2})) == []


def test_without_a_watchdog_line_only_a_full_set_of_zero_counts_says_nobody():
    assert warn(Vm(up_hours=3), snap(verdict=None)) == []                               # nothing to go on: no guess
    assert len(warn(Vm(up_hours=3), snap(verdict=None, players={"valheim": 0}))) == 1   # every running game counted 0
    assert warn(Vm(up_hours=3, games=("valheim", "dragonwilds")),
                snap(verdict=None, players={"valheim": 0})) == []                       # dragonwilds has no count


def test_a_watchdog_that_cannot_tell_for_an_hour_is_reported():
    lines = warn(Vm(up_hours=3), snap(verdict="unknown", unknown=90, reason="no evidence could be read"))
    assert len(lines) == 1
    assert "90 min" in lines[0] and "no evidence could be read" in lines[0] and "8-hour limit will stop" in lines[0]
    assert warn(Vm(up_hours=3), snap(verdict="unknown", unknown=30)) == []
    assert warn(Vm(up_hours=3, idle="0"), snap(verdict="unknown", unknown=90)) == []      # never sleep: nothing to be unsure of


def test_nothing_is_said_about_a_vm_that_is_off_or_just_started():
    assert warn(Vm(up_hours=10, status="TERMINATED"), snap()) == []
    fresh = Vm(up_hours=0.5)
    fresh.state.started_at = None                                                          # no start time known
    assert warn(fresh, snap()) == []


def test_a_failing_status_call_never_breaks_the_page():
    class Broken:
        def status(self):
            raise RuntimeError("compute API down")
    assert idle_watch.warnings(Broken(), snap(), NOW) == []


def test_the_watcher_includes_these_lines_beside_its_own():
    vm = Vm(up_hours=9.5, now=datetime.now(timezone.utc))          # watch.warnings uses the real clock
    out = watch.warnings(vm, snap(verdict="active"), {"memory_pct": 10, "disk_pct": 10})
    assert len(out) == 1 and "over its 8-hour limit" in out[0]
    crash = "dragonwilds  Restarting (1) 5 seconds ago\n" + snap(verdict="active")
    both = watch.warnings(vm, crash, {})
    assert any("dragonwilds is restarting" in line for line in both) and any("8-hour limit" in line for line in both)


def test_the_dashboard_shows_it():
    pytest.importorskip("flask")
    pytest.importorskip("authlib")
    import app as portal
    from test_app import ADMIN, SETTINGS, FakeSkippy, FakeVm, login       # the same fakes the dashboard tests use

    vm = FakeVm()
    vm.state.status, vm.state.games = "RUNNING", ["valheim"]
    vm.state.started_at = datetime.now(timezone.utc) - timedelta(hours=9.5)
    vm.serial[2] = snap(verdict="active")
    client = portal.create_app(dict(SETTINGS), vm=vm, skippy=FakeSkippy()).test_client()
    login(client, ADMIN)
    page = client.get("/me").data.decode()
    assert "over its 8-hour limit" in page and "should have stopped it" in page
