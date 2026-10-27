"""The doctor's tests (doctor_checks.py): a service that answers wrongly, too slowly or not at all is a failed test, with its reason."""
import socket

import doctor_checks as dc
import reporter


def test_a_good_answer_passes_and_a_wrong_or_late_one_fails_with_its_reason():
    ok = lambda b: isinstance(b, dict) and b.get("status") == "ok" or "its /health isn't ok"     # noqa: E731
    assert dc.run_one("x", "u", ok, get=lambda url: (200, {"status": "ok"}))["ok"] is True
    bad = dc.run_one("x", "u", ok, get=lambda url: (200, {"status": "degraded"}))
    assert bad == {"ok": False, "ms": bad["ms"], "why": "its /health isn't ok", "reachable": True}
    assert dc.run_one("x", "u", ok, get=lambda url: (503, None))["why"] == "HTTP 503"

    def hangs(url):
        raise socket.timeout("timed out")
    assert dc.run_one("x", "u", ok, get=hangs)["why"] == "no answer in time"

    def refused(url):
        raise ConnectionRefusedError()
    assert dc.run_one("x", "u", ok, get=refused)["why"] == "no answer (ConnectionRefusedError)"


def test_only_services_this_reporter_can_reach_are_tested(monkeypatch):
    for v in ("MUSIC_URL", "IMAGE_URL", "MENTAT_URL", "MUSIC_GATE_URL", "VIDEO_GATE_URL", "VOICE_GATE_URL", "CLAUDE_BRIDGE_URL"):
        monkeypatch.delenv(v, raising=False)
    assert set(dc.checks()) == {"flask-api", "ollama", "whisper"}
    monkeypatch.setenv("VOICE_GATE_URL", "http://voice-gate:8080")
    monkeypatch.setenv("CLAUDE_BRIDGE_URL", "http://host.docker.internal:9337")
    names = dc.checks()
    assert {"voice-gate", "bridge"} <= set(names) and names["voice-gate"][0] == "http://voice-gate:8080/health"
    assert names["voice-gate"][1]({"ready": False}) == "Whisper isn't answering behind it"


def test_the_deep_test_makes_ollama_write_and_unload():
    sent = {}

    def get(url, timeout=5, data=None):
        sent.update(url=url, timeout=timeout, data=data)
        return 200, {"response": "OK"}
    got = dc.deep(get=get)
    assert got["ollama"]["ok"] and sent["url"].endswith("/api/generate") and sent["data"]["keep_alive"] == 0
    assert sent["data"]["options"]["num_predict"] == 5 and sent["timeout"] >= 60


def test_every_report_carries_the_tests_and_a_late_one_reads_as_failed(monkeypatch):
    monkeypatch.setattr(dc, "checks", lambda: {"flask-api": ("u", lambda b: True), "ollama": ("u", lambda b: True)})
    monkeypatch.setattr(dc, "run_one", lambda name, url, ok: {"ok": True, "ms": 3, "why": ""} if name == "flask-api" else None)
    monkeypatch.setattr(reporter, "fetch", lambda *a, **k: None)
    monkeypatch.setattr(reporter, "_capability_probes", lambda: {})
    got = reporter.gather()["doctor"]
    assert got["flask-api"]["ok"] is True and got["ollama"] == {"ok": False, "ms": got["ollama"]["ms"], "why": "no answer in time"}


# ── the readiness contract (PLATINUM section 24): a 200 is not "fine" ───────────────────────────────────────────
def _body(ready=True, running=0, stuck=0, detail=""):
    return {"ready": ready, "accepting": ready, "jobs": {"running": running, "queued": 0, "oldest_s": None}, "stuck_s": stuck, "detail": detail}


def test_the_contract_tells_ready_busy_hung_and_not_ready_apart():
    assert dc.contract(_body())["state"] == "ready"
    assert dc.contract(_body(running=2, stuck=5))["state"] == "busy"
    assert dc.contract(_body(running=1, stuck=dc.HUNG_S))["state"] == "hung"
    assert dc.contract(_body(ready=False, detail="Whisper isn't answering"))["state"] == "not-ready"
    assert dc.contract({"ok": True, "ready": True}) is None and dc.contract("ok") is None and dc.contract(None) is None      # a bare answer is not the contract


def test_the_verdict_is_true_only_for_ready_and_busy_and_otherwise_says_why_in_the_services_words():
    assert dc.ready_verdict(_body()) is True and dc.ready_verdict(_body(running=3, stuck=10)) is True
    assert dc.ready_verdict(_body(ready=False, detail="the router model isn't offered")) == "the router model isn't offered"
    assert "hung: 1 job running, no progress for 300 s" == dc.ready_verdict(_body(running=1, stuck=300))
    assert dc.ready_verdict("old plain answer") is True and dc.ready_verdict("x", lambda b: "legacy says no") == "legacy says no"


def test_a_503_that_says_why_is_an_answer_and_a_hung_service_is_a_failed_test_with_its_state():
    ok = lambda b: dc.ready_verdict(b)                                                    # noqa: E731
    down = dc.run_one("mentat-home", "u", ok, get=lambda url: (503, _body(ready=False, detail="waiting on llm (skippy-core doesn't offer the chat model)")))
    assert down["ok"] is False and down["state"] == "not-ready" and "chat model" in down["why"]
    hung = dc.run_one("music-gate", "u", ok, get=lambda url: (200, _body(running=1, stuck=400)))
    assert hung["ok"] is False and hung["state"] == "hung" and hung["jobs"] == 1 and hung["stuck_s"] == 400
    busy = dc.run_one("voice-gate", "u", ok, get=lambda url: (200, _body(running=2, stuck=3)))
    assert busy["ok"] is True and busy["state"] == "busy"
    plain = dc.run_one("whisper", "u", lambda b: True, get=lambda url: (200, None))
    assert plain == {"ok": True, "ms": plain["ms"], "why": "", "reachable": True} and "state" not in plain   # answers, but never said it is ready
    assert dc.run_one("x", "u", ok, get=lambda url: (_ for _ in ()).throw(OSError("refused")))["reachable"] is False   # no answer at all
