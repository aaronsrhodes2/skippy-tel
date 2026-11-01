"""The Captain's prompt is kept until it has a real answer (the Captain, 2026-11-01): a model that goes away mid-answer (Skippy Core's
llama-swap restarting, a dropped connection) is waited out and the same run goes on in the same session; a real failure still ends it."""
import time

import pytest

from bridge_testkit import calls, engine, finish, make_shop


@pytest.fixture
def shop(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "RETRY_BACKOFF_S", (0.1,))
    return make_shop(tmp_path, monkeypatch)


def down(tmp_path, monkeypatch, times):
    f = tmp_path / "down.txt"
    f.write_text(str(times))
    monkeypatch.setenv("FAKE_DOWN", str(f))
    monkeypatch.setenv("FAKE_MODE", "down")


def test_the_model_going_down_mid_answer_is_retried_in_the_same_session_and_answered(shop, tmp_path, monkeypatch):
    down(tmp_path, monkeypatch, 2)
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "fix the tile", who="c@example.com"))
    assert run.ok is True and run.error is None                                   # answered: the dropped tries were never the ending
    retries = [e for e in run.events if e.get("t") == "retry"]
    assert [e["attempt"] for e in retries] == [1, 2] and "Connection error" in retries[0]["why"]
    tries = calls(shop)
    assert len(tries) == 3 and tries[0]["stdin"] == "fix the tile"
    assert all("--resume" in t["argv"] and "sess-1" in t["argv"] for t in tries[1:])     # it goes on where it was, not from scratch
    assert all(t["stdin"] == engine.RESUME_NOTE + "fix the tile" for t in tries[1:])


def test_a_real_failure_is_not_retried(shop, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "errors-only")                                # "The repository is on fire": an answer, of sorts
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    assert run.ok is False and "on fire" in run.error and len(calls(shop)) == 1


def test_it_gives_up_only_when_the_window_runs_out(shop, tmp_path, monkeypatch):
    down(tmp_path, monkeypatch, 1000)
    shop.retry_for_s = 0.5
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    assert run.ok is False and "Connection error" in run.error and len(calls(shop)) >= 2


def test_stop_while_it_waits_ends_it_at_once(shop, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "RETRY_BACKOFF_S", (30.0,))
    down(tmp_path, monkeypatch, 1000)
    run = shop.start("aaaaaaaa", "conv-12345678", "hi")
    deadline = time.time() + 20
    while not any(e.get("t") == "retry" for e in run.events) and time.time() < deadline:
        time.sleep(0.05)
    assert shop.cancel("aaaaaaaa")
    finish(run, timeout=5)
    assert run.error == "stopped"


@pytest.mark.parametrize("error, again", [
    ("API Error: Connection error.", True), ("API Error: 503 Service Unavailable", True), ("API Error: 529 overloaded_error", True),
    ("Request timed out.", True), ("fetch failed", True), ("API Error: 400 prompt is too long", False),
    ("Failed to authenticate: OAuth session expired", False), ("stopped", False), (None, False)])
def test_what_counts_as_the_model_going_away(error, again):
    assert engine.transient(error) is again
