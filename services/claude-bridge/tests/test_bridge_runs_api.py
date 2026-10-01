"""Whose run it is (`who`), re-attaching without ever starting (`attach`), and the listing the home reporter reconciles
the portal with (GET /workshop/runs). The incident behind them: after a deploy the portal forgot a run the bridge had
finished, and nothing on skippy-pc's side could say "this run exists, it is this person's, here are its events"."""
import json
import time
from pathlib import Path

import pytest

import bridge
import workshop_engine as engine
from bridge_testkit import calls, finish, saved, wait_for

BODY = {"task": "aaaaaaaa", "conv": "conv-12345678", "message": "make the home tile green"}
LISTED = {"task", "conv", "who", "project", "events", "done", "ok", "error", "age_s", "finished_age_s"}


def start_slow(door, shop, monkeypatch, who=""):
    """A run that stays running until cancelled; returns once the (fake) claude is really going."""
    monkeypatch.setenv("FAKE_MODE", "slow")
    assert door("POST", "/workshop", {**BODY, "who": who})[0] == 200
    wait_for(shop.record.exists, what="the fake claude to start")
    return shop.get("aaaaaaaa")


def stop(shop, run):
    shop.cancel(run.task)
    finish(run)


# ── who ─────────────────────────────────────────────────────────────────────
def test_who_is_stored_on_the_run_and_listed(door, shop):
    assert door("POST", "/workshop", {**BODY, "who": "captain@example.com"})[0] == 200
    run = finish(shop.get("aaaaaaaa"))
    assert run.who == "captain@example.com"
    status, got = door("GET", "/workshop/runs")
    assert status == 200 and [r["who"] for r in got["runs"]] == ["captain@example.com"]
    assert saved(shop, "aaaaaaaa", lambda d: d["done"])["who"] == "captain@example.com"      # and it survives a restart


def test_a_run_without_who_has_an_empty_one(door, shop):
    assert door("POST", "/workshop", BODY)[0] == 200
    assert finish(shop.get("aaaaaaaa")).who == ""


def test_who_is_limited_to_200_characters_and_refused_not_cut(door, shop):
    assert door("POST", "/workshop", {**BODY, "who": "x" * 201})[0] == 400       # a cut email would name somebody else
    assert shop.get("aaaaaaaa") is None and not shop.busy()
    assert door("POST", "/workshop", {**BODY, "who": "y" * 200})[0] == 200
    assert finish(shop.get("aaaaaaaa")).who == "y" * 200


def test_a_known_run_without_who_takes_it_from_the_next_request_but_never_changes_it(door, shop):
    """A run the old reporter started has no owner on the bridge; the new reporter's re-attach supplies it."""
    finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    assert shop.get("aaaaaaaa").who == ""
    assert door("POST", "/workshop", {**BODY, "who": "first@example.com", "attach": True})[0] == 200
    assert shop.get("aaaaaaaa").who == "first@example.com"
    assert saved(shop, "aaaaaaaa", lambda d: d["who"] == "first@example.com")["done"] is True
    assert door("POST", "/workshop", {**BODY, "who": "second@example.com", "attach": True})[0] == 200
    assert shop.get("aaaaaaaa").who == "first@example.com"


# ── attach ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("body", [
    {**BODY, "attach": True},                                                    # with a message, even
    {"task": "aaaaaaaa", "conv": "conv-12345678", "attach": True},               # without: a re-attach needs none
    {"task": "aaaaaaaa", "conv": "conv-12345678", "attach": True, "who": "c@example.com"},
])
def test_attaching_to_an_unknown_task_answers_404_and_starts_nothing(door, shop, body):
    assert door("POST", "/workshop", body) == (404, {"error": "unknown_task"})
    assert shop.get("aaaaaaaa") is None and not shop.busy() and shop.listing() == []
    assert not shop.record.exists()                                              # the fake claude never ran
    assert not Path(shop.state_dir).exists() or not list(Path(shop.state_dir).iterdir())   # nor was anything saved


def test_attach_false_is_an_ordinary_start(door, shop):
    assert door("POST", "/workshop", {**BODY, "attach": False})[0] == 200
    assert finish(shop.get("aaaaaaaa")).ok is True


def test_attaching_to_a_known_task_needs_no_message_and_never_reruns_it(door, shop):
    finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    for body in ({"task": "aaaaaaaa", "conv": "conv-12345678", "attach": True},
                 {**BODY, "attach": True}):
        status, out = door("POST", "/workshop", body)
        assert status == 200 and out == {"ok": True, "task": "aaaaaaaa", "done": True}
    assert len(calls(shop)) == 1                                                 # claude ran once, for the original start


def test_an_empty_message_without_attach_is_still_refused_even_for_a_known_task(door, shop):
    assert door("POST", "/workshop", {**BODY, "message": "  "})[0] == 400
    finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    assert door("POST", "/workshop", {**BODY, "message": ""})[0] == 400
    assert door("POST", "/workshop", {"task": "bad", "conv": "conv-12345678", "attach": True})[0] == 400   # ids still checked


def test_attach_to_an_unknown_task_is_404_even_while_another_run_is_busy_and_attach_to_the_busy_one_works(
        door, shop, monkeypatch):
    run = start_slow(door, shop, monkeypatch)
    try:
        assert door("POST", "/workshop", {"task": "bbbbbbbb", "conv": "conv-12345678", "attach": True}) == (
            404, {"error": "unknown_task"})                                      # not 409: it was never going to start
        status, out = door("POST", "/workshop", {"task": "aaaaaaaa", "conv": "conv-12345678", "attach": True})
        assert status == 200 and out["done"] is False and shop.get("aaaaaaaa") is run
        assert door("POST", "/workshop", {"task": "bbbbbbbb", "conv": "conv-12345678", "message": "x"})[0] == 409
    finally:
        stop(shop, run)
    assert len(calls(shop)) == 1


# ── GET /workshop/runs ──────────────────────────────────────────────────────
def test_the_listing_has_exactly_the_documented_shape_and_no_content(door, shop):
    finish(shop.start("aaaaaaaa", "conv-12345678", "make the home tile green", who="captain@example.com"))
    status, got = door("GET", "/workshop/runs")
    assert status == 200 and set(got) == {"runs"} and len(got["runs"]) == 1
    row = got["runs"][0]
    assert set(row) == LISTED
    assert (row["task"], row["conv"], row["who"]) == ("aaaaaaaa", "conv-12345678", "captain@example.com")
    assert row["events"] == 4 and row["done"] is True and row["ok"] is True and row["error"] is None
    assert isinstance(row["age_s"], float) and isinstance(row["finished_age_s"], float)
    assert 0 <= row["finished_age_s"] <= row["age_s"] < 120
    text = json.dumps(got)
    for secret in ("make the home tile green", "Working on it.", "Done.", "SECRET BODY", "home.html", "abcdef123456"):
        assert secret not in text                                                # counts and state, never content


def test_a_running_run_is_listed_as_not_done_with_no_finish_age(door, shop, monkeypatch):
    run = start_slow(door, shop, monkeypatch, who="captain@example.com")
    try:
        row = door("GET", "/workshop/runs")[1]["runs"][0]
        assert row["done"] is False and row["ok"] is None and row["error"] is None and row["finished_age_s"] is None
        assert row["age_s"] >= 0 and row["who"] == "captain@example.com"
    finally:
        stop(shop, run)
    row = door("GET", "/workshop/runs")[1]["runs"][0]
    assert row["done"] is True and row["ok"] is False and row["error"] == "stopped" and row["finished_age_s"] >= 0


def test_the_listing_is_newest_first_and_at_most_fifty(door, shop):
    now = time.time()
    for i in range(60):
        run = engine.Run(f"{i:08x}", "conv-12345678", "m", f"u{i}@example.com", created=now - 1000 + i)
        run.finish(True)
        shop.adopt(run)
    rows = door("GET", "/workshop/runs")[1]["runs"]
    assert len(rows) == engine.MAX_LISTED == 50
    assert [r["task"] for r in rows] == [f"{i:08x}" for i in range(59, 9, -1)]       # the ten oldest fell off the end


def test_an_empty_bridge_lists_nothing(door):
    assert door("GET", "/workshop/runs") == (200, {"runs": []})


def test_the_listing_needs_the_token_like_every_other_workshop_door(door, monkeypatch):
    finish_body = {"task": "aaaaaaaa", "conv": "conv-12345678", "message": "hi"}
    assert door("POST", "/workshop", finish_body)[0] == 200
    assert door("GET", "/workshop/runs", token=None)[0] == 401
    assert door("GET", "/workshop/runs", token="wrong")[0] == 401
    assert door("GET", "/workshop/runs", token="door-toke")[0] == 401
    monkeypatch.setattr(bridge, "_token_cache", (time.time() + 3600, ""))          # none configured: shut for everyone
    assert door("GET", "/workshop/runs", token=None)[0] == 503
    assert door("GET", "/workshop/runs", token="door-token")[0] == 503
