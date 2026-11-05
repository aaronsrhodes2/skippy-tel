"""The starter's keeper for the direct front door's router forward (watch_edge): off unless SKIPPY_EDGE_UPNP=1 (environment or vault), looks after the
forward every five minutes on its own thread, says a line for a change and for a failure episode but never one per round, and can never raise into the
starter's loop. The router is the stand-in from tools/tests/fake_igd.py; no network is touched."""
import sys
import threading
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_igd  # noqa: E402
import skippy_starter as ss  # noqa: E402
import upnp_port  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def later(self, seconds):
        self.t += seconds


@pytest.fixture(autouse=True)
def _off_unless_a_test_says_otherwise(monkeypatch, tmp_path):
    monkeypatch.delenv("SKIPPY_EDGE_UPNP", raising=False)
    monkeypatch.delenv("SKIPPY_VAULT", raising=False)
    monkeypatch.setattr(ss, "VAULTS", (str(tmp_path / "no-such-vault.env"),))


def make(tmp_path, said, clock=None):
    clock = clock or Clock()
    s = ss.Starter(run=lambda *a, **k: (0, ""), http=lambda *a, **k: {}, spawn=lambda *a, **k: None, sleep=lambda s: None, clock=clock, token="t",
                   root=tmp_path, say=said.append, now=lambda: datetime(2026, 11, 5, 12, 0), state_file=tmp_path / "starter.json", gpu_read=lambda: None)
    s.edge_inline = True
    return s, clock


class FakeKeeper:
    def __init__(self, boom=None):
        self.rounds, self.boom = 0, boom

    def round(self):
        self.rounds += 1
        if self.boom:
            raise self.boom


# ── the switch ───────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, want", [("1", True), ("true", True), ("YES", True), ("on", True), (" 1 ", True), ("0", False), ("", False), ("false", False),
                                         ("off", False), ("2", False), ("no", False)])
def test_the_switch_reads_the_environment(value, want):
    assert ss.edge_upnp_wanted({"SKIPPY_EDGE_UPNP": value}, ()) is want


def test_the_switch_is_off_when_nothing_sets_it(tmp_path):
    assert ss.edge_upnp_wanted({}, (str(tmp_path / "missing.env"),)) is False


def test_the_switch_reads_the_vault_and_the_environment_wins(tmp_path):
    vault = tmp_path / ".env"
    vault.write_text("SKIPPY_HOME_TOKEN=abc\nSKIPPY_EDGE_UPNP=1\nOTHER=x\n", encoding="utf-8")
    assert ss.edge_upnp_wanted({}, (str(vault),)) is True
    assert ss.edge_upnp_wanted({"SKIPPY_EDGE_UPNP": "0"}, (str(vault),)) is False          # the environment says off: off
    vault.write_text('SKIPPY_EDGE_UPNP="true"\n', encoding="utf-8")
    assert ss.edge_upnp_wanted({}, (str(vault),)) is True                                    # quotes are fine
    vault.write_text("# SKIPPY_EDGE_UPNP=1\nNOT_SKIPPY_EDGE_UPNP=1\nSKIPPY_EDGE_UPNP_OTHER=1\n", encoding="utf-8")
    assert ss.edge_upnp_wanted({}, (str(vault),)) is False                                   # a comment or a longer name is not the setting
    second = tmp_path / "second.env"
    second.write_text("SKIPPY_EDGE_UPNP=yes\n", encoding="utf-8")
    assert ss.edge_upnp_wanted({}, (str(vault), str(second))) is True                        # the next vault is read when the first has no line


def test_nothing_happens_while_it_is_off(tmp_path, monkeypatch):
    said = []
    s, clock = make(tmp_path, said)
    monkeypatch.setattr(upnp_port, "Keeper", lambda **kw: pytest.fail("a keeper was made while the switch is off"))
    monkeypatch.setattr(upnp_port, "SocketNet", lambda: pytest.fail("the network was touched while the switch is off"))
    for _ in range(3):
        assert s.watch_edge() is False
        clock.later(400)
    assert said == [] and s._edge_keeper is None


# ── the rhythm ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_it_runs_at_the_first_chance_and_then_every_five_minutes(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIPPY_EDGE_UPNP", "1")
    said = []
    s, clock = make(tmp_path, said)
    fake = FakeKeeper()
    s._edge_keeper = fake
    assert s.watch_edge() is True and fake.rounds == 1
    for _ in range(29):                                                    # ten-second loop turns for four and a half minutes
        clock.later(10)
        assert s.watch_edge() is False
    assert fake.rounds == 1
    clock.later(10)                                                        # 300 s since the round
    assert s.watch_edge() is True and fake.rounds == 2


def test_it_makes_its_keeper_with_the_starters_own_log(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIPPY_EDGE_UPNP", "1")
    said = []
    s, _ = make(tmp_path, said)
    made = []
    monkeypatch.setattr(upnp_port, "Keeper", lambda **kw: made.append(kw) or FakeKeeper())
    assert s.watch_edge() is True
    assert len(made) == 1 and made[0]["say"] == s.say


def test_switching_it_off_forgets_the_keeper_and_switching_it_on_starts_fresh(tmp_path, monkeypatch):
    said = []
    s, clock = make(tmp_path, said)
    made = []
    monkeypatch.setattr(upnp_port, "Keeper", lambda **kw: made.append(1) or FakeKeeper())
    monkeypatch.setenv("SKIPPY_EDGE_UPNP", "1")
    s.watch_edge()
    clock.later(300)
    monkeypatch.setenv("SKIPPY_EDGE_UPNP", "0")
    assert s.watch_edge() is False and s._edge_keeper is None
    clock.later(300)
    monkeypatch.setenv("SKIPPY_EDGE_UPNP", "1")
    assert s.watch_edge() is True and len(made) == 2


def test_it_stands_down_while_the_pc_is_shutting_down_on_the_ups(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIPPY_EDGE_UPNP", "1")
    s, _ = make(tmp_path, [])
    s._edge_keeper = FakeKeeper()
    s.power_down = True
    assert s.watch_edge() is False and s._edge_keeper.rounds == 0


# ── what it says: with the real keeper and the stand-in router ───────────────────────────────────────────────────────────

def test_one_line_for_a_change_one_for_a_failure_and_one_for_the_end_of_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIPPY_EDGE_UPNP", "1")
    said = []
    s, clock = make(tmp_path, said)
    router = fake_igd.FakeRouter()
    s._edge_keeper = upnp_port.Keeper(net=router, say=s.say)

    def turn():
        clock.later(300)
        assert s.watch_edge() is True

    s.watch_edge()                                                         # the first round adds the forward
    assert len(said) == 1 and "edge upnp: added 443/TCP -> 192.168.0.164:47443" in said[0]
    for _ in range(4):
        turn()
    assert len(said) == 1                                                  # twenty minutes of all-is-well: nothing more
    router.mappings.clear()                                                # the router rebooted and forgot it
    turn()
    assert len(said) == 2 and "added" in said[1]
    router.down = True
    for _ in range(5):
        turn()
    assert len(said) == 3 and "FAILED" in said[2] and "did not answer" in said[2]        # twenty-five minutes of failing: one line
    router.down = False
    turn()
    assert len(said) == 4 and "back in order" in said[3]
    turn()
    assert len(said) == 4


# ── it can never reach the loop ──────────────────────────────────────────────────────────────────────────────────────────

def test_a_keeper_that_blows_up_is_caught_and_the_round_still_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIPPY_EDGE_UPNP", "1")
    said = []
    s, _ = make(tmp_path, said)
    s._edge_keeper = FakeKeeper(boom=RuntimeError("router on fire"))
    assert s.watch_edge() is True
    assert said and "crashed" in said[0] and "router on fire" in said[0]


def test_a_keeper_that_cannot_even_be_made_is_one_line_not_one_per_round(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIPPY_EDGE_UPNP", "1")
    said = []
    s, clock = make(tmp_path, said)

    def broken(**kw):
        raise ImportError("no module named upnp_port")
    monkeypatch.setattr(upnp_port, "Keeper", broken)
    for _ in range(4):
        assert s.watch_edge() is False
        clock.later(300)
    assert len(said) == 1 and "could not run" in said[0] and "no module named upnp_port" in said[0]


def test_even_a_broken_switch_check_does_not_raise(tmp_path, monkeypatch):
    said = []
    s, _ = make(tmp_path, said)
    monkeypatch.setattr(ss, "edge_upnp_wanted", lambda *a, **k: (_ for _ in ()).throw(OSError("the vault drive is gone")))
    assert s.watch_edge() is False
    assert len(said) == 1 and "the vault drive is gone" in said[0]


def test_the_main_loop_calls_it_after_the_other_watchers_and_survives_it_raising(tmp_path):
    said, order = [], []
    s, _ = make(tmp_path, said)

    class Stop(Exception):
        pass
    turns = []
    s.watch_power = lambda: order.append("power") or False
    s.check_resume = lambda: order.append("resume")
    s.watch_docker = lambda: order.append("docker")
    s.watch_logs = lambda: order.append("logs")
    s.poll_once = lambda *a, **k: order.append("poll")

    def edge():
        order.append("edge")
        raise ValueError("a bug that must stay inside the loop's own try")
    s.watch_edge = edge

    def sleep(_):
        turns.append(1)
        if len(turns) == 2:
            raise Stop
    s.sleep = sleep
    with pytest.raises(Stop):
        s.forever()
    assert order[:6] == ["power", "resume", "docker", "logs", "edge", "poll"]               # in that order, and poll still ran after edge raised
    assert order.count("poll") == 2 and any("auto-start crashed (ValueError)" in line for line in said)


# ── a slow router never stalls the loop ──────────────────────────────────────────────────────────────────────────────────

def test_the_round_runs_on_its_own_thread_and_only_one_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIPPY_EDGE_UPNP", "1")
    said = []
    s, clock = make(tmp_path, said)
    s.edge_inline = False
    gate, started = threading.Event(), threading.Event()

    class Slow:
        rounds = 0

        def round(self):
            Slow.rounds += 1
            started.set()
            gate.wait(10)
    s._edge_keeper = Slow()
    assert s.watch_edge() is True                                          # returned at once, the round is still going
    assert started.wait(5) and s._edge_thread.is_alive()
    clock.later(300)
    assert s.watch_edge() is False and Slow.rounds == 1                    # its time has come but the last round has not finished: no second one
    gate.set()
    s._edge_thread.join(5)
    clock.later(300)
    assert s.watch_edge() is True
    s._edge_thread.join(5)
    assert Slow.rounds == 2
