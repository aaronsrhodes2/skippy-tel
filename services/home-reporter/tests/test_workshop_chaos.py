"""The private Skippy's link, under everything that has gone wrong with it (and what might).

The incident: the agent pushed portal changes (each push redeploys the Cloud Run portal, wiping its in-memory task
records AND its home job queue), then rebuilt `home-reporter` as its last step, killing the relay thread. The run
itself finished fine on the bridge (67 events, ok); the portal never heard the rest, held a stale "running" record
(state "stalled") for ever, and the browser stayed stuck.

The invariant these tests hold the code to: every run on the bridge that the portal's chat refers to ends up
fully visible in the portal (every event, then the final state) within about 30 seconds of all three components
being up, whatever order the portal instance, the reporter and the bridge restarted in, and whether or not the
home job queue still holds the job. And the browser's read() never ends "stalled".

What is real here: portal/workshop.py and portal/home.py (the actual objects, driven by a fake clock) and
reporter.py's own functions (Jobs, workshop(), WorkshopSync ...) with real threads and real HTTP (urllib) against
two tiny in-process servers: a portal front that forwards the reporter's calls to the real Workshop/Home, and a
bridge (an in-memory list of runs with scripted events and the bridge's start()/since()/attach/who/restart
rules). What is fake: the clock, the Claude agent (it emits scripted events), and the thread scheduling: a relay
thread only advances when the test releases it (reporter.time.sleep is a gate), so every scenario is
deterministic and a whole outage takes milliseconds.

Scenarios 1-14 are the numbered ones in the hardening spec.
"""
import hashlib
import json
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "portal"))      # import the portal's modules flat, as the image does

import home as home_mod  # noqa: E402
import reporter  # noqa: E402
import workshop as workshop_mod  # noqa: E402
from test_music import Fake, _json  # noqa: E402

TOKEN = "home-token"
OWNER = "aaron@x.com"
CONV = "conv-12345678"


class Clock:
    def __init__(self):
        self.now = 10_000.0

    def __call__(self):
        return self.now


class Vm:
    """Just enough of the portal's VM for Home's pairing: the paired token's hash."""

    def __init__(self, token):
        self._meta = json.dumps({"token_sha256": hashlib.sha256(token.encode()).hexdigest()})

    def get_metadata(self, key):
        return self._meta

    def set_metadata(self, key, value):
        pass


# ── the bridge (services/claude-bridge: Contract A's rules) ──────────────────────────────────────
class BridgeRun:
    def __init__(self, task, conv, who, born, total, rate, ok, error):
        self.task, self.conv, self.who, self.born = task, conv, who, born
        self.events: list[dict] = []
        self.done, self.ok, self.error, self.finished = False, None, None, None
        self.total, self.rate, self.owed, self.ok_at_end, self.error_at_end = total, rate, 0.0, ok, error

    def advance(self, dt, now):
        if self.done:
            return
        self.owed += dt * self.rate
        while self.owed >= 1 and len(self.events) < self.total:
            self.owed -= 1
            n = len(self.events)
            self.events.append({"t": "tool", "name": "Bash", "detail": f"step {n}"} if n % 3 == 1
                               else {"t": "text", "text": f"event {n}"})
        if len(self.events) >= self.total:
            self.finish(self.ok_at_end, self.error_at_end, now)

    def finish(self, ok, error, now):
        self.done, self.ok, self.error, self.finished = True, ok, error, now


class BridgeSim:
    """/workshop (start; `attach` never starts anything), /workshop/run/<task>, /workshop/runs, /workshop/cancel/<task>."""

    def __init__(self, clock):
        self.clock, self.up, self.lock = clock, True, threading.RLock()
        self.runs: dict[str, BridgeRun] = {}
        self.script = {"total": 20, "rate": 1.0, "ok": True, "error": None}      # what the next run will do
        self.starts, self.cancelled = 0, []                                      # Claude runs started; cancels received

    def tick(self, dt):
        with self.lock:
            if self.up:
                for run in self.runs.values():
                    run.advance(dt, self.clock())

    def restart(self, persist):
        """The bridge process restarted. With persistence it reloads its runs, closing any that were mid-way;
        without (the bridge before this hardening) it forgets everything."""
        with self.lock:
            if not persist:
                self.runs = {}
                return
            for run in self.runs.values():
                if not run.done:
                    run.finish(False, "The bridge restarted while this was running.", self.clock())

    def handle(self, method, path, headers, body):
        if headers.get("Authorization") != f"Bearer {TOKEN}":
            return _json({"error": "unauthorized"}, 401)
        route, _, query = path.partition("?")
        q = dict(urllib.parse.parse_qsl(query))
        with self.lock:
            if method == "POST" and route == "/workshop":
                return self._start(json.loads(body or b"{}"))
            if method == "GET" and route == "/workshop/runs":
                now = self.clock()
                rows = [{"task": r.task, "conv": r.conv, "who": r.who, "events": len(r.events), "done": r.done, "ok": r.ok,
                         "error": r.error, "age_s": now - r.born,
                         "finished_age_s": None if r.finished is None else now - r.finished}
                        for r in reversed(list(self.runs.values()))][:50]
                return _json({"runs": rows})
            m = re.fullmatch(r"/workshop/run/([a-f0-9]{8,32})", route)
            if method == "GET" and m:
                run = self.runs.get(m.group(1))
                if run is None:
                    return _json({"error": "unknown_task"}, 404)
                after = max(0, int(q.get("after") or 0))
                return _json({"events": run.events[after:], "next": len(run.events), "done": run.done,
                              "ok": run.ok, "error": run.error})
            m = re.fullmatch(r"/workshop/cancel/([a-f0-9]{8,32})", route)
            if method == "POST" and m:
                run = self.runs.get(m.group(1))
                if run is None or run.done:
                    return _json({"ok": False})
                self.cancelled.append(run.task)
                run.finish(False, "stopped", self.clock())
                return _json({"ok": True})
        return _json({"error": "not_found"}, 404)

    def _start(self, body):
        task, conv, who = str(body.get("task") or ""), str(body.get("conv") or ""), str(body.get("who") or "")[:200]
        message, attach = str(body.get("message") or "").strip(), bool(body.get("attach"))
        if not re.fullmatch(r"[a-f0-9]{8,32}", task) or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", conv):
            return _json({"error": "bad_request"}, 400)
        if attach and task not in self.runs:
            return _json({"error": "unknown_task"}, 404)          # a relay that is only re-attaching can never start Claude
        if not attach and not message:
            return _json({"error": "bad_request", "message": "empty message"}, 400)
        if task in self.runs:
            return _json({"ok": True, "task": task, "done": self.runs[task].done})
        if any(not r.done for r in self.runs.values()):
            return _json({"error": "busy", "message": "Skippy is still working on the last thing"}, 409)
        s = self.script
        self.starts += 1
        self.runs[task] = BridgeRun(task, conv, who, self.clock(), s["total"], s["rate"], s["ok"], s["error"])
        return _json({"ok": True, "task": task, "done": False})


# ── the portal (portal/workshop.py + home.py, behind the home routes of portal/app.py) ────────────
class PortalSim:
    def __init__(self, clock, bridge):
        self.clock, self.bridge, self.up = clock, bridge, True
        self.pushes: list[tuple] = []          # (task, start, number of events, state) in arrival order
        self.violations: list[str] = []        # things that must never happen, noticed as they happen
        self.final_seen: dict[str, str] = {}
        self.boot()

    def boot(self):
        """A fresh instance: the in-memory task records and the job queue are gone (a deploy)."""
        self.home = home_mod.Home(Vm(TOKEN), clock=self.clock)
        self.workshop = workshop_mod.Workshop(self.home, clock=self.clock)

    def handle(self, method, path, headers, body):
        if not self.up:
            return _json({"error": "redeploying"}, 503)
        if not self.home.authorized(headers.get("Authorization")):
            return _json({"error": "unauthorized"}, 401)
        m = re.fullmatch(r"/home/workshop/([a-f0-9]{8,32})", path)
        if method == "POST" and m:                                  # mirrors app.py's home_workshop route
            task, b = m.group(1), json.loads(body or b"{}")
            try:
                reply = self.workshop.push(task, str(b.get("who") or ""), str(b.get("conv") or ""), b.get("start"),
                                           b.get("events"), str(b.get("state") or ""), b.get("error"))
            except ValueError:
                return _json({"error": "bad push"}, 400)
            self.pushes.append((task, b.get("start"), len(b.get("events") or []), b.get("state")))
            self._watch(task)
            return _json(reply)
        if method == "GET" and path == "/home/workshop":            # mirrors home_workshop_list
            return _json(self.workshop.listing())
        if method == "GET" and path == "/home/jobs":
            return _json(self.home.jobs_reply())
        m = re.fullmatch(r"/home/jobs/([a-f0-9]{8,32})", path)
        if method == "POST" and m:
            return _json({"ok": self.home.finish(m.group(1), json.loads(body or b"{}"))})
        return _json({"error": "not_found"}, 404)

    def _watch(self, task):
        """After every push: the record is a prefix of the bridge's run (no duplicate, no invented event), and a
        record that reached done/error never goes back to running."""
        with self.workshop._lock:
            rec = self.workshop._tasks.get(task)
            if rec is None:
                return
            events, state = list(rec["events"]), rec["state"]
        with self.bridge.lock:
            run = self.bridge.runs.get(task)
            truth = list(run.events) if run else None
        if truth is not None and events != truth[:len(events)]:
            self.violations.append(f"{task}: portal holds {len(events)} events that are not a prefix of the bridge's {len(truth)}")
        if state == "running" and self.final_seen.get(task):
            self.violations.append(f"{task}: went back from {self.final_seen[task]} to running")
        if state in ("done", "error"):
            self.final_seen[task] = state


# ── cooperative threads: a relay only runs when the test lets it ─────────────────────────────────
class ProcessKilled(Exception):
    """Raised in a relay thread whose reporter process was killed (Jobs._run ends the thread; it posts no outcome)."""


class Gate:
    """reporter.time.sleep for every thread but the test's own: block until released (or doomed: the process died)."""

    def __init__(self):
        self.main = threading.current_thread()
        self.cv = threading.Condition()
        self.entries: list[dict] = []

    def sleep(self, seconds):
        if threading.current_thread() is self.main:
            return
        entry = {"go": False, "doomed": False}
        with self.cv:
            self.entries.append(entry)
            while not entry["go"] and not entry["doomed"]:
                self.cv.wait(timeout=1)
            self.entries.remove(entry)
        if entry["doomed"]:
            raise ProcessKilled("the reporter was rebuilt")      # the thread's process died: it posts nothing more

    def release(self):
        with self.cv:
            for e in self.entries:
                e["go"] = True
            self.cv.notify_all()

    def doom_all(self):
        with self.cv:
            for e in self.entries:
                e["doomed"] = True
            self.cv.notify_all()

    def parked(self):
        """Threads waiting at the gate for a release (one that has been released but not yet woken doesn't count)."""
        return sum(1 for e in self.entries if not e["go"] and not e["doomed"])


class Browser:
    """The chat page following one task: it polls read() and keeps what it was shown, never going back."""

    def __init__(self, world, task):
        self.world, self.task, self.events, self.state, self.states = world, task, [], None, []

    def poll(self):
        if not self.world.portal.up:
            self.state = "unreachable"
        else:
            out = self.world.portal.workshop.read(OWNER, self.task, len(self.events))
            self.events += out["events"]
            self.state = out["state"]
        self.states.append(self.state)


class World:
    def __init__(self, monkeypatch):
        self.clock = Clock()
        self.bridge, self.errors = BridgeSim(self.clock), []
        self.portal = PortalSim(self.clock, self.bridge)
        self.fakes = [Fake(self._guard(self.bridge.handle)), Fake(self._guard(self.portal.handle))]
        monkeypatch.setattr(reporter, "BRIDGE", self.fakes[0].url)
        monkeypatch.setattr(reporter, "PORTAL", self.fakes[1].url)
        monkeypatch.setattr(reporter, "WORKSHOP_WAIT", 0)
        monkeypatch.setattr(reporter, "_now", self.clock)
        real_bridge = reporter._bridge
        monkeypatch.setattr(reporter, "_bridge",
                            lambda *a, **k: real_bridge(*a, **k) if self.bridge.up else (None, {}))      # a dead bridge refuses
        self.gate = Gate()
        monkeypatch.setattr(reporter.time, "sleep", self.gate.sleep)
        self.all_jobs: list = []
        self.fast_poll = True
        self.next_report = self.clock()
        self.browser: Browser | None = None
        self.start_reporter()
        self.report()                                    # the first report: the portal now knows the workshop is ready

    def _guard(self, handler):
        def run(method, path, headers, body):
            try:
                return handler(method, path, headers, body)
            except Exception as exc:   # noqa: BLE001 — a bug in a fake must fail the test, not hide as a dropped connection
                self.errors.append(f"{method} {path}: {exc!r}")
                return _json({"error": "fake broke"}, 500)
        return run

    def close(self):
        self.gate.doom_all()
        for f in self.fakes:
            f.close()

    # the reporter process
    def start_reporter(self):
        self.jobs = reporter.Jobs(TOKEN)
        self.all_jobs.append(self.jobs)
        self.sync = reporter.WorkshopSync(TOKEN, self.jobs)
        self.last_sync = -1e9
        self.next_report = self.clock()

    def restart_reporter(self):
        """The container was rebuilt: every relay thread dies, the new process knows nothing."""
        self.gate.doom_all()
        self.quiesce()
        self.start_reporter()

    # the portal, the bridge
    def restart_portal(self):
        self.portal.boot()

    def restart_bridge(self, persist, down_for=5):
        self.bridge.up = False
        self.run(down_for)
        self.bridge.restart(persist)
        self.bridge.up = True

    def report(self):
        """What main()'s loop does every 15 s: report in, take the jobs and controls the reply carries."""
        if self.portal.up:
            self.jobs.accept(self.portal.home.accept({"capabilities": {"workshop": {"ready": True, "model": "claude-opus-5-5"}}}))

    def send(self, message="make the tile green"):
        task = self.portal.workshop.send(OWNER, CONV, message)
        self.browser = Browser(self, task)
        return task

    # time
    def quiesce(self, timeout=15.0):
        """Wait until every relay thread is parked at the gate or finished: a thread is registered in `_running` from the
        moment it is accepted to the moment it ends, and it can only be parked inside the gate, so equal counts mean
        none is mid-call. A thread that parks wakes this at once; one that ends is noticed within 50 ms."""
        deadline = time.monotonic() + timeout
        with self.gate.cv:
            while True:
                running = sum(len(j._running) for j in self.all_jobs)
                if running == self.gate.parked():
                    return
                left = deadline - time.monotonic()
                assert left > 0, f"relay threads never parked: {running} running, {self.gate.parked()} parked; {self.errors}"
                self.gate.cv.wait(timeout=min(left, 0.05))

    def tick(self, dt=1.0, concurrent=False):
        self.clock.now += dt
        self.bridge.tick(dt)
        if self.browser:
            self.browser.poll()
        if self.clock() >= self.next_report:
            self.next_report = self.clock() + 15
            self.report()
        elif self.fast_poll and self.jobs.fast_until > reporter._now():     # the quick poll while someone is chatting
            try:
                self.jobs.accept(self.jobs._post("/home/jobs", None, method="GET"))
            except Exception:   # noqa: BLE001 — the portal is down: the next report carries them
                pass
        if not concurrent:
            self.quiesce()                                    # a relay that just started runs until it parks
        if self.clock() - self.last_sync >= self.sync.interval():
            self.last_sync = self.clock()
            if concurrent:
                self.gate.release()
                for _ in range(3):
                    self.sync.sync_once()
            else:
                self.sync.sync_once()
                self.quiesce()                                 # a relay it adopted runs until it parks
        self.gate.release()
        self.quiesce()

    def run(self, seconds, dt=1.0, **kw):
        end = self.clock() + seconds
        while self.clock() < end - 1e-9:
            self.tick(dt, **kw)

    def until(self, cond, within=30, dt=1.0, why="", **kw):
        end = self.clock() + within
        while not cond():
            assert self.clock() < end, f"not done within {within} s: {why or cond.__name__}\n{self.describe()}"
            self.tick(dt, **kw)

    # what to look at
    def record(self, task):
        return self.portal.workshop._tasks.get(task)

    def describe(self):
        recs = {t: (r["state"], len(r["events"]), r["error"]) for t, r in self.portal.workshop._tasks.items()}
        runs = {t: (len(r.events), "done" if r.done else "running", r.ok, r.error) for t, r in self.bridge.runs.items()}
        seen = self.browser and (self.browser.task, self.browser.state, len(self.browser.events))
        return f"portal records {recs}\nbridge runs {runs}\nbrowser {seen}\nrelays {[sorted(j._running) for j in self.all_jobs]}\nerrors {self.errors}"

    def delivered(self, task):
        """The portal holds every event of the run and its final state."""
        rec, run = self.record(task), self.bridge.runs.get(task)
        return bool(rec and run and run.done and rec["state"] == ("done" if run.ok else "error") and rec["events"] == run.events)

    def assert_delivered(self, task):
        rec, run = self.record(task), self.bridge.runs[task]
        assert rec is not None, "the portal has no record\n" + self.describe()
        assert rec["owner"] == OWNER
        assert rec["events"] == run.events, "the portal's events differ from the bridge's\n" + self.describe()
        assert rec["state"] == ("done" if run.ok else "error"), self.describe()
        assert not self.portal.violations, self.portal.violations
        assert not self.errors, self.errors
        read = self.portal.workshop.read(OWNER, task, 0)
        assert read["state"] in ("done", "error") and read["events"] == run.events       # what a browser that starts over sees
        if self.browser and self.browser.task == task:
            self.browser.poll()
            assert self.browser.state in ("done", "error") and self.browser.events == run.events, self.describe()


@pytest.fixture
def world(monkeypatch):
    w = World(monkeypatch)
    yield w
    w.close()


def finished(w, task):
    return lambda: w.delivered(task)


# ── the scenarios ─────────────────────────────────────────────────────────────────────────────────
def test_1_portal_restart_mid_run_relay_alive(world):
    w = world
    task = w.send()
    w.run(8)
    assert len(w.record(task)["events"]) >= 5                     # events were flowing
    w.restart_portal()
    w.until(finished(w, task), within=40)
    w.assert_delivered(task)


def test_2_reporter_restart_mid_run_the_queue_still_has_the_job(world):
    w = world
    task = w.send()
    w.run(8)
    w.restart_reporter()
    assert task in w.portal.home._jobs                            # the queue still holds the unanswered job
    w.until(finished(w, task), within=40)
    w.assert_delivered(task)


@pytest.mark.parametrize("portal_first", [True, False])
def test_3_portal_and_reporter_both_restart_mid_run_and_the_queue_is_lost(world, portal_first):
    """THE CAPTAIN'S CASE: a self-deploy pushes (the portal restarts), then rebuilds the reporter. Nothing is left
    that could push the rest: the job queue went with the old portal and the relay thread with the old reporter."""
    w = world
    task = w.send()
    w.run(8)
    for restart in ((w.restart_portal, w.restart_reporter) if portal_first else (w.restart_reporter, w.restart_portal)):
        restart()
    assert not w.portal.home._jobs and not w.portal.workshop._tasks
    w.until(finished(w, task), within=40)                         # the run ends at 20 s; the rest is the 30 s window
    w.assert_delivered(task)
    assert w.record(task)["owner"] == OWNER                        # the browser can read it: the record kept its owner


def test_4_portal_restart_after_the_run_finished_and_was_delivered(world):
    w = world
    task = w.send()
    w.until(lambda: w.browser.state == "done", within=60)
    assert w.browser.events == w.bridge.runs[task].events
    w.restart_portal()                                             # the browser is still polling that task
    w.until(finished(w, task), within=30)
    w.assert_delivered(task)
    assert w.browser.states[-1] == "done" and "unknown" in w.browser.states      # it saw the gap, and then the answer


def test_5_reporter_restart_after_the_run_finished_but_before_its_final_push(world):
    w = world
    task = w.send()
    w.run(14)
    w.portal.up = False                                            # the portal can't be reached as the run ends
    w.run(10)
    assert w.bridge.runs[task].done and w.record(task)["state"] == "running"
    w.restart_reporter()
    w.portal.up = True
    w.until(finished(w, task), within=30)
    w.assert_delivered(task)


def test_5b_the_same_with_a_fresh_portal_too(world):
    w = world
    task = w.send()
    w.run(14)
    w.portal.up = False
    w.run(10)
    w.restart_reporter()
    w.restart_portal()
    w.portal.up = True
    w.until(finished(w, task), within=30)
    w.assert_delivered(task)


def test_6_bridge_restart_mid_run_without_persistence_ends_in_an_explicit_error_not_stalled(world):
    w = world
    task = w.send()
    w.run(8)
    w.restart_bridge(persist=False)                                # the run is gone for good
    w.until(lambda: w.record(task)["state"] == "error", within=30, why="an explicit error")
    rec = w.record(task)
    assert rec["error"] and "lost" in rec["error"].lower() and "send" in rec["error"].lower()
    assert w.portal.workshop.read(OWNER, task, 0)["state"] == "error"
    w.run(130, dt=10)                                                  # and it stays an error: nothing turns it into "stalled"
    assert w.portal.workshop.read(OWNER, task, 0)["state"] == "error" and not w.portal.violations


@pytest.mark.parametrize("reconciler_on", [True, False])
def test_6b_the_same_when_the_reporter_restarted_with_it_and_the_queue_offers_the_job_again(world, reconciler_on):
    """The job is still in the queue (the portal never saw the end), so the new reporter is handed it again. The
    bridge has forgotten the run: starting it afresh would redo the work and splice a second run's events onto the
    first's. The portal already holds events for it, so the reporter only ever re-attaches. (With the reconciler
    off, the re-delivered job is the only thing that can end the chat, which is what this pins down.)"""
    w = world
    task = w.send()
    w.run(8)
    w.bridge.up = False
    w.restart_reporter()
    if not reconciler_on:
        w.sync.sync_once = lambda: {}
    w.run(2)
    w.bridge.restart(persist=False)
    w.bridge.up = True
    w.until(lambda: w.record(task)["state"] == "error", within=30, why="an explicit error")
    assert "lost" in w.record(task)["error"].lower()
    assert w.bridge.starts == 1 and not w.bridge.runs               # nothing was started a second time
    assert w.portal.workshop.read(OWNER, task, 0)["state"] == "error" and not w.portal.violations


@pytest.mark.parametrize("persist", [False, True])
def test_6c_the_relay_gives_up_on_a_long_bridge_outage_and_the_reconciler_ends_the_story(world, persist):
    """The relay stops after 20 misses and its job leaves the queue; nothing owns the run any more. The bridge then
    comes back with the run lost (no persistence) or closed (persistence): either way the chat gets an explicit end."""
    w = world
    task = w.send()
    w.run(8)
    w.bridge.up = False
    w.run(160, dt=5)
    assert not w.jobs._running and w.portal.home.jobs_reply()["jobs"] == []         # the relay is gone, the queue let go
    assert w.portal.workshop.read(OWNER, task, 0)["state"] == "stalled"             # and nobody has said anything since
    w.bridge.restart(persist=persist)
    w.bridge.up = True
    w.until(lambda: w.record(task)["state"] == "error", within=30, why="an explicit error")
    assert w.record(task)["error"] and ("restarted" in w.record(task)["error"] or "lost" in w.record(task)["error"])
    assert w.portal.workshop.read(OWNER, task, 0)["state"] == "error" and not w.portal.violations


def test_7_bridge_restart_after_the_run_finished_with_persistence_still_delivers(world):
    w = world
    task = w.send()
    w.run(8)
    w.portal.up = False                                            # the run ends while nobody can tell the portal
    w.run(15)
    assert w.bridge.runs[task].done and w.record(task)["state"] == "running"
    w.restart_bridge(persist=True)
    w.restart_reporter()
    w.restart_portal()                                             # and the portal is a fresh one when it comes back
    w.portal.up = True
    w.until(finished(w, task), within=30)
    w.assert_delivered(task)


def test_7b_a_run_the_bridge_closed_when_it_restarted_is_delivered_as_the_error_it_is(world):
    w = world
    task = w.send()
    w.run(8)
    w.portal.up = False
    w.restart_reporter()
    w.restart_bridge(persist=True)                                 # it was mid-way: closed with the restart error
    w.restart_portal()
    w.portal.up = True
    w.until(finished(w, task), within=30)
    w.assert_delivered(task)
    assert w.record(task)["state"] == "error" and "restarted" in w.record(task)["error"]


@pytest.mark.parametrize("fresh_portal", [False, True])
def test_8_portal_unreachable_for_several_minutes_then_back(world, fresh_portal):
    w = world
    task = w.send()
    w.run(8)
    w.portal.up = False
    w.run(300, dt=15)
    if fresh_portal:
        w.restart_portal()
    w.portal.up = True
    w.until(finished(w, task), within=30)
    w.assert_delivered(task)


def test_9_the_relay_and_the_reconciler_pushing_the_same_run_at_once_lose_and_repeat_nothing(world):
    w = world
    w.bridge.script.update(total=40)
    task = w.send()
    w.run(5)
    w.until(finished(w, task), within=60, concurrent=True)         # relay threads run while sync_once() runs, unsynchronised
    w.assert_delivered(task)


def test_9b_a_stale_running_push_landing_after_the_final_one_cannot_reopen_the_run(world, monkeypatch):
    """The exact interleaving: a relay has read the run (3 events, still going), the run finishes, the reconciler
    delivers everything and the final state, and only then does the relay's older push arrive."""
    w = world
    w.bridge.script.update(total=6, rate=0.5)
    task = w.send()
    w.run(7)                                                       # 3 of 6 events, not done
    run = w.bridge.runs[task]
    assert len(run.events) == 3 and not run.done
    relay = reporter.Relay(TOKEN, task, OWNER, CONV)
    real_push, raced = reporter.push_workshop, []

    def racing_push(*args, **kw):
        if not raced:
            raced.append(1)
            run.advance(100, w.clock())                            # the run finishes ...
            w.sync.sync_once()                                     # ... and the reconciler delivers it, before this push lands
        return real_push(*args, **kw)

    monkeypatch.setattr(reporter, "push_workshop", racing_push)
    relay.step(0)
    assert run.done and w.record(task)["state"] == "done" and len(w.record(task)["events"]) == 6
    assert not w.portal.violations
    while relay.step(0) not in ("final", "lost", "bridge", "portal"):
        pass
    w.assert_delivered(task)


def test_10_a_second_message_after_the_first_finished(world):
    w = world
    t1 = w.send("one")
    w.until(finished(w, t1), within=60)
    before = list(w.record(t1)["events"])
    w.bridge.script.update(total=6)
    w.fast_poll = False                                            # the job waits for the next report, as it may
    w.next_report = w.clock() + 15
    t2 = w.send("two")
    w.run(10)
    assert w.record(t2)["state"] == "queued"                       # the reconciler must not call a waiting job lost
    assert not any(p[0] == t2 for p in w.portal.pushes)
    w.until(finished(w, t2), within=40)
    w.assert_delivered(t2)
    assert w.record(t1)["events"] == before and w.record(t1)["state"] == "done"
    assert w.bridge.starts == 2


def test_11_attach_for_an_unknown_task_never_starts_a_run(world, monkeypatch):
    monkeypatch.setattr(reporter, "WORKSHOP_MAX_S", 1)      # a relay that wrongly started the run would poll it for a second, not 3 hours
    w = world
    job = {"id": "abcd1234ef", "kind": "workshop", "conv": CONV, "who": OWNER, "message": "(re-attach)", "attach": True}
    out = reporter.workshop(job, TOKEN)
    assert "error" in out
    assert w.bridge.starts == 0 and not w.bridge.runs
    rec = w.record("abcd1234ef")
    assert rec is not None and rec["state"] == "error" and "lost" in rec["error"].lower()      # the chat ends, plainly


def test_12_runs_without_who_and_runs_finished_long_ago_are_left_alone(world):
    w = world
    b = w.bridge
    with b.lock:
        b.runs["aaaa1111"] = BridgeRun("aaaa1111", CONV, "", w.clock(), 5, 1, True, None)           # no owner: nobody could read it
        old = BridgeRun("bbbb2222", CONV, OWNER, w.clock() - 7200, 5, 1, True, None)
        old.advance(10, w.clock() - 7100)                                                           # finished 2 hours ago
        b.runs["bbbb2222"] = old
        mine = BridgeRun("cccc3333", CONV, OWNER, w.clock() - 600, 5, 1, True, None)
        mine.advance(10, w.clock() - 500)                                                           # finished 8 minutes ago
        b.runs["cccc3333"] = mine
    out = w.sync.sync_once()
    assert w.record("aaaa1111") is None and w.record("bbbb2222") is None
    assert w.delivered("cccc3333")                                  # the recent one is delivered, whole
    assert not w.jobs._running                                      # and nothing adopted (none is running)
    assert out["pushed"] == ["cccc3333"] and out["adopted"] == [] and out["lost"] == []


def test_13_the_portal_asks_for_earlier_events_than_the_reporter_thought(world):
    w = world
    task = w.send()
    w.run(10)
    with w.portal.workshop._lock:
        w.record(task)["events"] = w.record(task)["events"][:3]    # the portal holds less than it was sent
    mark = len(w.portal.pushes)
    w.until(finished(w, task), within=40)
    w.assert_delivered(task)
    assert any(p[1] is not None and p[1] <= 3 for p in w.portal.pushes[mark:] if p[2]), "nothing was re-sent from the portal's `have`"


@pytest.mark.parametrize("relay_alive", [True, False])
def test_15_a_run_that_says_nothing_for_minutes_is_never_called_stalled(world, relay_alive):
    """Claude thinking (or a long build) emits no event for minutes. The portal calls a task stalled after STALL_S without
    a push, so every round must push even when nothing is new: from the relay, and, with no relay, from the reconciler."""
    w = world
    w.bridge.script.update(total=3, rate=1 / 150)                    # one event per 150 s, longer than STALL_S
    task = w.send()
    w.run(10, dt=10)
    if not relay_alive:
        w.restart_reporter()
        w.portal.home._jobs.clear()                                   # nothing in the queue ...
        w.jobs.accept = lambda reply: None                             # ... and nothing may adopt the run: only the reconciler's own pushes count
    w.until(finished(w, task), within=600, dt=10)
    w.assert_delivered(task)
    assert "stalled" not in w.browser.states and w.bridge.runs[task].finished - w.bridge.runs[task].born > 3 * workshop_mod.STALL_S


class FakeTime:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_14_the_self_watchdog_fires_only_after_watchdog_s_of_silence():
    t, exits = FakeTime(), []
    dog = reporter.Watchdog(clock=t, exit_fn=exits.append)
    limit = reporter.WATCHDOG_S
    assert limit == 900
    for now in (0, 60, 600, limit - 1, limit):
        t.now = now
        assert dog.check() is False                                # at most 900 s since the process began
    t.now = limit + 1
    assert dog.check() is True and exits == [3]                    # silent since the start, and up long enough
    exits.clear()
    t.now = 0                                                     # a process whose reports keep succeeding never trips it
    dog = reporter.Watchdog(clock=t, exit_fn=exits.append)
    for now in range(0, 5000, 60):
        t.now = now
        dog.ok()
        assert dog.check() is False
    last_ok = 4980                                                 # the last report that got through
    t.now = last_ok + limit
    assert dog.check() is False
    t.now = last_ok + limit + 1
    assert dog.check() is True and exits == [3]                    # and does the moment they stop for WATCHDOG_S


def test_14b_the_watchdog_thread_checks_every_minute(monkeypatch):
    t, exits, sleeps = FakeTime(), [], []
    dog = reporter.Watchdog(clock=t, exit_fn=exits.append)

    def fake_sleep(s):
        sleeps.append(s)
        t.now += s
        if exits:
            raise KeyboardInterrupt                                # leave the endless loop

    monkeypatch.setattr(reporter.time, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        dog.run_forever()
    assert set(sleeps) == {60} and exits == [3] and t.now > reporter.WATCHDOG_S
