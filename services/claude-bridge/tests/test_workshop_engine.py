"""The private Skippy's engine and its HTTP door, against a stand-in `claude` (no login, no tokens)."""
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bridge  # noqa: E402
import workshop_engine as engine  # noqa: E402

FAKE = r'''
import json, os, sys
mode = os.environ.get("FAKE_MODE", "ok")
rec = os.environ["FAKE_RECORD"]
with open(rec, "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read(), "env": sorted(os.environ)}) + "\n")

def out(**e):
    print(json.dumps(e), flush=True)

resuming = "--resume" in sys.argv
out(type="system", subtype="init", session_id="sess-1", model="claude-opus-5-5")
if mode == "auth":
    msg = "Failed to authenticate: OAuth session expired and could not be refreshed"
    out(type="assistant", message={"content": [{"type": "text", "text": msg}]})
    out(type="result", is_error=True, result=msg, session_id="sess-1")
elif mode == "noresume" and resuming:
    out(type="result", is_error=True, result="No conversation found with session ID: sess-old", session_id="x")
elif mode == "slow":
    import time
    out(type="assistant", message={"content": [{"type": "text", "text": "starting"}]})
    time.sleep(60)
else:
    out(type="assistant", message={"content": [{"type": "text", "text": "Working on it."},
        {"type": "tool_use", "name": "Bash", "input": {"command": "curl -H 'Authorization: Bearer abcdef123456' http://x"}},
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "portal/templates/home.html", "new_string": "SECRET BODY"}}]})
    out(type="user", message={"content": [{"type": "tool_result", "content": "TOOL OUTPUT MUST NOT LEAK"}]})
    out(type="assistant", message={"content": [{"type": "text", "text": "Done."}]})
    out(type="result", is_error=False, result="Done.", session_id="sess-1")
'''


@pytest.fixture
def shop(tmp_path, monkeypatch):
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE, encoding="utf-8")
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("FAKE_RECORD", str(record))
    monkeypatch.setenv("SKIPPY_HOME_TOKEN", "top-secret-token")
    origin = tmp_path / "origin"
    origin.mkdir()
    for cmd in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "first"]):
        subprocess.run(["git", *cmd], cwd=origin, check=True, capture_output=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(tmp_path / "clone")], check=True, capture_output=True)
    w = engine.Workshop([sys.executable, str(fake)], repo=str(tmp_path / "clone"), primary=str(tmp_path / "primary"),
                        sessions=str(tmp_path / "sessions.json"), remote=str(origin))
    w.record, w.origin = record, origin
    return w


def finish(run, timeout=30):
    deadline = time.time() + timeout
    while not run.done and time.time() < deadline:
        run.since(0, wait=0.5)
    assert run.done, "the run never finished"
    return run


def calls(shop):
    return [json.loads(line) for line in shop.record.read_text().splitlines()]


def test_the_model_is_hard_coded_and_the_agent_has_full_permissions(shop):
    cmd = shop.command(None)
    assert engine.MODEL == "claude-opus-5-5" and cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert "--dangerously-skip-permissions" in cmd and "--resume" not in cmd
    assert shop.command("sess-9")[-2:] == ["--resume", "sess-9"]
    prompt = cmd[cmd.index("--append-system-prompt") + 1]
    assert shop.repo in prompt and shop.primary in prompt          # it works in its own clone, deploys from the Captain's
    assert "{repo}" not in prompt and "{primary}" not in prompt


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


def test_bad_input_is_refused(shop):
    for args in (("../x", "conv-12345678", "hi"), ("aaaaaaaa", "no", "hi"), ("aaaaaaaa", "conv-12345678", "  ")):
        with pytest.raises(ValueError):
            shop.start(*args)


def test_the_first_task_clones_the_repo_and_stop_works_at_any_moment(shop, tmp_path):
    fresh = engine.Workshop(shop.claude_cmd, repo=str(tmp_path / "new-clone"), primary="p",
                            sessions=str(tmp_path / "s2.json"), remote=str(shop.origin))
    run = finish(fresh.start("aaaaaaaa", "conv-12345678", "hi"))
    assert run.ok is True and run.events[0] == {"t": "tool", "name": "git", "detail": "cloning the repo for the first time"}
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


# ── the HTTP door ───────────────────────────────────────────────────────────
@pytest.fixture
def door(shop, monkeypatch):
    monkeypatch.setattr(bridge, "WORKSHOP", shop)
    monkeypatch.setattr(bridge, "_token_cache", (time.time() + 3600, "door-token"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(method, path, body=None, token="door-token"):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    yield call
    server.shutdown()


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
