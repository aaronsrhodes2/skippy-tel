"""The report is also the heartbeat: a slow or odd service must not delay it or kill it.

The portal calls skippy-pc silent after 60 s without a report (portal/home.py STALE_AFTER), drops every capability and
refuses Send, the private Skippy's chat included. gather() used to ask its services one after another (about 45 s when
flask-api or Ollama was slow), the POST, a 20 s telemetry pull and the sleep came after that, and an unexpected JSON shape
from one service (a list where a dict was read) raised out of gather() and killed the process. These pin the replacement:
probes side by side under one budget, a late or failing probe reads as missing (never stale), the telemetry on its own
thread, the sleep net of the work, and the report's shape unchanged."""
import json
import threading
import time
import types

import pytest

import reporter
from test_music import Fake, _json

TOKEN = "home-token"
SHAPE = {"status", "health", "services", "music_session", "bilby", "setlist", "up_next", "capabilities",
         "site_activity", "local", "doctor"}                          # what the portal reads: it must not change with how it is collected


@pytest.fixture(autouse=True)
def nothing_configured(monkeypatch):
    for name in ("BRIDGE", "MUSIC", "MAC", "GATE", "GATE_PUBLIC", "VIDEO_GATE", "VIDEO_GATE_PUBLIC"):
        monkeypatch.setattr(reporter, name, "")
    monkeypatch.setattr(reporter, "_probe_failures", {})
    monkeypatch.setattr(reporter.doctor_checks, "checks", lambda: {})    # the doctor's tests have their own (test_doctor_checks.py)


def healthy_health():
    return {"status": "ok", "spec_version": "1", "bridge": True, "hung_tiers": [],
            "models": {"ollama": {"available": True, "default": "qwen3:8b", "default_present": True}, "tiers": {"claude": True}}}


def fetch_from(routes, delay=0.0, hold=None):
    """A stand-in for reporter.fetch: `routes` maps a URL suffix to its reply (or a callable); every call takes `delay` s;
    `hold` maps a suffix to an Event that call waits on (a wedged service)."""
    hold = hold or {}

    def fake(url, method="GET", data=None, timeout=4.0, raw=False):
        if delay:
            time.sleep(delay)
        for suffix, reply in routes.items():
            if url.endswith(suffix):
                if suffix in hold:
                    hold[suffix].wait(10)
                return reply() if callable(reply) else reply
        return None
    return fake


def test_probes_run_side_by_side_not_one_after_another(monkeypatch):
    """Eight calls of 0.25 s each: one after another that is 2 s; side by side it is about one call."""
    monkeypatch.setattr(reporter, "GATHER_BUDGET_S", 5)
    monkeypatch.setattr(reporter, "fetch", fetch_from({"/status.json": {"services": []}, "/health": healthy_health(),
                                                       "/services": {"services": [{"id": "a", "name": "A", "available": True}]}},
                                                      delay=0.25))
    began = time.monotonic()
    report = reporter.gather()
    assert time.monotonic() - began < 1.2
    assert set(report) == SHAPE
    assert report["status"] == {"services": []} and report["health"]["status"] == "ok"
    assert report["services"] == [{"id": "a", "name": "A", "available": True, "status_detail": None, "planned": None}]


def test_a_wedged_service_is_missing_from_the_report_and_cannot_hold_it_up(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(reporter, "GATHER_BUDGET_S", 0.4)
    monkeypatch.setattr(reporter, "fetch", fetch_from({"/status.json": {"services": [{"name": "x", "ok": False}]},
                                                       "/health": healthy_health(), "/music/session": {"mode": "dj", "status": "playing"}},
                                                      hold={"/status.json": release}))
    began = time.monotonic()
    try:
        report = reporter.gather()
        took = time.monotonic() - began
    finally:
        release.set()
    assert took < 2.0                                          # the budget, not the wedged call's own 10 s
    assert report["status"] is None                            # missing, the way a down service reads: never a stale answer
    assert report["health"]["status"] == "ok" and report["music_session"]["mode"] == "dj"   # the rest still made it
    assert set(report) == SHAPE


def test_a_probe_that_raises_degrades_to_missing_and_is_said_once(monkeypatch, capsys):
    """A list where a dict is read used to raise out of gather() (outside any try) and kill the process; Docker restarted
    it and it died again for as long as the shape lasted."""
    monkeypatch.setattr(reporter, "GATHER_BUDGET_S", 3)
    monkeypatch.setattr(reporter, "fetch", fetch_from({"/health": [1, 2], "/services": [1], "/bilby/status": [1],
                                                       "/music/session": ["x"], "/status.json": {"services": []}}))
    for _ in range(3):
        report = reporter.gather()                              # nothing raises
    assert report["health"] is None and report["bilby"] is None and report["services"] == [] and report["music_session"] is None
    assert report["status"] == {"services": []}                 # the one that was fine is unaffected
    assert set(report) == SHAPE
    out = capsys.readouterr().out
    assert out.count("the health probe failed") == 1 and out.count("the bilby probe failed") == 1     # once, not every report


def test_a_late_capability_reads_as_not_ready_and_the_gate_keeps_its_url(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(reporter, "GATHER_BUDGET_S", 0.3)
    monkeypatch.setattr(reporter, "GATE_PUBLIC", "https://gate.example")
    monkeypatch.setattr(reporter, "VIDEO_GATE_PUBLIC", "https://gate.example:8443")
    monkeypatch.setattr(reporter, "fetch", fetch_from({}))
    monkeypatch.setattr(reporter, "workshop_capability", lambda: (release.wait(10), {"ready": True, "model": "m"})[1])
    monkeypatch.setattr(reporter, "video_gate_capability", lambda: (release.wait(10), {"ready": True, "url": "x"})[1])
    monkeypatch.setattr(reporter, "_up", lambda url: False)
    try:
        caps = reporter.gather()["capabilities"]
    finally:
        release.set()
    assert caps["workshop"] == {"ready": False}                 # not the last answer, not "ready": the safe one
    assert caps["make_music"] == {"ready": False} and caps["music_gate"] == {"ready": False, "url": "https://gate.example"}
    assert caps["video_gate"] == {"ready": False, "url": "https://gate.example:8443"}      # late: not ready, but it keeps its address


def test_the_macs_setlist_and_next_track_are_fetched_only_when_traktor_is_the_source(monkeypatch):
    asked = []
    monkeypatch.setattr(reporter, "MAC", "http://mac.example")
    monkeypatch.setattr(reporter, "GATHER_BUDGET_S", 3)
    state = {"source": "traktor"}

    def fake(url, method="GET", data=None, timeout=4.0, raw=False):
        asked.append(url)
        if url.endswith("/bilby/status"):
            return {"source": state["source"], "loaded": {}, "now_playing": None}
        if url.endswith("/api/setlist"):
            return {"setlist": list(range(30))}
        if url.endswith("/bilby/next"):
            return {"ok": True, "track": {"title": "T"}}
        return None
    monkeypatch.setattr(reporter, "fetch", fake)
    report = reporter.gather()
    assert report["setlist"] == list(range(10, 30)) and report["up_next"] == {"title": "T"}      # the last 20, as before
    assert report["bilby"]["source"] == "traktor"
    asked.clear()
    state["source"] = "local"
    report = reporter.gather()
    assert report["setlist"] == [] and report["up_next"] is None
    assert not [u for u in asked if u.startswith("http://mac.example")]


# ── main(): the heartbeat's cadence and what runs beside it ────────────────────────────────────────────────────
class _Stop(Exception):
    def __init__(self, slept):
        self.slept = slept


def run_main_once(monkeypatch, gather, interval=15.0):
    """main() through one report; returns (what it POSTed to /home/report, how long it then slept, the targets it threaded)."""
    posted = []

    def handle(method, path, headers, body):
        posted.append((path, json.loads(body or b"{}")))
        return _json({"commands": [], "jobs": []})
    portal = Fake(handle)
    monkeypatch.setenv("SKIPPY_HOME_TOKEN", TOKEN)
    monkeypatch.setattr(reporter, "PORTAL", portal.url)
    monkeypatch.setattr(reporter, "INTERVAL", interval)
    monkeypatch.setattr(reporter, "gather", gather)
    targets = []
    monkeypatch.setattr(reporter, "threading", types.SimpleNamespace(
        Lock=threading.Lock, Thread=lambda target, daemon=True: types.SimpleNamespace(start=lambda: targets.append(target))))

    def stop(seconds):
        raise _Stop(seconds)
    monkeypatch.setattr(reporter.time, "sleep", stop)
    try:
        with pytest.raises(_Stop) as stopped:
            reporter.main()
    finally:
        portal.close()
    return posted, stopped.value.slept, targets


def test_the_telemetry_has_its_own_thread_and_the_report_loop_never_waits_for_it(monkeypatch):
    def refuse(self):
        raise AssertionError("main()'s loop collected telemetry itself: a 20 s pull would delay the next heartbeat")
    monkeypatch.setattr(reporter.SiteTelemetry, "collect", refuse)
    posted, _, targets = run_main_once(monkeypatch, lambda: {"status": None})
    assert posted and posted[0][0] == "/home/report"
    assert "SiteTelemetry.run_forever" in [getattr(t, "__qualname__", "") for t in targets]


def test_the_telemetry_thread_survives_a_failing_collection_and_goes_on(monkeypatch, capsys):
    site = reporter.SiteTelemetry(TOKEN)
    calls = []

    def collect():
        calls.append(1)
        raise RuntimeError("weird")
    site.collect = collect

    def sleep(seconds):
        if len(calls) >= 3:
            raise _Stop(seconds)
    monkeypatch.setattr(reporter.time, "sleep", sleep)
    monkeypatch.setattr(reporter, "INTERVAL", 15.0)
    with pytest.raises(_Stop) as stopped:
        site.run_forever()
    assert len(calls) == 3 and stopped.value.slept == 15.0
    assert capsys.readouterr().out.count("telemetry collection failed") == 3


def test_the_loop_sleeps_the_interval_minus_the_work_it_did(monkeypatch):
    """The work (probes, art, the POST) counts against the wait, so a slow round doesn't push the next report later still."""
    def slow_gather():
        threading.Event().wait(0.4)                            # (time.sleep is patched, for every module, to end the loop)
        return {"status": None}
    _, slept, _ = run_main_once(monkeypatch, slow_gather, interval=1.5)
    assert 1.0 <= slept < 1.3                                  # 1.5 minus the 0.4 s gather (and the POST), never less than 1 s
    _, slept, _ = run_main_once(monkeypatch, slow_gather, interval=0.2)
    assert slept == 1.0                                         # a round slower than the interval doesn't spin


def test_a_gather_that_blows_up_still_sends_a_heartbeat(monkeypatch, capsys):
    def boom():
        raise RuntimeError("assembling went wrong")
    posted, slept, _ = run_main_once(monkeypatch, boom)
    assert posted == [("/home/report", {"results": []})]       # the portal still hears from skippy-pc, with nothing else to say
    assert "gathering the report failed" in capsys.readouterr().out and slept > 1
