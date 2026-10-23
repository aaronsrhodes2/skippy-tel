"""Resumable conversations: the bridge lists the Captain's conversations (GET /workshop/conversations) and gives one back
turn by turn (GET /workshop/conversation/<conv>), from the turns index conversation_log.py keeps beside each
conversation's Markdown. Every test writes into a tmp dir, never the real folder."""
import json
import os
from pathlib import Path

import pytest

import conversation_log as cl
from bridge_testkit import finish

SUMMARY = {"conv", "project", "who", "title", "started", "last", "turns", "state"}


class FakeRun:
    def __init__(self, task, conv="conv-12345678", message="hello", created=1_800_000_000.0, project="skippytel",
                 who="captain@example.com"):
        self.task, self.conv, self.message, self.created, self.project, self.who = task, conv, message, created, project, who
        self.events, self.ok, self.error, self.finished_at, self.clone = [], None, None, None, ""


def test_a_conversation_is_listed_with_its_title_project_turns_and_state(shop):
    long = "make the games tile orange and also " + "very " * 40 + "orange"
    finish(shop.start("aaaaaaaa", "conv-12345678", long, who="captain@example.com"))
    finish(shop.start("bbbbbbbb", "conv-12345678", "and green now", who="captain@example.com"))
    [conv] = shop.conversations()
    assert set(conv) == SUMMARY
    assert conv["conv"] == "conv-12345678" and conv["project"] == "skippytel" and conv["who"] == "captain@example.com"
    assert conv["title"] == "games-tile-orange-orange"            # named, not its first words (no Skippy Core here: the fallback)
    assert conv["turns"] == 2 and conv["state"] == "done" and conv["last"] >= conv["started"]
    assert Path(shop.convlog.root).parent == Path(shop.sessions_path).parent        # a tmp dir: never the real folder


def test_a_conversation_comes_back_turn_by_turn_with_text_and_tool_lines_only(shop):
    finish(shop.start("aaaaaaaa", "conv-12345678", "make it green", who="captain@example.com"))
    got = shop.conversation("conv-12345678")
    assert got["project"] == "skippytel" and got["trimmed"] == 0
    [turn] = got["turns"]
    assert turn["task"] == "aaaaaaaa" and turn["message"] == "make it green" and turn["state"] == "done"
    assert turn["events"][0] == {"t": "text", "text": "Working on it."}
    assert {"t": "tool", "name": "Bash", "detail": "curl -H 'Authorization: [redacted] http://x"} in turn["events"]
    assert "SECRET BODY" not in json.dumps(got) and "TOOL OUTPUT MUST NOT LEAK" not in json.dumps(got)
    assert shop.conversation("conv-00000000") is None and shop.conversation("../etc") is None


def test_the_newest_fifty_newest_first(tmp_path):
    log = cl.ConversationLog(str(tmp_path / "out"))
    for n in range(55):
        run = FakeRun(f"{n:08x}", conv=f"conv-{n:08d}", message=f"message {n}", created=1_800_000_000.0 + n)
        log.started(run)
        run.ok, run.finished_at = True, run.created + 1
        log._written.pop(run.task, None)
        log._turn_finished(log.path(run.conv, run.created), run)
    listed = log.conversations()
    assert len(listed) == 50 and listed[0]["conv"] == "conv-00000054" and listed[-1]["conv"] == "conv-00000005"


def test_a_turn_left_running_by_a_bridge_that_restarted_reads_as_ended(tmp_path):
    log = cl.ConversationLog(str(tmp_path / "out"))
    log.started(FakeRun("aaaaaaaa"))
    going = {"state": "running", "events": [{"t": "text", "text": "halfway"}], "error": None}
    assert log.conversations(live=lambda t: going if t == "aaaaaaaa" else None)[0]["state"] == "running"
    assert log.conversation("conv-12345678", live=lambda t: going)["turns"][0]["events"] == going["events"]
    assert log.conversations()[0]["state"] == "error"                     # nobody is running it any more
    assert log.conversation("conv-12345678")["turns"][0]["state"] == "error"
    over = {"state": "done", "events": [{"t": "text", "text": "all done"}], "error": None}    # ended a moment ago
    assert log.conversation("conv-12345678", live=lambda t: over)["turns"][0]["state"] == "done"


def test_a_failed_turn_keeps_its_error_and_another_project_is_kept_apart(tmp_path):
    log = cl.ConversationLog(str(tmp_path / "out"))
    run = FakeRun("aaaaaaaa", conv="conv-abcdefgh", project="sigma-ground")
    log.started(run)
    run.events = [{"t": "text", "text": "tried"}, {"t": "weird"}, "junk"]
    run.ok, run.error, run.finished_at = False, "tests failed", run.created + 5
    log.finished(run, None)
    got = log.conversation("conv-abcdefgh")
    assert got["project"] == "sigma-ground"
    assert got["turns"][0]["state"] == "error" and got["turns"][0]["error"] == "tests failed"
    assert got["turns"][0]["events"] == [{"t": "text", "text": "tried"}]


def test_a_big_conversation_is_trimmed_from_the_oldest_turn_to_fit(tmp_path):
    log = cl.ConversationLog(str(tmp_path / "out"))
    for n in range(6):
        run = FakeRun(f"aaaaaa{n:02d}", message=f"turn {n}", created=1_800_000_000.0 + n)
        log.started(run)
        run.events = [{"t": "text", "text": "x" * 3000}] * 10
        run.ok, run.finished_at = True, run.created + 1
        log.finished(run, None)
    got = log.conversation("conv-12345678", budget=100_000)
    assert len(json.dumps(got)) <= 100_000 and got["trimmed"] > 0
    assert got["turns"][-1]["message"] == "turn 5"                        # the newest are the ones kept


def test_unreadable_or_foreign_files_are_skipped(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    (root / "2026-01-01_conv-bad00000.turns.json").write_text("{not json", encoding="utf-8")
    (root / "2026-01-01_conv-bad11111.turns.json").write_text(json.dumps({"conv": "../../x", "turns": []}), encoding="utf-8")
    log = cl.ConversationLog(str(root))
    assert log.conversations() == []


def test_a_disk_that_fails_never_breaks_the_run(tmp_path, monkeypatch, capsys):
    log = cl.ConversationLog(str(tmp_path / "out"))
    real = os.replace

    def refuse(src, dst):
        if str(dst).endswith(cl.INDEX_SUFFIX):
            raise PermissionError("[WinError 5] Access is denied")
        return real(src, dst)
    monkeypatch.setattr(cl.os, "replace", refuse)
    monkeypatch.setattr(cl.time, "sleep", lambda s: None)
    log.started(FakeRun("aaaaaaaa"))                                      # no exception
    assert "couldn't write" in capsys.readouterr().err


# ── the door ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/workshop/conversations", "/workshop/conversation/conv-12345678"])
def test_the_conversation_doors_want_the_token(door, path):
    assert door("GET", path, token=None)[0] == 401
    assert door("GET", path, token="wrong")[0] == 401


def test_the_conversation_doors_answer_with_the_listing_and_the_turns(door, shop):
    assert door("GET", "/workshop/conversations") == (200, {"conversations": []})
    assert door("GET", "/workshop/conversation/conv-12345678") == (404, {"error": "unknown_conversation"})
    finish(shop.start("aaaaaaaa", "conv-12345678", "hi", who="captain@example.com"))
    status, body = door("GET", "/workshop/conversations")
    assert status == 200 and [c["conv"] for c in body["conversations"]] == ["conv-12345678"]
    status, body = door("GET", "/workshop/conversation/conv-12345678")
    assert status == 200 and body["turns"][0]["message"] == "hi"
    assert door("GET", "/workshop/conversation/..%2F..%2Fsecrets")[0] == 404


def test_a_new_conversation_is_named_by_skippy_core_once_on_a_thread(tmp_path):
    import time as _t
    asked = []

    def namer(message):
        asked.append(message)
        return "voice-needs-simpler-modes"
    log = cl.ConversationLog(str(tmp_path / "out"), namer=namer)
    first = FakeRun("aaaaaaaa", message="the voice keys are a mess")
    log.started(first)
    for _ in range(500):                                      # (the namer runs on a thread: up to 5 s under a busy test run)
        if log.conversations() and log.conversations()[0]["title"] == "voice-needs-simpler-modes":
            break
        _t.sleep(0.01)
    assert log.conversations()[0]["title"] == "voice-needs-simpler-modes"
    log.started(FakeRun("bbbbbbbb", message="and another thing", created=1_800_000_100.0))
    _t.sleep(0.2)
    assert asked == ["the voice keys are a mess"] and log.conversations()[0]["title"] == "voice-needs-simpler-modes"


def test_a_namer_that_fails_or_rambles_leaves_the_fallback(tmp_path):
    import time as _t
    for namer in (lambda m: 1 / 0, lambda m: "Sure! Here is a name for it"):
        log = cl.ConversationLog(str(tmp_path / str(id(namer))), namer=namer)
        log.started(FakeRun("aaaaaaaa", message="fix the flaky recovery test"))
        _t.sleep(0.05)
        assert log.conversations()[0]["title"] == "fix-flaky-recovery-test"
