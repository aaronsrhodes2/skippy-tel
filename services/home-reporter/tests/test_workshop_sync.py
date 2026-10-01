"""The pieces the private Skippy's recovery is built from, one at a time (test_workshop_chaos.py runs them together
against the real portal objects): the push to the portal, the reconciler's pass (what it pushes, adopts, ends and,
as important, leaves alone), the start/attach rules, the quick-poll bookkeeping, the watchdog's wiring in main()."""
import json
import re
import threading
import types
import urllib.error
import urllib.parse

import pytest

import reporter
from test_music import Fake, _json

TOKEN = "home-token"
OWNER = "aaron@x.com"
CONV = "conv-12345678"
T1, T2 = "aaaa1111", "bbbb2222"


def ev(n):
    return {"t": "text", "text": f"event {n}"}


class BridgeStub:
    """The bridge's workshop routes over a dict of runs: {task: {conv, who, events, done, ok, error, finished_age_s}}."""

    def __init__(self, runs=None, runs_status=200, autofinish=False):
        self.runs, self.runs_status, self.autofinish = runs or {}, runs_status, autofinish    # autofinish: a started run ends at once
        self.cancelled, self.started, self.gets, self.forget_after_gets = [], [], [], None
        self.fake = Fake(self.handle)

    @staticmethod
    def run(who=OWNER, events=(), done=False, ok=True, error=None, finished_age_s=None, conv=CONV):
        return {"conv": conv, "who": who, "events": list(events), "done": done, "ok": ok if done else None,
                "error": error, "finished_age_s": finished_age_s}

    def handle(self, method, path, headers, body):
        if headers.get("Authorization") != f"Bearer {TOKEN}":
            return _json({"error": "unauthorized"}, 401)
        route, _, query = path.partition("?")
        q = dict(urllib.parse.parse_qsl(query))
        if method == "GET" and route == "/workshop/runs":
            if self.runs_status != 200:
                return _json({}, self.runs_status)
            return _json({"runs": [{"task": t, "conv": r["conv"], "who": r["who"], "events": len(r["events"]), "done": r["done"],
                                    "ok": r["ok"], "error": r["error"], "age_s": 5.0, "finished_age_s": r["finished_age_s"]}
                                   for t, r in reversed(list(self.runs.items()))]})
        m = re.fullmatch(r"/workshop/run/(\w+)", route)
        if method == "GET" and m:
            self.gets.append((m.group(1), int(q.get("after") or 0)))
            if self.forget_after_gets is not None and len(self.gets) > self.forget_after_gets:
                self.runs.pop(m.group(1), None)                    # the bridge restarted without its runs
            r = self.runs.get(m.group(1))
            if r is None:
                return _json({"error": "unknown_task"}, 404)
            after = int(q.get("after") or 0)
            return _json({"events": r["events"][after:], "next": len(r["events"]), "done": r["done"], "ok": r["ok"], "error": r["error"]})
        m = re.fullmatch(r"/workshop/cancel/(\w+)", route)
        if method == "POST" and m:
            self.cancelled.append(m.group(1))
            return _json({"ok": True})
        if method == "POST" and route == "/workshop":
            b = json.loads(body)
            self.started.append(b)
            if b["task"] in self.runs:
                return _json({"ok": True, "task": b["task"], "done": self.runs[b["task"]]["done"]})
            if b.get("attach"):
                return _json({"error": "unknown_task"}, 404)
            self.runs[b["task"]] = (self.run(who=b.get("who", ""), conv=b["conv"], events=[ev(0)], done=True, finished_age_s=0)
                                    if self.autofinish else self.run(who=b.get("who", ""), conv=b["conv"]))
            return _json({"ok": True})
        return _json({"error": "not_found"}, 404)


class PortalStub:
    """The portal's workshop routes: the listing and the push (appends at `have`, answers with it)."""

    def __init__(self, status=200):
        self.held: dict[str, list] = {}
        self.states: dict[str, tuple] = {}          # task -> (live state, final?)
        self.owners: dict[str, str] = {}
        self.pushes, self.listing_status, self.stop, self.have_override = [], status, False, {}
        self.wants: set = set()
        self.push_status = 200                       # what a push gets (503: the portal is redeploying)
        self.fake = Fake(self.handle)

    def seed(self, task, events=(), state="running", final=False, owner=OWNER):
        self.held[task], self.states[task], self.owners[task] = list(events), (state, final), owner

    def handle(self, method, path, headers, body):
        if headers.get("Authorization") != f"Bearer {TOKEN}":
            return _json({"error": "unauthorized"}, 401)
        if method == "GET":
            assert path == "/home/workshop"
            if self.listing_status != 200:
                return _json({}, self.listing_status)
            return _json({"tasks": [{"task": t, "state": self.states[t][0], "have": len(h), "final": self.states[t][1],
                                     "owner": self.owners[t], "quiet_s": 0, "want": t in self.wants} for t, h in self.held.items()]})
        if self.push_status != 200:
            return _json({}, self.push_status)
        task = path.rsplit("/", 1)[1]
        push = json.loads(body)
        self.pushes.append({"task": task, **push})
        held = self.held.setdefault(task, [])
        if push["start"] <= len(held):
            held += push["events"][len(held) - push["start"]:]
            self.states[task] = ("running", False) if push["state"] == "running" else (push["state"], True)
            self.owners.setdefault(task, push["who"])
        have = self.have_override.pop(task, len(held)) if task in self.have_override else len(held)
        return _json({"have": have, "stop": self.stop})


class SpyJobs:
    """What the reconciler uses of Jobs: the quick-poll deadline, accept() and is_running()."""

    def __init__(self):
        self.fast_until, self.accepted, self.running = 0.0, [], set()

    def accept(self, reply):
        self.accepted.append(reply)

    def is_running(self, task):
        return task in self.running


@pytest.fixture
def env(monkeypatch):
    made = []

    def make(runs=None, runs_status=200, portal_status=200, autofinish=False):
        bridge, portal = BridgeStub(runs, runs_status, autofinish), PortalStub(portal_status)
        made.extend([bridge.fake, portal.fake])
        monkeypatch.setattr(reporter, "BRIDGE", bridge.fake.url)
        monkeypatch.setattr(reporter, "PORTAL", portal.fake.url)
        monkeypatch.setattr(reporter, "WORKSHOP_WAIT", 0)
        monkeypatch.setattr(reporter, "START_TRIES", 1)
        monkeypatch.setattr(reporter, "WORKSHOP_MAX_S", 3)       # a relay that starts a run these stubs never finish must give up, not spin for 3 hours
        monkeypatch.setattr(reporter.time, "sleep", lambda s: None)
        jobs = SpyJobs()
        return bridge, portal, jobs, reporter.WorkshopSync(TOKEN, jobs)
    yield make
    for f in made:
        f.close()


def pushed(portal, task=None):
    return [(p["start"], len(p["events"]), p["state"], p["error"]) for p in portal.pushes if task in (None, p["task"])]


# ── the push and the portal GET ─────────────────────────────────────────────────────────────────
def test_push_workshop_sends_the_token_and_the_run_and_returns_the_portals_reply(env):
    _, portal, _, _ = env()
    reply = reporter.push_workshop(TOKEN, T1, OWNER, CONV, 3, [ev(3), ev(4)], "running", None)
    assert reply == {"have": 0, "stop": False}                          # the portal has nothing: start 3 left a gap
    [call] = portal.fake.calls
    assert call["path"] == f"/home/workshop/{T1}" and call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(call["body"]) == {"who": OWNER, "conv": CONV, "start": 3, "events": [ev(3), ev(4)], "state": "running", "error": None}


def test_push_workshop_returns_none_when_the_portal_doesnt_take_it_and_says_so_once(env, capsys, monkeypatch):
    _, portal, _, _ = env(portal_status=200)
    assert reporter.push_workshop("wrong-token", T1, OWNER, CONV, 0, [], "running") is None            # 401
    assert reporter.push_workshop("wrong-token", T1, OWNER, CONV, 0, [], "running") is None
    out = capsys.readouterr().out
    assert out.count("HTTP 401") == 1 and "pair again" in out                                          # not once per attempt
    assert reporter.push_workshop(TOKEN, T1, OWNER, CONV, 0, [], "running") is not None                 # it recovers ...
    reporter.push_workshop("wrong-token", T1, OWNER, CONV, 0, [], "running")
    assert capsys.readouterr().out.count("HTTP 401") == 1                                               # ... and says it again if it breaks again
    def refused(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(reporter.urllib.request, "urlopen", refused)
    assert reporter.push_workshop(TOKEN, T1, OWNER, CONV, 0, [], "running") is None                     # nothing listening
    assert "can't reach the portal" in capsys.readouterr().out


def test_portal_get_sends_the_same_bearer_header_and_returns_the_json_object(env):
    _, portal, _, _ = env()
    portal.seed(T1, [ev(0)])
    body, why = reporter.portal_get(TOKEN, "/home/workshop")
    assert body["tasks"][0]["have"] == 1 and why == ""
    assert portal.fake.calls[-1]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert reporter.portal_get("nope", "/home/workshop")[0] is None
    assert "401" in reporter.portal_get("nope", "/home/workshop")[1]


def test_accept_keeps_the_quick_poll_going_unless_the_reply_sets_it(monkeypatch):
    started = []
    monkeypatch.setattr(reporter, "threading", types.SimpleNamespace(
        Lock=threading.Lock,
        Thread=lambda target, args, daemon: types.SimpleNamespace(start=lambda: started.append(args[0]["id"]))))
    jobs = reporter.Jobs(TOKEN)
    jobs.accept({"jobs": [], "fast_for_s": 300})
    deadline = jobs.fast_until
    assert deadline > reporter._now() + 200
    jobs.accept({"jobs": [{"id": "x1"}]})                  # the reconciler adopting a run: a reply with no fast_for_s
    assert jobs.fast_until == deadline and started == ["x1"] and jobs.is_running("x1")
    jobs.accept({"jobs": [{"id": "x1"}]})                  # already being worked: not started twice
    assert started == ["x1"]
    jobs.accept({"jobs": [], "fast_for_s": 0})
    assert jobs.fast_until <= reporter._now()


# ── the reconciler ───────────────────────────────────────────────────────────────────────────────
def test_it_invents_nothing_when_either_side_is_silent_and_says_so_once(env, capsys):
    bridge, portal, jobs, sync = env({T1: BridgeStub.run(events=[ev(0)])}, runs_status=500)
    portal.seed(T2, [ev(0)])                                # a record the bridge doesn't list: would be "lost" if it had answered
    assert sync.sync_once() == {"pushed": [], "adopted": [], "lost": []}          # the bridge is silent (HTTP 500)
    sync.sync_once()
    assert portal.pushes == [] and jobs.accepted == []
    assert capsys.readouterr().out.count("the bridge didn't answer") == 1
    bridge.runs_status = 200
    portal.listing_status = 503                              # now the portal is
    assert sync.sync_once() == {"pushed": [], "adopted": [], "lost": []}
    assert portal.pushes == [] and bridge.gets == []         # not even a look at the run
    assert "the portal didn't answer (HTTP 503)" in capsys.readouterr().out


def test_a_run_the_portal_lacks_is_pushed_in_chunks_and_ends_with_the_runs_own_state(env, monkeypatch):
    monkeypatch.setattr(reporter, "WORKSHOP_PUSH", 4)
    events = [ev(n) for n in range(10)]
    bridge, portal, jobs, sync = env({T1: BridgeStub.run(events=events, done=True, ok=False, error="Claude Code failed", finished_age_s=30)})
    assert sync.sync_once() == {"pushed": [T1], "adopted": [], "lost": []}        # done: nothing to adopt
    assert [(s, n, st) for s, n, st, _ in pushed(portal)] == [(0, 4, "running"), (4, 4, "running"), (8, 2, "error")]
    assert pushed(portal)[-1][3] == "Claude Code failed" and portal.held[T1] == events
    assert portal.pushes[0]["who"] == OWNER and portal.pushes[0]["conv"] == CONV
    assert sync.sync_once()["pushed"] == []                                       # delivered: the next pass leaves it alone


def test_a_run_that_ended_ok_is_reported_done_and_one_without_an_error_text_still_says_something(env):
    _, portal, _, sync = env({T1: BridgeStub.run(events=[ev(0)], done=True, ok=True, finished_age_s=1),
                              T2: BridgeStub.run(events=[ev(0)], done=True, ok=False, error=None, finished_age_s=1)})
    sync.sync_once()
    assert pushed(portal, T1) == [(0, 1, "done", None)]
    [(_, _, state, error)] = pushed(portal, T2)
    assert state == "error" and error                       # the chat always gets a reason


def test_a_run_still_going_is_always_pushed_even_with_nothing_new_and_handed_to_the_fast_path(env):
    bridge, portal, jobs, sync = env({T1: BridgeStub.run(events=[ev(0), ev(1)])})
    portal.seed(T1, [ev(0), ev(1)])                          # the portal holds everything the run has so far
    assert sync.sync_once() == {"pushed": [T1], "adopted": [T1], "lost": []}
    assert pushed(portal) == [(2, 0, "running", None)]       # a heartbeat: no event, but it keeps the portal from "stalled"
    assert jobs.accepted == [{"jobs": [{"id": T1, "kind": "workshop", "conv": CONV, "who": OWNER,
                                        "message": "(re-attach)", "attach": True}]}]
    jobs.running.add(T1)                                     # now a relay thread streams it
    jobs.accepted.clear()
    sync.sync_once()
    assert jobs.accepted == [] and len(portal.pushes) == 2   # still heartbeats, and doesn't adopt a run that has a thread


def test_it_sends_from_the_portals_have_not_from_what_it_thought(env):
    events = [ev(n) for n in range(6)]
    bridge, portal, jobs, sync = env({T1: BridgeStub.run(events=events)})
    portal.seed(T1, events[:4])
    portal.have_override[T1] = 1                             # on the first push the portal says it only holds 1 (it was redeployed)
    sync.sync_once()
    assert [(s, n) for s, n, _, _ in pushed(portal)] == [(4, 2), (1, 5), (6, 0)]    # then it resends from there, then heartbeats
    assert portal.held[T1] == events


def test_the_portals_stop_cancels_the_run_on_the_bridge(env):
    bridge, portal, jobs, sync = env({T1: BridgeStub.run(events=[ev(0)])})
    portal.stop = True
    sync.sync_once()
    assert bridge.cancelled == [T1]


def test_a_task_the_bridge_no_longer_has_ends_in_a_plain_error_but_only_a_running_one(env):
    bridge, portal, jobs, sync = env({})
    portal.seed("cccc3333", [ev(0), ev(1)], state="running")
    portal.seed("dddd4444", [ev(0)], state="stalled")
    portal.seed("eeee5555", [], state="queued")              # the job queue still owns it: not lost, just not picked up yet
    portal.seed("ffff6666", [], state="offline")
    portal.seed("aaaa0000", [ev(0)], state="done", final=True)
    jobs.running.add("1111aaaa")
    portal.seed("1111aaaa", [ev(0)], state="running")        # a relay thread owns it: it will say what's true
    out = sync.sync_once()
    assert sorted(out["lost"]) == ["cccc3333", "dddd4444"] and out["pushed"] == []
    [(s1, n1, st1, err1)] = pushed(portal, "cccc3333")
    assert (s1, n1, st1) == (2, 0, "error") and err1 == reporter.LOST_TASK       # from where the portal is, no events
    assert "Send your message again" in reporter.LOST_TASK
    assert pushed(portal, "eeee5555") == pushed(portal, "ffff6666") == pushed(portal, "aaaa0000") == pushed(portal, "1111aaaa") == []
    assert {p["who"] for p in portal.pushes} == {OWNER}


def test_it_leaves_alone_a_run_without_an_owner_one_over_an_hour_old_and_one_the_portal_has_whole(env):
    events = [ev(0), ev(1)]
    bridge, portal, jobs, sync = env({
        "aaaa0001": BridgeStub.run(who="", events=events),                                             # nobody could read it
        "aaaa0002": BridgeStub.run(events=events, done=True, finished_age_s=3601),                     # over an hour ago
        "aaaa0003": BridgeStub.run(events=events, done=True, finished_age_s=None),                     # (no age: not recent)
        "aaaa0004": BridgeStub.run(events=events, done=True, ok=True, finished_age_s=10),              # the portal holds it all
        "aaaa0005": BridgeStub.run(events=events, done=True, ok=True, finished_age_s=3599),            # just under: still delivered
    })
    portal.seed("aaaa0004", events, state="done", final=True)
    out = sync.sync_once()
    assert out == {"pushed": ["aaaa0005"], "adopted": [], "lost": []}
    assert jobs.accepted == [] and bridge.gets == [("aaaa0005", 0)]


def test_a_complete_record_is_still_repaired_when_it_disagrees_or_a_person_asked_again(env):
    events = [ev(0), ev(1)]
    bridge, portal, jobs, sync = env({"aaaa0001": BridgeStub.run(events=events, done=True, ok=True, finished_age_s=10),
                                      "aaaa0002": BridgeStub.run(events=events, done=True, ok=True, finished_age_s=10),
                                      "aaaa0003": BridgeStub.run(events=events, done=True, ok=True, finished_age_s=10),
                                      "aaaa0004": BridgeStub.run(events=events, done=True, ok=True, finished_age_s=10)})
    portal.seed("aaaa0001", events, state="error", final=True)           # the relay's guess ("lost contact") was wrong: the bridge says done
    portal.seed("aaaa0002", events[:1], state="done", final=True)         # fewer events than the run has
    portal.seed("aaaa0003", events, state="running")                     # not final yet
    portal.seed("aaaa0004", events, state="done", final=True)
    portal.wants.add("aaaa0004")                                         # complete, but someone pressed "check again"
    assert sorted(sync.sync_once()["pushed"]) == ["aaaa0001", "aaaa0002", "aaaa0003", "aaaa0004"]
    assert pushed(portal, "aaaa0001") == [(2, 0, "done", None)]


def test_a_pass_stops_when_a_side_goes_quiet_and_ends_no_task_on_a_guess(env):
    runs = {T1: BridgeStub.run(events=[ev(0)]), T2: BridgeStub.run(events=[ev(0)])}
    bridge, portal, jobs, sync = env(runs)
    portal.seed(T1, [], state="running")
    portal.seed("cccc3333", [ev(0)], state="running")        # not on the bridge: lost, IF the pass could tell
    portal.push_status = 503                                  # the portal takes the listing but is failing pushes (a deploy under way)
    assert sync.sync_once() == {"pushed": [], "adopted": [], "lost": []}
    assert [c["method"] for c in portal.fake.calls].count("POST") == 1     # one try, not one per run: no 20 s wait for each
    assert jobs.accepted == [] and pushed(portal) == []
    portal.push_status = 200
    out = sync.sync_once()
    assert sorted(out["pushed"]) == [T1, T2] and out["lost"] == ["cccc3333"]


def test_a_failure_is_said_once_not_every_pass(env, capsys):
    bridge, portal, jobs, sync = env({})
    portal.listing_status = 500
    for _ in range(5):
        sync.sync_once()
    assert capsys.readouterr().out.count("workshop sync") == 1


def test_the_pass_is_quick_while_someone_is_watching_and_lazy_otherwise(env):
    _, _, jobs, sync = env()
    jobs.fast_until = 0.0
    assert sync.interval() == reporter.SYNC_INTERVAL == 10
    jobs.fast_until = reporter._now() + 100
    assert sync.interval() == reporter.SYNC_FAST == 2


def test_the_loop_runs_a_pass_on_schedule_and_survives_a_pass_that_blows_up(env, monkeypatch, capsys):
    _, _, jobs, sync = env()
    clock, passes = [1000.0], []
    monkeypatch.setattr(reporter, "_now", lambda: clock[0])

    def boom():
        passes.append(clock[0])
        raise RuntimeError("weird")
    sync.sync_once = boom

    def tick(s):
        clock[0] += s
        if clock[0] > 1035:
            raise KeyboardInterrupt
    monkeypatch.setattr(reporter.time, "sleep", tick)
    with pytest.raises(KeyboardInterrupt):
        sync.run_forever()
    assert passes == [1000.0, 1010.0, 1020.0, 1030.0]       # first at once, then every SYNC_INTERVAL; exceptions don't end the loop
    assert capsys.readouterr().out.count("workshop sync failed") == 1


# ── starting and attaching ──────────────────────────────────────────────────────────────────────
JOB = {"id": T1, "kind": "workshop", "conv": CONV, "message": "make the tile green", "who": OWNER}


def test_a_start_on_a_bridge_that_is_restarting_is_tried_again(env, monkeypatch):
    bridge, portal, jobs, sync = env({}, autofinish=True)
    monkeypatch.setattr(reporter, "START_TRIES", 3)
    real, calls = reporter._bridge, []

    def flaky(method, path, body, token, timeout=40):
        if path == "/workshop":
            calls.append(1)
            if len(calls) < 3:
                return None, {}                                  # refused: it is coming back up
        return real(method, path, body, token, timeout)
    monkeypatch.setattr(reporter, "_bridge", flaky)
    assert reporter.run_job(JOB, TOKEN) == {"result": {"state": "done"}}
    assert len(calls) == 3 and [b["task"] for b in bridge.started] == [T1]       # two refusals, then it took it, once
    monkeypatch.setattr(reporter, "START_TRIES", 2)
    bridge.runs.clear()
    monkeypatch.setattr(reporter, "_bridge", lambda m, p, b, t, timeout=40: (None, {}) if p == "/workshop" else real(m, p, b, t, timeout))
    assert "isn't running" in reporter.run_job(JOB, TOKEN)["error"]            # and it gives up with the plain message after the last try


def test_a_job_the_portal_already_started_only_attaches_and_never_starts_the_run_again(env):
    """The queue offers the job again after a reporter restart. If the portal holds events for it the run existed; if the
    bridge has forgotten it, starting it afresh would redo the work and splice a second run's events onto the first's."""
    bridge, portal, jobs, sync = env({})
    portal.seed(T1, [ev(0), ev(1)], state="running")
    out = reporter.run_job(JOB, TOKEN)
    assert out == {"error": reporter.LOST_TASK}
    assert [b["attach"] for b in bridge.started] == [True] and bridge.runs == {}                 # asked to join, started nothing
    assert pushed(portal)[-1][2:] == ("error", reporter.LOST_TASK)


def test_a_job_the_portal_has_not_started_yet_starts_the_run(env):
    bridge, portal, jobs, sync = env({}, autofinish=True)
    portal.seed(T1, [], state="queued")
    reporter.run_job(JOB, TOKEN)
    assert [b["attach"] for b in bridge.started] == [False] and T1 in bridge.runs


def test_an_attach_job_never_starts_a_run_and_ends_the_chat_plainly(env):
    bridge, portal, jobs, sync = env({})
    out = reporter.run_job({**JOB, "message": "(re-attach)", "attach": True}, TOKEN)
    assert out == {"error": reporter.LOST_TASK} and bridge.runs == {}
    assert bridge.started[0]["attach"] is True and bridge.started[0]["who"] == OWNER
    assert pushed(portal) == [(0, 0, "error", reporter.LOST_TASK)]


def test_twenty_silent_rounds_end_the_relay_without_telling_the_portal_anything(env, monkeypatch):
    """The run may be fine and only the link down: an error pushed now would stick. The reconciler decides later."""
    bridge, portal, jobs, sync = env({T1: BridgeStub.run(events=[ev(0)])})
    bridge.runs_status = 200
    real = reporter._bridge
    misses = []

    def silent(method, path, body, token, timeout=40):
        if path.startswith("/workshop/run/"):
            misses.append(1)
            return None, {}
        return real(method, path, body, token, timeout)
    monkeypatch.setattr(reporter, "_bridge", silent)
    out = reporter.run_job({**JOB, "attach": True}, TOKEN)
    assert "Lost contact" in out["error"] and len(misses) == 20
    assert portal.pushes == []


def test_the_bridge_losing_the_task_mid_run_ends_the_chat_with_the_plain_message(env):
    bridge, portal, jobs, sync = env({T1: BridgeStub.run(events=[ev(0)])})
    bridge.forget_after_gets = 1                                  # answers the first look, then it has restarted without its runs
    assert reporter.run_job({**JOB, "attach": True}, TOKEN) == {"error": reporter.LOST_TASK}
    assert pushed(portal) == [(0, 1, "running", None), (0, 0, "error", reporter.LOST_TASK)]


# ── the self-watchdog's wiring in main() ───────────────────────────────────────────────────────
class _StopMain(Exception):
    pass


def _run_main_once(monkeypatch, portal_status, bridge_url):
    """main() through one report to a portal answering `portal_status`; returns (the targets it started threads for, the
    watchdog it made)."""
    portal = Fake(lambda m, p, h, b: _json({"commands": [], "jobs": []}, portal_status))
    monkeypatch.setenv("SKIPPY_HOME_TOKEN", TOKEN)
    monkeypatch.setattr(reporter, "PORTAL", portal.url)
    monkeypatch.setattr(reporter, "BRIDGE", bridge_url)
    monkeypatch.setattr(reporter, "gather", lambda: {"status": None})
    monkeypatch.setattr(reporter.SiteTelemetry, "collect", lambda self: 0)
    targets, dogs = [], []
    monkeypatch.setattr(reporter, "threading", types.SimpleNamespace(
        Lock=threading.Lock, Thread=lambda target, daemon=True: types.SimpleNamespace(start=lambda: targets.append(target))))
    real_dog = reporter.Watchdog

    class Spy(real_dog):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.oks = 0
            dogs.append(self)

        def ok(self):
            self.oks += 1
            super().ok()
    monkeypatch.setattr(reporter, "Watchdog", Spy)

    def stop(_):
        raise _StopMain
    monkeypatch.setattr(reporter.time, "sleep", stop)
    try:
        with pytest.raises(_StopMain):
            reporter.main()
    finally:
        portal.close()
    return targets, dogs[0]


def test_main_starts_the_reconciler_only_when_there_is_a_bridge_and_always_the_watchdog(monkeypatch):
    targets, dog = _run_main_once(monkeypatch, 200, "http://127.0.0.1:9")
    names = [getattr(t, "__qualname__", "") for t in targets]
    assert "WorkshopSync.run_forever" in names and "Watchdog.run_forever" in names and "Jobs.poll_forever" in names
    assert "SiteTelemetry.run_forever" in names                  # (test_reporter_heartbeat.py: it is off the report loop)
    targets, _ = _run_main_once(monkeypatch, 200, "")
    names = [getattr(t, "__qualname__", "") for t in targets]
    assert "WorkshopSync.run_forever" not in names and "Watchdog.run_forever" in names


def test_main_tells_the_watchdog_about_a_report_that_got_through_and_not_about_one_that_didnt(monkeypatch):
    _, dog = _run_main_once(monkeypatch, 200, "")
    assert dog.oks == 1
    _, dog = _run_main_once(monkeypatch, 500, "")
    assert dog.oks == 0
