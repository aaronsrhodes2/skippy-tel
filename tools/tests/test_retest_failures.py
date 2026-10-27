"""tools/retest_failures.py: following a run that is already going (--attach), so a driver that dies does not orphan its run.

The bridge is a function (`call` returns what GET /workshop/run/<task> and GET /workshop/runs say), so a long run plays out in
milliseconds. What is checked is the shape of the result: the same one a run the tool started itself produces.
"""
from __future__ import annotations

import json

import pytest

import retest_failures as rf


class FakeBridge:
    """GET /workshop/runs lists the run with an age; GET /workshop/run/<task>?after=N&wait=W hands out its events, a few at a time."""

    def __init__(self, task: str, events: list[dict], age_s: float = 125.0, ok: bool = True, error: str | None = None, per_poll: int = 2):
        self.task, self.events, self.age_s, self.ok, self.error, self.per_poll = task, events, age_s, ok, error, per_poll
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, path, body, tok, timeout=60):
        self.calls.append((method, path))
        assert tok == "token"
        if path == "/workshop/runs":
            return 200, {"runs": [{"task": "other", "age_s": 5.0}, {"task": self.task, "age_s": self.age_s, "project": "p", "conv": "c"}]}
        assert path.startswith(f"/workshop/run/{self.task}?after=")
        after = int(path.split("after=")[1].split("&")[0])
        chunk = self.events[after:after + self.per_poll]
        nxt = after + len(chunk)
        done = nxt >= len(self.events)
        return 200, {"events": chunk, "next": nxt, "done": done, "ok": self.ok if done else None, "error": self.error if done else None}


EVENTS = [{"t": "tool", "name": "Read", "detail": "SPEC.md"}, {"t": "text", "text": "Reading."}, {"t": "tool", "name": "Write", "detail": "gen.mjs"},
          {"t": "text", "text": "Done: gen.mjs written."}]
CASE = {"name": "T09", "message": "Turn 1.", "project": "f-shifting-prophesy-purple", "conv": "shifting-prophesy-purple-T09"}


def test_attaching_replays_the_runs_events_from_the_start_and_returns_the_usual_result(monkeypatch):
    bridge = FakeBridge("abc123", EVENTS)
    monkeypatch.setattr(rf, "call", bridge)
    r = rf.attach_case(CASE, "abc123", "core", "token", max_min=0)
    assert r["ok"] is True and r["done"] is True and r["task"] == "abc123"
    assert r["name"] == "T09" and r["conv"] == "shifting-prophesy-purple-T09" and r["brain"] == "core"
    assert r["tools"] == ["Read", "Write"] and r["tool_calls"] == 2
    assert r["answer"] == "Reading.\nDone: gen.mjs written."
    assert [e["t"] for e in r["events"]] == ["tool", "text", "tool", "text"]
    assert r["seconds"] >= 125                                                         # the run's age when we joined, not time since we joined
    assert [p for m, p in bridge.calls if p.startswith("/workshop/run/")][0].startswith("/workshop/run/abc123?after=0")


def test_a_run_that_failed_is_reported_as_failed_with_its_reason(monkeypatch):
    monkeypatch.setattr(rf, "call", FakeBridge("abc123", EVENTS, ok=False, error="still going after 8 segments"))
    r = rf.attach_case(CASE, "abc123", "core", "token", max_min=0)
    assert r["ok"] is False and r["error"] == "still going after 8 segments"


def test_attaching_to_a_run_the_bridge_does_not_know_says_so(monkeypatch):
    def call(method, path, body, tok, timeout=60):
        return (200, {"runs": []}) if path == "/workshop/runs" else (404, {"error": "not_found"})
    monkeypatch.setattr(rf, "call", call)
    r = rf.attach_case(CASE, "nope", "core", "token", max_min=1)
    assert r["ok"] is False and "nope" in r["error"] and r["events"] == []


def test_main_attach_appends_the_result_to_the_results_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(rf, "call", FakeBridge("abc123", EVENTS))
    monkeypatch.setattr(rf, "token", lambda: "token")
    cases, results = tmp_path / "case.json", tmp_path / "workshop.json"
    cases.write_text(json.dumps([CASE]))
    monkeypatch.setattr("sys.argv", ["retest_failures.py", str(cases), str(results), "--brain", "core", "--max-min", "0", "--attach", "abc123"])
    assert rf.main() == 0
    saved = json.loads(results.read_text())
    assert len(saved) == 1 and saved[0]["task"] == "abc123" and saved[0]["ok"] is True and saved[0]["tool_calls"] == 2
    assert "attached" in capsys.readouterr().out
