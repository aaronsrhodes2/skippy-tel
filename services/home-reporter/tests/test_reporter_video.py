"""Nagatha's Video Service: the reporter tells the portal where the video gate is and whether it is worth a tile.

The gate's /health answers 200 whenever the container runs (it doubles as liveness), so "ready" must come from its own
`ready` field (a catalog exists AND Jellyfin answers), not from the status code like the music gate's probe."""
import pytest

import reporter

PUBLIC = "https://skippy-pc.tailab2cc7.ts.net:8443"


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(reporter, "VIDEO_GATE", "http://video-gate:8080")
    monkeypatch.setattr(reporter, "VIDEO_GATE_PUBLIC", PUBLIC)
    for name in ("BRIDGE", "MUSIC", "GATE", "GATE_PUBLIC"):        # the other probes ask nothing real
        monkeypatch.setattr(reporter, name, "")


def answer(monkeypatch, reply):
    asked = []

    def fake(url, method="GET", data=None, timeout=4.0, raw=False):
        asked.append(url)
        return reply
    monkeypatch.setattr(reporter, "fetch", fake)
    return asked


def test_ready_when_the_gate_says_ready(monkeypatch):
    asked = answer(monkeypatch, {"ok": True, "ready": True, "jellyfin": True, "items": 285})
    assert reporter.capabilities()["video_gate"] == {"ready": True, "url": PUBLIC}
    assert asked == ["http://video-gate:8080/health"]               # the inside address, over the compose network


@pytest.mark.parametrize("reply", [
    {"ok": True, "ready": False, "jellyfin": False, "items": 0},     # alive, but Jellyfin is down and no catalog yet
    {"ok": True, "ready": "yes"},                                   # only a real true counts
    {"ok": True},
    None,                                                           # the container isn't answering
    ["ready"],
])
def test_not_ready_otherwise_and_the_address_is_still_sent(monkeypatch, reply):
    answer(monkeypatch, reply)
    assert reporter.capabilities()["video_gate"] == {"ready": False, "url": PUBLIC}


@pytest.mark.parametrize("inside, outside", [("", PUBLIC), ("http://video-gate:8080", "")])
def test_not_configured_means_not_ready_and_nothing_is_asked(monkeypatch, inside, outside):
    monkeypatch.setattr(reporter, "VIDEO_GATE", inside)
    monkeypatch.setattr(reporter, "VIDEO_GATE_PUBLIC", outside)
    asked = answer(monkeypatch, {"ready": True})
    assert reporter.video_gate_capability() == {"ready": False, "url": outside}
    assert asked == []


def test_compose_gives_the_reporter_both_addresses():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[3] / "docker-compose.override.yml").read_text(encoding="utf-8")
    assert "VIDEO_GATE_URL: http://video-gate:8080" in text
    assert f"VIDEO_GATE_PUBLIC: {PUBLIC}" in text
