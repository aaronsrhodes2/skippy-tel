"""The Audiobooks app: the reporter tells the portal where the audiobook gate is and whether it is worth a tile.

Like the video gate, its /health answers while the container runs, so "ready" is the gate's own `ready` field (there are books to serve),
and the compose file gives the reporter both the inside address and the Funnel path (:10000/books, a mount on the voice gate's port)."""
from pathlib import Path

import pytest

import reporter

PUBLIC = "https://skippy-pc.tailab2cc7.ts.net:10000/books"


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    monkeypatch.setattr(reporter, "BOOK_GATE", "http://audiobook-gate:8080")
    monkeypatch.setattr(reporter, "BOOK_GATE_PUBLIC", PUBLIC)
    for name in ("BRIDGE", "MUSIC", "GATE", "GATE_PUBLIC", "VIDEO_GATE", "VIDEO_GATE_PUBLIC"):
        monkeypatch.setattr(reporter, name, "")


def answer(monkeypatch, reply):
    asked = []

    def fake(url, method="GET", data=None, timeout=4.0, raw=False):
        asked.append(url)
        return reply
    monkeypatch.setattr(reporter, "fetch", fake)
    return asked


def test_ready_when_the_gate_says_ready(monkeypatch):
    asked = answer(monkeypatch, {"ready": True, "books": 394})
    assert reporter.capabilities()["book_gate"] == {"ready": True, "url": PUBLIC}
    assert "http://audiobook-gate:8080/health" in asked


@pytest.mark.parametrize("reply", [{"ready": False, "books": 0}, {"ready": "yes"}, {"books": 3}, None, ["ready"]])
def test_not_ready_otherwise_and_the_address_is_still_sent(monkeypatch, reply):
    answer(monkeypatch, reply)
    assert reporter.capabilities()["book_gate"] == {"ready": False, "url": PUBLIC}


@pytest.mark.parametrize("inside, outside", [("", PUBLIC), ("http://audiobook-gate:8080", "")])
def test_not_configured_means_not_ready_and_nothing_is_asked(monkeypatch, inside, outside):
    monkeypatch.setattr(reporter, "BOOK_GATE", inside)
    monkeypatch.setattr(reporter, "BOOK_GATE_PUBLIC", outside)
    asked = answer(monkeypatch, {"ready": True})
    assert reporter.book_gate_capability() == {"ready": False, "url": outside}
    assert asked == []


def test_a_late_probe_reads_as_not_ready_with_the_address():
    assert reporter._capability_down("book_gate") == {"ready": False, "url": PUBLIC}


def test_the_portal_knows_the_capability_and_the_compose_file_wires_it():
    root = Path(__file__).resolve().parents[3]
    assert '"book_gate"' in (root / "portal" / "home.py").read_text(encoding="utf-8")
    text = (root / "docker-compose.override.yml").read_text(encoding="utf-8")
    assert "BOOK_GATE_URL: http://audiobook-gate:8080" in text and f"BOOK_GATE_PUBLIC: {PUBLIC}" in text
    block = text[text.index("  audiobook-gate:"):text.index("  # Pushes status")]
    assert "D:/Audiobooks:/audiobooks:ro" in block                                  # the library is read-only
    assert "./state:/state:ro" in block and "./state/audiobooks-inbox:/inbox" in block      # the one thing it writes is the inbox
    assert "127.0.0.1:3009:8080" in block                                           # localhost only: Funnel is the way in
    assert '("10000", "3009", "/books")' in (root / "services" / "claude-bridge" / "host_ops.py").read_text(encoding="utf-8")
