"""The reporter's private-Skippy job against a fake bridge and a fake portal: start the run, relay its
events, ride out a redeployed portal (which then reports fewer events than we sent), pass Stop along, and
say plainly when the bridge is down or refuses."""
import json
import re

import pytest

import reporter
from test_music import Fake, _json

TOKEN = "home-token"
JOB = {"id": "abc12345", "kind": "workshop", "conv": "conv-12345678", "message": "make the tile green", "who": "aaron@x.com"}
EVENTS = [{"t": "text", "text": "On it."}, {"t": "tool", "name": "Bash", "detail": "git push origin main"},
          {"t": "text", "text": "Deployed."}]


class FakeBridge:
    """/health, /workshop (start), /workshop/run/<task> (events from `after`), /workshop/cancel.
    `visible` is how many events exist at each successive poll; the run is done at the last one."""

    def __init__(self, visible=(3,), ok=True, error=None, start_status=200):
        self.visible, self.ok, self.error, self.start_status = list(visible), ok, error, start_status
        self.polled, self.cancelled, self.started = 0, [], []
        self.fake = Fake(self.handle)

    def handle(self, method, path, headers, body):
        if path == "/health":
            return _json({"status": "ok", "workshop": {"ready": True, "model": "claude-opus-5-5"}})
        if headers.get("Authorization") != f"Bearer {TOKEN}":
            return _json({"error": "unauthorized"}, 401)
        if path == "/workshop":
            self.started.append(json.loads(body))
            return _json({"ok": True} if self.start_status == 200 else {"message": "busy"}, self.start_status)
        m = re.match(r"/workshop/run/(\w+)\?after=(\d+)", path)
        if m:
            after = int(m.group(2))
            count = self.visible[min(self.polled, len(self.visible) - 1)]
            self.polled += 1
            done = self.polled >= len(self.visible)
            return _json({"events": EVENTS[after:count], "next": count, "done": done,
                          "ok": self.ok if done else None, "error": self.error if done else None})
        if path.startswith("/workshop/cancel/"):
            self.cancelled.append(path.rsplit("/", 1)[1])
            return _json({"ok": True})
        return _json({}, 404)


class FakePortal:
    """Keeps pushed events like portal/workshop.py (appends what continues its list) and answers with
    `have` and `stop`. `forget_at` empties it just before that push, as a redeploy would."""

    def __init__(self, forget_at=None, stop=False):
        self.held, self.pushes, self.forget_at, self.stop, self.final = [], [], forget_at, stop, None
        self.fake = Fake(self.handle)

    def handle(self, method, path, headers, body):
        if method == "GET":                      # the listing (portal/workshop.py `listing`): what the portal holds of each task
            assert path == "/home/workshop"
            state = self.final[0] if self.final else ("running" if self.pushes else "queued")
            return _json({"tasks": [{"task": JOB["id"], "state": state, "have": len(self.held),
                                     "final": bool(self.final), "owner": JOB["who"], "quiet_s": 0, "want": False}]
                          if self.pushes else []})
        assert path == f"/home/workshop/{JOB['id']}"
        push = json.loads(body)
        self.pushes.append(push)
        if self.forget_at == len(self.pushes):
            self.held = []
        if push["start"] <= len(self.held):
            self.held += push["events"][len(self.held) - push["start"]:]
            if push["state"] != "running":
                self.final = (push["state"], push["error"])
        return _json({"have": len(self.held), "stop": self.stop})


@pytest.fixture
def wired(monkeypatch):
    made = []

    def make(bridge=None, portal=None):
        bridge, portal = bridge or FakeBridge(), portal or FakePortal()
        made.extend([bridge.fake, portal.fake])
        monkeypatch.setattr(reporter, "BRIDGE", bridge.fake.url)
        monkeypatch.setattr(reporter, "PORTAL", portal.fake.url)
        monkeypatch.setattr(reporter, "WORKSHOP_WAIT", 0)
        monkeypatch.setattr(reporter, "START_TRIES", 1)          # a refused connection is slow on Windows: one try per start
        monkeypatch.setattr(reporter, "WORKSHOP_MAX_S", 5)       # a relay that never sees the end of a fake run gives up, not spins
        monkeypatch.setattr(reporter.time, "sleep", lambda s: None)
        return bridge, portal
    yield make
    for f in made:
        f.close()


def test_a_run_is_started_on_the_bridge_and_its_events_reach_the_portal(wired):
    bridge, portal = wired()
    assert reporter.run_job(JOB, TOKEN) == {"result": {"state": "done"}}
    assert bridge.started == [{"task": "abc12345", "conv": "conv-12345678", "message": "make the tile green",
                               "who": "aaron@x.com", "attach": False, "project": ""}]   # "" = the bridge's default
    assert portal.held == EVENTS and portal.final == ("done", None)
    assert portal.pushes[0]["who"] == "aaron@x.com" and portal.pushes[0]["conv"] == "conv-12345678"
    assert all(c["headers"].get("Authorization") == f"Bearer {TOKEN}" for c in portal.fake.calls)


def test_a_failed_run_reports_its_error(wired):
    _, portal = wired(FakeBridge(ok=False, error="Claude Code on skippy-pc isn't logged in"))
    assert reporter.run_job(JOB, TOKEN) == {"result": {"state": "error"}}
    assert portal.final == ("error", "Claude Code on skippy-pc isn't logged in")


def test_events_keep_flowing_while_the_run_is_still_going(wired):
    _, portal = wired(FakeBridge(visible=(0, 0, 3)))
    reporter.run_job(JOB, TOKEN)
    assert [p["state"] for p in portal.pushes] == ["running", "running", "done"] and portal.held == EVENTS


def test_a_redeployed_portal_gets_the_events_it_lost(wired):
    _, portal = wired(FakeBridge(visible=(2, 3)), FakePortal(forget_at=2))     # the portal restarts between pushes
    assert reporter.run_job(JOB, TOKEN) == {"result": {"state": "done"}}
    assert [(p["start"], len(p["events"]), p["state"]) for p in portal.pushes] == \
        [(0, 2, "running"), (2, 1, "done"), (0, 3, "done")]     # the third push starts over, from what it holds
    assert portal.held == EVENTS and portal.final == ("done", None)


def test_more_than_one_chunk_of_events_is_split(wired, monkeypatch):
    monkeypatch.setattr(reporter, "WORKSHOP_PUSH", 2)
    _, portal = wired()
    reporter.run_job(JOB, TOKEN)
    assert [(len(p["events"]), p["state"]) for p in portal.pushes] == [(2, "running"), (1, "done")]
    assert portal.held == EVENTS


def test_stop_from_the_portal_cancels_the_run(wired):
    bridge, _ = wired(portal=FakePortal(stop=True))
    reporter.run_job(JOB, TOKEN)
    assert bridge.cancelled == ["abc12345"]


def test_a_busy_bridge_and_a_missing_bridge_say_so(wired, monkeypatch):
    _, portal = wired(FakeBridge(start_status=409))
    assert "still working" in reporter.run_job(JOB, TOKEN)["error"] and portal.final[0] == "error"
    monkeypatch.setattr(reporter, "BRIDGE", "http://127.0.0.1:9")            # nothing listens there
    out = reporter.run_job(JOB, TOKEN)
    assert "isn't running" in out["error"] and "make bridge" in out["error"]
    monkeypatch.setattr(reporter, "BRIDGE", "")
    assert "isn't set up" in reporter.run_job(JOB, TOKEN)["error"]


def test_a_wrong_token_is_explained(wired):
    wired()
    assert "SKIPPY_HOME_TOKEN" in reporter.run_job(JOB, "not-the-token")["error"]


def test_the_capability_says_ready_and_the_model_when_the_bridge_reports_the_workshop(wired, monkeypatch):
    wired()
    assert reporter.workshop_capability() == {"ready": True, "model": "claude-opus-5-5"}
    assert reporter.capabilities()["workshop"]["ready"] is True
    monkeypatch.setattr(reporter, "BRIDGE", "http://127.0.0.1:9")
    assert reporter.workshop_capability()["ready"] is False
    monkeypatch.setattr(reporter, "BRIDGE", "")
    assert reporter.workshop_capability() == {"ready": False}


# ── why the workshop is not ready: the bridge's `login` and `reason` reach the portal's card ────────────────────────
def _bridge_saying(workshop):
    return Fake(lambda method, path, headers, body: _json({"status": "ok", "workshop": workshop}))


EXPIRED = "Claude's login on skippy-pc expired: run `claude auth login` there"
NO_TOKEN = "SKIPPY_HOME_TOKEN isn't configured on this PC"


@pytest.mark.parametrize("said, passed_on", [
    # not ready: the bridge's own words (a `backticked` command stays: the portal makes it copyable) and its login state
    ({"ready": False, "model": "claude-opus-5-5", "busy": False, "login": "expired", "reason": EXPIRED},
     {"ready": False, "model": "claude-opus-5-5", "login": "expired", "reason": EXPIRED}),
    ({"ready": False, "model": "claude-opus-5-5", "login": "unknown", "reason": NO_TOKEN},
     {"ready": False, "model": "claude-opus-5-5", "login": "unknown", "reason": NO_TOKEN}),
    # ready: the login rides along, and a reason (there should be none) is not forwarded: the card says nothing is wrong
    ({"ready": True, "model": "claude-opus-5-5", "login": "ok"}, {"ready": True, "model": "claude-opus-5-5", "login": "ok"}),
    ({"ready": True, "model": "m", "login": "ok", "reason": "left over"}, {"ready": True, "model": "m", "login": "ok"}),
    # an older bridge says neither: exactly what it always sent
    ({"ready": True, "model": "claude-opus-5-5"}, {"ready": True, "model": "claude-opus-5-5"}),
    ({"ready": False, "model": "claude-opus-5-5"}, {"ready": False, "model": "claude-opus-5-5"}),
    # odd values: an unknown login word is dropped, the reason becomes one short line, only a real True is "ready"
    ({"ready": False, "model": "m", "login": "banana", "reason": "  two\nlines\t here  "}, {"ready": False, "model": "m", "reason": "two lines here"}),
    ({"ready": False, "model": "m", "reason": "x" * 500}, {"ready": False, "model": "m", "reason": "x" * 200}),
    ({"ready": "yes", "model": "m", "reason": 7}, {"ready": False, "model": "m"}),
])
def test_the_capability_passes_the_bridges_login_and_reason_through(monkeypatch, said, passed_on):
    bridge = _bridge_saying(said)
    try:
        monkeypatch.setattr(reporter, "BRIDGE", bridge.url)
        assert reporter.workshop_capability() == passed_on
        assert reporter.capabilities()["workshop"] == passed_on             # and that is what the report carries
    finally:
        bridge.close()


def test_a_bridge_that_does_not_answer_is_the_reason_when_it_is_the_whole_story(monkeypatch):
    monkeypatch.setattr(reporter, "BRIDGE", "http://127.0.0.1:9")            # nothing listens there
    got = reporter.workshop_capability()
    assert got["ready"] is False and "isn't answering" in got["reason"] and "make bridge" in got["reason"]
    monkeypatch.setattr(reporter, "BRIDGE", "")
    assert reporter.workshop_capability() == {"ready": False}               # not set up at all: nothing to add
    bridge = Fake(lambda m, p, h, b: _json([1, 2, 3]))                      # an answer that isn't what a bridge says
    try:
        monkeypatch.setattr(reporter, "BRIDGE", bridge.url)
        assert reporter.workshop_capability() == {"ready": False, "model": ""}
    finally:
        bridge.close()
