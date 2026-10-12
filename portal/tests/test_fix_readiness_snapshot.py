"""Live readings come from the newest COMPLETE snapshot, never from the one still being written.

diag.sh streams its snapshot to the serial port as it runs, so for a few seconds of every 2-minute cycle the newest
snapshot has a header and no JOIN_CODE / PLAYERS / READY lines yet. `latest_snapshot()` returns that one on purpose (the
logs page wants the freshest text); reading readiness from it made the game's state flap (READY gone -> the boot timer
decides -> back again), and game.html reloads the page whenever `starting` differs from what it rendered.

Invite codes and player counts are also this boot's or nothing: a snapshot from before the VM's last start is the
previous session's (Dragonwilds' code is new every boot).
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("flask")
pytest.importorskip("authlib")

import app as portal  # noqa: E402
import diagnostics  # noqa: E402
from test_fix_readiness_admin_card import inline_script, needs_node, run_script  # noqa: E402
from test_readiness_page import ADMIN, SETTINGS, FakeSkippy, FakeVm, boot, login  # noqa: E402

T0 = datetime(2026, 9, 30, 6, 0, 0, tzinfo=timezone.utc)      # the VM's last start, in the unit tests


def snap(taken: datetime, *lines: str, complete: bool = True) -> str:
    """One skippy-diag snapshot as diag.sh streams it: header, `docker ps`, a log tail, the readiness section (`lines`),
    the tail; `=== end ===` only when it has finished. With no `lines` and complete=False it is the moment right after the header."""
    text = f"=== skippy-diag {taken.isoformat(timespec='seconds')} ===\nNAMES\tSTATUS\tPORTS\nvalheim\tUp 2 minutes\t\n"
    if lines or complete:
        text += "--- valheim: last 40 log lines\nnoise\n" + "".join(f"{ln}\n" for ln in lines) + "--- memory\nMem: 100 50\n"
    return text + ("=== end ===\n" if complete else "")


class Serial:
    def __init__(self, port2: str):
        self.port2 = port2

    def serial_output(self, port):
        assert port == 2
        return self.port2


READINGS = ["JOIN_CODE valheim=106969", "PLAYERS valheim=2", "READY valheim=1"]


# ── unit: which snapshot the readings come from ───────────────────────────────

def test_the_readings_come_from_the_complete_snapshot_when_a_newer_one_is_still_being_written():
    done = snap(T0 + timedelta(minutes=1), *READINGS)
    writing = snap(T0 + timedelta(minutes=3), complete=False)                # header + ps, nothing after
    for port2 in (done + writing, (done + writing).replace("\n", "\r\n")):   # the serial port ends lines with CRLF
        codes, players, ready = diagnostics.live_all(Serial(port2), T0)[:3]
        assert (codes, players, ready) == ({"valheim": "106969"}, {"valheim": 2}, {"valheim": True})
        assert diagnostics.current_ready(Serial(port2), T0) == {"valheim": True}
        assert diagnostics.current_join_codes(Serial(port2)) == {"valheim": "106969"}


def test_a_snapshot_partway_through_is_still_skipped_when_it_has_only_some_of_its_lines():
    done = snap(T0 + timedelta(minutes=1), "READY valheim=0")
    half = snap(T0 + timedelta(minutes=3), "READY valheim=1", complete=False)   # cut off after the readiness section
    assert diagnostics.live_all(Serial(done + half), T0)[2] == {"valheim": False}
    assert diagnostics.live_all(Serial(half), T0)[2] == {"valheim": True}       # ...but it is all there is: use it


def test_the_one_being_written_is_used_when_it_is_the_only_snapshot_of_this_boot():
    old_session = snap(T0 - timedelta(hours=2), *READINGS)                       # the serial port kept the last session's output
    writing = snap(T0 + timedelta(minutes=1), "READY valheim=0", "PLAYERS valheim=1", complete=False)
    codes, players, ready = diagnostics.live_all(Serial(old_session + writing), T0)[:3]
    assert (codes, players, ready) == ({}, {"valheim": 1}, {"valheim": False})   # nothing of yesterday's: no code, this boot's numbers
    assert diagnostics.live_all(Serial(writing), T0)[2] == {"valheim": False}


def test_the_previous_sessions_codes_and_players_are_no_answer_just_like_its_ready_lines():
    old = snap(T0 - timedelta(hours=2), *READINGS)
    assert diagnostics.live_all(Serial(old), T0)[:3] == ({}, {}, {})
    assert diagnostics.current_join_codes(Serial(old), T0) == {}
    assert diagnostics.live_all(Serial(old), T0 - timedelta(hours=3))[:3] == ({"valheim": "106969"}, {"valheim": 2}, {"valheim": True})
    # the boot time is unknown: nothing to compare against, the newest complete snapshot is what there is
    assert diagnostics.live_all(Serial(old), None)[:3] == ({"valheim": "106969"}, {"valheim": 2}, {"valheim": True})
    assert diagnostics.current_join_codes(Serial(old)) == {"valheim": "106969"}
    # a header that can't be read can't be proven fresh
    unreadable = old.replace(old.splitlines()[0], "=== skippy-diag garbage ===")
    assert diagnostics.live_all(Serial(unreadable), T0)[:3] == ({}, {}, {})


def test_nothing_of_the_previous_session_survives_next_to_a_fresh_snapshot_either():
    old = snap(T0 - timedelta(hours=2), "JOIN_CODE dragonwilds=OLD-CODE", "PLAYERS dragonwilds=9", "READY dragonwilds=1")
    new = snap(T0 + timedelta(minutes=1), "READY dragonwilds=0")
    codes, players, ready = diagnostics.live_all(Serial(old + new), T0)[:3]
    assert (codes, players, ready) == ({}, {}, {"dragonwilds": False})


def test_live_and_the_serial_port_failing_still_never_raise():
    class Broken:
        def serial_output(self, port):
            raise RuntimeError("serial port unavailable")

    assert diagnostics.live_all(Broken(), T0)[:3] == ({}, {}, {})
    assert diagnostics.current_join_codes(Broken()) == {}


# ── the page: no flapping, no reload ──────────────────────────────────────────

@pytest.fixture
def env():
    vm = FakeVm()
    app = portal.create_app(dict(SETTINGS), vm=vm, skippy=FakeSkippy())
    return app.test_client(), vm, app


def live(client):
    resp = client.get("/games/valheim/live")
    assert resp.status_code == 200
    return resp.get_json()


# (minutes since boot, READY line, what the log says) - fast boot: ready a minute in while the timer says 5;
# slow boot: still loading 12 minutes in while the timer says online.
BOOTS = [(4, "READY valheim=1", False, True), (12, "READY valheim=0", True, False)]


@pytest.mark.parametrize("minutes,line,starting,ready", BOOTS)
def test_the_live_poll_does_not_flap_while_the_next_snapshot_is_being_written(env, minutes, line, starting, ready):
    client, vm, _ = env
    login(client, ADMIN)
    boot(vm, "valheim", minutes)
    up = vm.state.started_at
    done = snap(up + timedelta(minutes=1), "JOIN_CODE valheim=106969", "PLAYERS valheim=2", line)
    vm.serial[2] = done
    quiet = live(client)
    assert quiet["starting"] is starting and quiet["ready"] is ready and quiet["code"] == "106969" and quiet["players"] == 2
    # the next snapshot starts: its header, its `docker ps`, and nothing else yet
    vm.serial[2] = done + snap(up + timedelta(minutes=3), complete=False)
    assert live(client) == quiet, "a snapshot still being written must not change what the page is told"
    # ...and again once it has finished: the same, no matter which of the three moments a poll lands in
    vm.serial[2] = done + snap(up + timedelta(minutes=3), "JOIN_CODE valheim=106969", "PLAYERS valheim=3", line)
    assert live(client)["starting"] is starting and live(client)["players"] == 3


@needs_node
@pytest.mark.parametrize("minutes,line,starting,ready", BOOTS)
def test_game_html_does_not_reload_when_a_snapshot_is_only_partly_written(env, minutes, line, starting, ready):
    """The real page, the real poll script, the real /live answers: a reload here is the flap the Captain would see."""
    client, vm, _ = env
    login(client, ADMIN)
    boot(vm, "valheim", minutes)
    up = vm.state.started_at
    done = snap(up + timedelta(minutes=1), line)
    vm.serial[2] = done
    page = client.get("/games/valheim").data.decode()
    assert f"starting: {'true' if starting else 'false'}}};" in page                    # what the page rendered with
    script = inline_script(page, "/games/valheim/live")
    answers = []
    for port2 in (done, done + snap(up + timedelta(minutes=3), complete=False),
                  done + snap(up + timedelta(minutes=3), line, complete=False), done):
        vm.serial[2] = port2
        answers.append({"status": 200, "headers": {"Content-Type": "application/json"}, "body": json.dumps(live(client))})
    ran = run_script(script, answers, {"join-code": None})
    assert [s["reloads"] for s in ran["out"]] == [0, 0, 0, 0], "an open game page must not reload on a partial snapshot"
    # the same script does reload when the state really changes (the check is not vacuous)
    flipped = [{"status": 200, "headers": {}, "body": json.dumps({**live(client), "starting": not starting})}]
    assert run_script(script, flipped, {"join-code": None})["out"][0]["reloads"] == 1


# ── the dashboard's read ──────────────────────────────────────────────────────

def test_the_dashboard_reads_codes_and_players_from_the_complete_snapshot_of_this_boot(env):
    client, vm, _ = env
    login(client, ADMIN)
    boot(vm, "dragonwilds", minutes_up=10)
    up = vm.state.started_at
    stale = snap(up - timedelta(hours=2), "JOIN_CODE dragonwilds=OLD-0000", "PLAYERS dragonwilds=7")
    done = snap(up + timedelta(minutes=5), "JOIN_CODE dragonwilds=NEW-1234", "PLAYERS dragonwilds=2")
    writing = snap(up + timedelta(minutes=7), complete=False)
    vm.serial[2] = stale
    page = client.get("/me").data.decode()
    assert "OLD-0000" not in page                                                         # the last session's code is nobody's
    vm.serial[2] = stale + done + writing
    page = client.get("/me").data.decode()
    assert "NEW-1234" in page and "2 playing" in page and "OLD-0000" not in page          # not blanked by the one being written
