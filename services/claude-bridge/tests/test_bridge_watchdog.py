"""R06: a run has a clock, and nothing it starts can wait for a person.

The incident class: the bridge read Claude's output with no time limit of any kind. One hung run (a git push waiting for
a Git Credential Manager sign-in window with nobody at the PC, a hung test, an API stall, an MCP connector that never
answers) kept the bridge Busy for good: every later message got HTTP 409 until someone restarted the bridge, and the
orphaned claude.exe kept working in the clone after that.

The watchdog: MAX_RUN_S (default 2 h) of wall clock and IDLE_S (default 30 min) without a single output line each kill
the whole process tree and finish the run failed with a plain message. The tests shorten both to a second or two and
use a fake claude that hangs, never stops talking, or hangs with a grandchild that holds the output pipe."""
import time

import pytest

import workshop_engine as engine
from bridge_testkit import calls, finish, wait_for


@pytest.fixture(autouse=True)
def no_hung_process_outlives_a_test(shop):
    """A fake that hangs loops for ten minutes; whatever a failed assertion leaves running is stopped here."""
    yield
    for task, run in list(shop._runs.items()):
        if not run.done:
            shop.cancel(task)
            finish(run, timeout=30)


def stopped_for_good(path):
    """True if the process that was writing dots to `path` is dead: it did start, and the file has stopped growing."""
    assert path.exists(), "it never started"
    time.sleep(0.4)                                          # a kill is immediate; allow for the last dot in flight
    before = path.stat().st_size
    time.sleep(0.8)
    return path.stat().st_size == before


def go(shop, monkeypatch, tmp_path, mode, **limits):
    monkeypatch.setenv("FAKE_MODE", mode)
    monkeypatch.setenv("FAKE_HEARTBEAT", str(tmp_path / "beat"))
    monkeypatch.setenv("FAKE_CHILD_HEARTBEAT", str(tmp_path / "child-beat"))
    for name, value in limits.items():
        setattr(shop, name, value)
    return time.time()


# ── the idle limit ──────────────────────────────────────────────────────────
def test_a_run_that_goes_silent_is_killed_and_ends_failed_in_plain_words(shop, monkeypatch, tmp_path):
    started = go(shop, monkeypatch, tmp_path, "hang", idle_s=1.0, max_run_s=600.0)
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "do the thing"), timeout=30)
    assert run.ok is False
    assert run.error == ("Claude Code went quiet for 1 second (a command or a sign-in prompt may be stuck), "
                         "so I stopped it. Send the message again to retry.")
    assert time.time() - started < 20
    assert [e["text"] for e in run.events if e["t"] == "text"] == ["starting"]       # what it said before it went quiet is kept
    assert stopped_for_good(tmp_path / "beat")                                       # the process is dead, not abandoned
    assert not shop.busy()
    assert not (tmp_path / "sessions.json").exists()                                  # a run that had to be killed is not resumed


def test_after_a_timeout_the_chat_is_not_locked_the_next_message_runs(shop, monkeypatch, tmp_path):
    go(shop, monkeypatch, tmp_path, "hang", idle_s=1.0)
    first = finish(shop.start("aaaaaaaa", "conv-12345678", "one"), timeout=30)
    assert first.ok is False and shop.busy() is False
    monkeypatch.setenv("FAKE_MODE", "ok")
    second = finish(shop.start("bbbbbbbb", "conv-12345678", "two"))          # before the watchdog this raised Busy (HTTP 409) forever
    assert second.ok is True and second.error is None


def test_a_run_that_keeps_talking_is_not_idle_however_long_it_takes(shop, monkeypatch, tmp_path):
    """The idle limit counts from the LAST line, not from the start: a long job with quiet stretches is fine."""
    started = go(shop, monkeypatch, tmp_path, "steady", idle_s=1.0, max_run_s=600.0)
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "a long job"), timeout=30)
    assert run.ok is True and run.error is None
    assert time.time() - started > 2.0                                       # it really ran for longer than the idle limit


# ── the wall-clock limit ────────────────────────────────────────────────────
def test_a_run_that_never_finishes_is_stopped_at_the_wall_clock_limit_even_while_talking(shop, monkeypatch, tmp_path):
    started = go(shop, monkeypatch, tmp_path, "chatty", idle_s=600.0, max_run_s=2.0)
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "forever"), timeout=30)
    assert run.ok is False
    assert run.error == "Claude Code was still going after 2 seconds, so I stopped it. Send the message again, or a smaller one, to carry on."
    assert 1.5 < time.time() - started < 25
    assert any(e["t"] == "text" and e["text"].startswith("tick") for e in run.events)     # its work so far is still shown
    assert stopped_for_good(tmp_path / "beat")


# ── the whole tree ──────────────────────────────────────────────────────────
def test_the_whole_process_tree_dies_including_a_grandchild_holding_the_output_pipe(shop, monkeypatch, tmp_path):
    """claude starts shells and tools of its own. If only the direct child were killed, a grandchild would keep the
    output pipe open (the run would never see the end of it) and keep working in the clone."""
    go(shop, monkeypatch, tmp_path, "orphan", idle_s=1.0, max_run_s=600.0)
    started = time.time()
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "x"), timeout=30)
    assert run.ok is False and run.error.startswith("Claude Code went quiet for 1 second")
    assert time.time() - started < 25
    assert stopped_for_good(tmp_path / "beat")
    assert stopped_for_good(tmp_path / "child-beat")


def test_stop_ends_a_run_whose_grandchild_still_holds_the_pipe_and_kills_it(shop, monkeypatch, tmp_path):
    go(shop, monkeypatch, tmp_path, "orphan", idle_s=600.0, max_run_s=600.0)
    run = shop.start("aaaaaaaa", "conv-12345678", "x")
    wait_for(lambda: (tmp_path / "child-beat").exists() and (tmp_path / "beat").exists(), what="the fake and its grandchild to start")
    assert shop.cancel("aaaaaaaa")
    finish(run, timeout=30)
    assert run.ok is False and run.error == "stopped"
    assert stopped_for_good(tmp_path / "beat")
    assert stopped_for_good(tmp_path / "child-beat")


# ── the limits themselves ───────────────────────────────────────────────────
def test_the_defaults_are_two_hours_and_half_an_hour_and_can_be_set_per_workshop(shop):
    assert engine.MAX_RUN_S == 2 * 3600 and engine.IDLE_S == 30 * 60
    assert (shop.max_run_s, shop.idle_s) == (2 * 3600, 30 * 60)
    other = engine.Workshop(shop.claude_cmd, repo=shop.repo, primary="p", sessions=shop.sessions_path, max_run_s=5, idle_s="3")
    assert (other.max_run_s, other.idle_s) == (5.0, 3.0)


@pytest.mark.parametrize("seconds, words", [
    (1, "1 second"), (1.4, "1 second"), (2, "2 seconds"), (59, "59 seconds"), (60, "1 minute"), (90, "2 minutes"),
    (1800, "30 minutes"), (3600, "1 hour"), (7200, "2 hours"), (5400, "90 minutes"), (0, "1 second"),
])
def test_durations_read_in_plain_words(seconds, words):
    assert engine._span(seconds) == words


def test_the_default_messages_read_well(shop):
    assert shop._over_limit(7201.0, 0.0, 7200.0) == ("Claude Code was still going after 2 hours, so I stopped it. "
                                                     "Send the message again, or a smaller one, to carry on.")
    assert shop._over_limit(1801.0, 0.0, 0.0).startswith("Claude Code went quiet for 30 minutes")
    assert shop._over_limit(100.0, 0.0, 99.0) is None
    assert shop._over_limit(7199.0, 0.0, 7198.0) is None


# ── nothing waits for a person ──────────────────────────────────────────────
def test_claude_never_waits_for_a_credential_prompt_and_never_gets_the_doors_key(shop, monkeypatch):
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")                           # whatever the PC's environment says, the agent's is fixed
    monkeypatch.setenv("GCM_INTERACTIVE", "always")
    finish(shop.start("aaaaaaaa", "conv-12345678", "x"))
    seen = calls(shop)[0]
    assert seen["git_env"] == {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    assert "SKIPPY_HOME_TOKEN" not in seen["env"]


def test_the_engines_own_git_calls_never_wait_for_a_person_either(shop, monkeypatch):
    git_calls, real_run = [], engine.subprocess.run

    def spy(args, *a, **kw):
        if args and args[0] == "git":
            git_calls.append(kw)
        return real_run(args, *a, **kw)
    monkeypatch.setattr(engine.subprocess, "run", spy)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    monkeypatch.setenv("GCM_INTERACTIVE", "always")
    finish(shop.start("aaaaaaaa", "conv-12345678", "x"))
    assert len(git_calls) >= 2                                               # git status, git pull
    for kw in git_calls:
        assert kw["env"]["GIT_TERMINAL_PROMPT"] == "0" and kw["env"]["GCM_INTERACTIVE"] == "never"
        assert "SKIPPY_HOME_TOKEN" not in kw["env"]
        assert kw["stdin"] is engine.subprocess.DEVNULL                      # and nothing to read a password from
        assert kw["timeout"] in (engine.GIT_TIMEOUT, engine.SHIP_GIT_TIMEOUT)    # the ship check's are shorter still


def test_child_env_sets_the_two_variables_and_drops_the_key():
    env = engine.child_env({"PATH": "/bin", "SKIPPY_HOME_TOKEN": "secret", "GIT_TERMINAL_PROMPT": "1"})
    assert env == {"PATH": "/bin", "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
