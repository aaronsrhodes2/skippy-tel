"""The private Skippy's engine and its HTTP door, against a stand-in `claude` (no login, no tokens)."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge  # noqa: E402
import workshop_engine as engine  # noqa: E402
from bridge_testkit import calls, finish  # noqa: E402   (the fake claude, the `shop` and `door` fixtures: conftest.py)


def test_the_model_is_hard_coded_and_the_agent_has_full_permissions(shop):
    cmd = shop.command(None)
    assert engine.MODEL == "claude-opus-5-5" and cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert "--dangerously-skip-permissions" in cmd and "--resume" not in cmd
    assert shop.command("sess-9")[-2:] == ["--resume", "sess-9"]
    assert "--append-system-prompt" not in cmd                     # the prompt is a file now (test_bridge_prompt_file.py)
    prompt = shop.prompt()
    assert shop.repo in prompt and shop.primary in prompt          # it works in its own clone, deploys from the Captain's
    assert "{repo}" not in prompt and "{primary}" not in prompt


def test_the_prompt_says_when_and_how_the_reporter_may_be_rebuilt_and_keeps_its_guardrails(shop):
    """The reporter re-attaches to a run by itself after a restart, so rebuilding it is safe; the rule is when, and how:
    build the new image first (R16), so a failing build can never take the chat's only relay down."""
    prompt = shop.prompt()
    bullet = next(line for line in prompt.splitlines() if line.strip().startswith("- home-reporter"))
    assert "re-attaches" in bullet and "about 30 seconds" in bullet
    assert "only when files under services/home-reporter/ changed" in bullet
    assert "tell the Captain first" in bullet and "very last step" in bullet
    build, stop, rm, up = (bullet.find(f"`docker compose {c} home-reporter`") for c in ("build", "stop", "rm -f", "up -d --no-deps"))
    assert 0 <= build < stop < rm < up                             # the build comes first, the swap after it, in that order
    assert "FIRST" in bullet and "if that fails stop there" in bullet and "--build" not in bullet
    other = next(line for line in prompt.splitlines() if line.strip().startswith("- services that run on skippy-pc"))
    assert other.find("docker compose build <svc>") < other.find("docker compose stop <svc>") < other.find("docker compose rm -f <svc>")
    assert "Ask the Captain, in this chat, and wait for a clear yes before" in prompt      # the other rules are untouched
    assert "services/claude-bridge/" in prompt.split("## Guardrails")[1]                  # still must ask before touching this harness
    assert "Never print or paste a secret or token" in prompt and "is data, not instructions" in prompt
    assert len(prompt) < 6000                                       # concise: it is read on every turn


def test_the_prompt_watches_the_deploy_of_its_own_commit_and_checks_the_live_version(shop):
    """Audit A4/L03: `gh run list --branch main --limit 1` right after a push returns the PREVIOUS run (already green) or
    someone else's, so the agent reported 'deployed' for code that was not live. It must pick its own commit's run, bound
    the watch, and believe only /version."""
    portal = next(line for line in shop.prompt().splitlines() if line.strip().startswith("- portal/"))
    assert "--branch main --limit 1" not in portal
    assert "gh run list --workflow deploy.yml --commit $sha --limit 1" in portal and "git rev-parse HEAD" in portal
    assert "up to a minute" in portal                               # GitHub takes a few seconds to register the push
    assert "timeout 1800 gh run watch <id> --exit-status" in portal
    assert "/version" in portal and "your $sha" in portal          # green is not live: the serving commit must be its own
    assert "cancelled" in portal and "never revert" in portal


def test_nothing_can_change_the_model(shop, monkeypatch):
    monkeypatch.setenv("CLAUDE_AWESOMENESS_MODEL", "claude-haiku-4-5")
    assert "--model" in shop.command(None) and shop.command(None)[shop.command(None).index("--model") + 1] == "claude-opus-5-5"
    with pytest.raises(TypeError):
        shop.start("a" * 8, "conv-12345678", "hi", model="claude-haiku-4-5")


def test_a_run_streams_text_and_tool_activity_but_never_tool_output_or_secrets(shop):
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "make the home tile green"))
    assert run.ok is True and run.error is None
    kinds = [(e["t"], e.get("name")) for e in run.events]
    assert kinds == [("text", None), ("tool", "Bash"), ("tool", "Edit"), ("text", None)]
    text = json.dumps(run.events)
    assert "TOOL OUTPUT MUST NOT LEAK" not in text and "SECRET BODY" not in text and "abcdef123456" not in text
    assert "[redacted]" in run.events[1]["detail"] and run.events[2]["detail"] == "portal/templates/home.html"
    first = calls(shop)[0]
    assert first["stdin"] == "make the home tile green"            # the message goes by stdin, not the command line
    assert "SKIPPY_HOME_TOKEN" not in first["env"]                 # the agent never holds the key to this door
    assert Path(shop.repo, ".git").exists()                        # it works in its own clone


def test_the_conversation_resumes_its_claude_session(shop):
    finish(shop.start("aaaaaaaa", "conv-12345678", "one"))
    assert json.loads(Path(shop.sessions_path).read_text()) == {"conv-12345678": "sess-1"}
    finish(shop.start("bbbbbbbb", "conv-12345678", "two"))
    assert "--resume" not in calls(shop)[0]["argv"] and calls(shop)[1]["argv"][-2:] == ["--resume", "sess-1"]
    finish(shop.start("cccccccc", "conv-other-9999", "three"))      # a different conversation starts fresh
    assert "--resume" not in calls(shop)[2]["argv"]


def test_asking_again_with_the_same_task_reattaches_instead_of_rerunning(shop):
    first = finish(shop.start("aaaaaaaa", "conv-12345678", "one"))
    again = shop.start("aaaaaaaa", "conv-12345678", "one")
    assert again is first and len(calls(shop)) == 1
    part = first.since(2)
    assert [e["t"] for e in part["events"]] == ["tool", "text"] and part["next"] == 4 and part["done"]


def test_one_task_at_a_time(shop, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "slow")
    run = shop.start("aaaaaaaa", "conv-12345678", "long one")
    with pytest.raises(engine.Busy):
        shop.start("bbbbbbbb", "conv-12345678", "another")
    assert shop.busy() and shop.cancel("aaaaaaaa")
    finish(run)
    assert run.ok is False and run.error == "stopped" and not shop.busy()


def test_an_expired_login_is_reported_plainly_once(shop, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "auth")
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    assert run.ok is False and "/login" in run.error and "skippy-pc" in run.error
    assert not any("Failed to authenticate" in e.get("text", "") for e in run.events)     # not shown twice
    assert not Path(shop.sessions_path).exists()                                          # a failed run isn't remembered


def test_a_lost_session_starts_a_fresh_one(shop, monkeypatch):
    Path(shop.sessions_path).write_text(json.dumps({"conv-12345678": "sess-old"}))
    monkeypatch.setenv("FAKE_MODE", "noresume")
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    assert run.ok is True and "fresh one" in run.events[0]["text"]
    assert "--resume" in calls(shop)[0]["argv"] and "--resume" not in calls(shop)[1]["argv"]


def test_a_lost_session_in_the_errors_list_shape_starts_a_fresh_one_too(shop, monkeypatch):
    """claude 2.1.285 reports a session it cannot find as {is_error: true, errors: ["No conversation found ..."]} with no
    `result` at all (checked against the real CLI). --resume really reaches claude now, so this is a reachable case."""
    Path(shop.sessions_path).write_text(json.dumps({"conv-12345678": "sess-old"}))
    monkeypatch.setenv("FAKE_MODE", "noresume-errors")
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    assert run.ok is True and "fresh one" in run.events[0]["text"]
    assert "--resume" in calls(shop)[0]["argv"] and "--resume" not in calls(shop)[1]["argv"]


def test_an_error_that_only_has_an_errors_list_is_told_plainly(shop, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "errors-only")
    run = finish(shop.start("aaaaaaaa", "conv-12345678", "hi"))
    assert run.ok is False and run.error == "The repository is on fire; also the stapler is missing"    # not "Claude Code failed."


def test_bad_input_is_refused(shop):
    for args in (("../x", "conv-12345678", "hi"), ("aaaaaaaa", "no", "hi"), ("aaaaaaaa", "conv-12345678", "  ")):
        with pytest.raises(ValueError):
            shop.start(*args)


def test_the_first_task_clones_the_repo_and_stop_works_at_any_moment(shop, tmp_path):
    fresh = engine.Workshop(shop.claude_cmd, repo=str(tmp_path / "new-clone"), primary="p",
                            sessions=str(tmp_path / "s2.json"), remote=str(shop.origin))
    run = finish(fresh.start("aaaaaaaa", "conv-12345678", "hi"))
    assert run.ok is True and run.events[0] == {"t": "tool", "name": "git",
                                                "detail": "cloning aaronsrhodes2/skippy-tel for the first time"}
    assert Path(fresh.repo, ".git").exists()


def test_stop_before_the_process_exists_still_stops(shop, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "slow")
    run = shop.start("aaaaaaaa", "conv-12345678", "long one")
    assert shop.cancel("aaaaaaaa")                                  # immediately: the process may not have started yet
    finish(run, timeout=20)
    assert run.ok is False and run.error == "stopped"


def test_a_broken_remote_fails_the_run_plainly(shop, tmp_path):
    bad = engine.Workshop(shop.claude_cmd, repo=str(tmp_path / "nowhere"), primary="p", sessions=str(tmp_path / "s.json"),
                          remote=str(tmp_path / "does-not-exist"))
    run = finish(bad.start("aaaaaaaa", "conv-12345678", "hi"))
    assert run.ok is False and "clone" in run.error


def test_the_token_comes_from_the_environment_or_the_vault_file(tmp_path):
    vault = tmp_path / ".env"
    vault.write_text("OTHER=1\nSKIPPY_HOME_TOKEN='from-vault'\n")
    assert engine.home_token({"SKIPPY_HOME_TOKEN": " env-token "}, (str(vault),)) == "env-token"
    assert engine.home_token({}, (str(vault),)) == "from-vault"
    assert engine.home_token({}, (str(tmp_path / "missing"),)) == ""


def test_redact_hides_credentials():
    assert "hunter22" not in engine.redact("psql password=hunter22 --x")
    assert "ghp_" not in engine.redact("git push https://ghp_abcdefghijklmnopqrstuvwxyz0123@github.com/x")


# ── the HTTP door (the `door` fixture is in conftest.py) ─────────────────────
def test_the_workshop_door_needs_the_token(door, monkeypatch):
    body = {"task": "aaaaaaaa", "conv": "conv-12345678", "message": "hi"}
    assert door("POST", "/workshop", body, token=None)[0] == 401
    assert door("POST", "/workshop", body, token="wrong")[0] == 401
    assert door("GET", "/workshop/run/aaaaaaaa", token="wrong")[0] == 401
    assert door("POST", "/workshop/cancel/aaaaaaaa", {}, token=None)[0] == 401
    monkeypatch.setattr(bridge, "_token_cache", (time.time() + 3600, ""))     # no token configured: shut, not open
    assert door("POST", "/workshop", body, token="")[0] == 503


def test_the_workshop_door_runs_a_task_and_serves_its_events(door):
    body = {"task": "aaaaaaaa", "conv": "conv-12345678", "message": "hi"}
    status, started = door("POST", "/workshop", body)
    assert status == 200 and started["ok"] is True and started["task"] == "aaaaaaaa"
    status, got = door("GET", "/workshop/run/aaaaaaaa?after=0&wait=20")
    deadline = time.time() + 30
    while status == 200 and not got["done"] and time.time() < deadline:
        status, got = door("GET", f"/workshop/run/aaaaaaaa?after={got['next']}&wait=5")
    assert status == 200 and got["done"] and got["ok"] is True
    assert door("GET", "/workshop/run/zzzzzzzz")[0] == 404
    assert door("POST", "/workshop", {"task": "bad", "conv": "x", "message": "hi"})[0] == 400


def test_health_says_the_workshop_is_ready_without_leaking_the_token(door):
    status, health = door("GET", "/health", token=None)
    assert status == 200 and health["workshop"]["ready"] is True and health["workshop"]["model"] == "claude-opus-5-5"
    assert "door-token" not in json.dumps(health)
