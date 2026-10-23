"""The doctor's tests (doctor_checks.py): a service that answers wrongly, too slowly or not at all is a failed test, with its reason."""
import socket

import doctor_checks as dc
import reporter


def test_a_good_answer_passes_and_a_wrong_or_late_one_fails_with_its_reason():
    ok = lambda b: isinstance(b, dict) and b.get("status") == "ok" or "its /health isn't ok"     # noqa: E731
    assert dc.run_one("x", "u", ok, get=lambda url: (200, {"status": "ok"}))["ok"] is True
    bad = dc.run_one("x", "u", ok, get=lambda url: (200, {"status": "degraded"}))
    assert bad == {"ok": False, "ms": bad["ms"], "why": "its /health isn't ok"}
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
