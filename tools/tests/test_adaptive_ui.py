"""skippy-pc's half of the portal's nightly tidy (tools/adaptive_ui.py): taps, days on screen, the surfaces' days in use and which
tap follows which, summed from the telemetry SQLite (portal/adaptive.py decides from it)."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adaptive_ui as U  # noqa: E402

NOW = 1_800_000_000.0


def db(rows):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE site_events (ts REAL, who TEXT, kind TEXT, name TEXT)")
    con.executemany("INSERT INTO site_events VALUES (?, ?, ?, ?)", rows)
    return con


def test_taps_seen_days_surfaces_and_pairs():
    d = 86400
    con = db([
        (NOW - 40 * d, "a", "page", "/"),                                   # outside the window: only the span counts it
        (NOW - 2 * d, "a", "page", "/"), (NOW - 1 * d, "a", "page", "/"), (NOW - 1 * d, "a", "page", "/me"),
        (NOW - 2 * d, "a", "ui", "seen:chat:tools"), (NOW - 1 * d, "a", "ui", "seen:chat:tools"),
        (NOW - 2 * d, "a", "ui", "chat:tools"), (NOW - 2 * d + 5, "a", "ui", "tray:music"),
        (NOW - 1 * d, "a", "ui", "chat:tools"), (NOW - 1 * d + 60, "a", "ui", "tray:music"),   # a minute later: not "following"
        (NOW - 1 * d + 61, "b", "ui", "tray:music"),
    ])
    s = U.summarise(con, now=NOW)
    assert s["span_days"] == 40.0 and s["surface_days"] == {"chat": 2, "launcher": 2, "me": 1}
    assert s["controls"]["chat:tools"] == {"taps": 2, "seen_days": 2, "last_days": 1.0}
    assert s["controls"]["tray:music"]["taps"] == 3
    assert s["pairs"] == [["chat:tools", "tray:music", 1]]
    assert "seen:chat:tools" not in s["controls"]


def test_the_overnight_words():
    assert U.words([]) == ""
    assert U.words([{"kind": "retire", "label": "Chat key: Attach", "why": "1 tap in 30 days"}]) == "retired Chat key: Attach (1 tap in 30 days)"
