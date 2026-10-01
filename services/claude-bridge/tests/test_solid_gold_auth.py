"""/solid-gold runs Claude Code with every permission check skipped ("trust the awesomeness": mode `bypass`), and the
bridge listens on 0.0.0.0 so Docker can reach it. It used to take no authentication (the README's own "Security note");
it now wants the same bearer token as the workshop, and with no token configured it stays shut (Platinum section 19,
condition 1).

The read-only "plan" mode (the "solid gold" tier) was removed at the Captain's request: only `bypass` is accepted now,
and `plan` answers 400 with a plain message. The endpoint keeps its path, and its token guard is unchanged.

These tests stand up the real HTTP door and prove the one thing that matters: Claude does not run without the token."""
import importlib.util
import inspect
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "claude-bridge"))

import bridge  # noqa: E402
from bridge_testkit import calls  # noqa: E402


@pytest.fixture
def door(monkeypatch):
    ran = []

    def fake_run(prompt):
        ran.append(prompt)
        return {"output": "ran", "returncode": 0}

    monkeypatch.setattr(bridge, "run_oneshot", fake_run)
    monkeypatch.setattr(bridge, "_token_cache", (time.time() + 3600, "door-token"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(path, body=None, token="door-token", method="POST", raw=False):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        req = urllib.request.Request(base + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
                return r.status, (data.decode() if raw else json.loads(data or b"{}"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    call.ran, call.base, call.monkeypatch = ran, base, monkeypatch
    yield call
    server.shutdown()


@pytest.mark.parametrize("mode", ["bypass", "plan", None])
def test_claude_never_runs_without_the_token(door, mode):
    """Whatever mode is asked for (the removed one included): no valid token, no run, and no hint about the input rules."""
    body = {"text": "list the files"} | ({"mode": mode} if mode else {})
    assert door("/solid-gold", body, token=None)[0] == 401                   # no header
    assert door("/solid-gold", body, token="wrong")[0] == 401                # wrong token
    assert door("/solid-gold", body, token="door-toke")[0] == 401            # a prefix of the right one
    assert door("/solid-gold", body, token="door-token-and-more")[0] == 401
    assert door("/solid-gold", body, token="")[0] == 401
    assert door.ran == []                                                     # the runner was never reached, not once


def test_the_right_token_opens_it_for_bypass(door):
    status, out = door("/solid-gold", {"text": "list the files", "mode": "bypass"})
    assert status == 200 and out["output"] == "ran"
    assert door.ran == ["list the files"]


def test_plan_mode_is_gone_and_says_so(door):
    status, out = door("/solid-gold", {"text": "list the files", "mode": "plan"})
    assert status == 400 and out["error"] == "bad_request"
    assert "removed" in out["message"] and "bypass" in out["message"] and "plan" in out["message"]
    assert door("/solid-gold", {"text": "list the files", "mode": "plan", "stream": True})[0] == 400
    assert door.ran == []                                                     # and it was never run


@pytest.mark.parametrize("body", [
    {"text": "x"},                                  # no mode: bypass is never assumed (it skips every permission check)
    {"text": "x", "mode": ""}, {"text": "x", "mode": None}, {"text": "x", "mode": "root"},
    {"text": "x", "mode": "BYPASS"}, {"text": "x", "mode": ["bypass"]}, {"text": "x", "mode": "plan "},
])
def test_only_the_exact_word_bypass_is_accepted(door, body):
    status, out = door("/solid-gold", body)
    assert status == 400 and out["error"] == "bad_request"
    assert door.ran == []


def test_with_no_token_configured_the_whole_bridge_stays_shut(door):
    door.monkeypatch.setattr(bridge, "_token_cache", (time.time() + 3600, ""))
    status, out = door("/solid-gold", {"text": "x", "mode": "bypass"})
    assert status == 503 and out["error"] == "bridge_disabled" and door.ran == []
    assert door("/solid-gold", {"text": "x"}, token=None)[0] == 503           # shut for everyone, header or not
    assert door("/workshop", {"task": "t", "conv": "c", "message": "m"})[0] == 503


def test_bad_input_is_refused_only_after_the_token_is_checked(door):
    assert door("/solid-gold", {"text": "", "mode": "bypass"})[0] == 400      # authorised, but nothing to do
    assert door("/solid-gold", {"text": "", "mode": "plan"})[0] == 400
    assert door("/solid-gold", {"text": "x", "mode": "root"})[0] == 400
    assert door("/solid-gold", {"text": "", "mode": "bypass"}, token="wrong")[0] == 401   # a stranger learns nothing about the input rules
    assert door("/solid-gold", {"text": "x", "mode": "plan"}, token="wrong")[0] == 401


def test_the_stream_path_serves_bypass_as_ndjson_and_refuses_plan(door, monkeypatch):
    seen = []

    def fake_streaming(prompt, write_chunk):
        seen.append(prompt)
        write_chunk("first")
        write_chunk("second")
        return 0
    monkeypatch.setattr(bridge, "run_streaming", fake_streaming)
    status, text = door("/solid-gold", {"text": "go", "mode": "bypass", "stream": True}, raw=True)
    assert status == 200
    assert [json.loads(line) for line in text.splitlines()] == [{"text": "first"}, {"text": "second"}, {"done": True, "returncode": 0}]
    assert seen == ["go"]
    assert door("/solid-gold", {"text": "go", "mode": "plan", "stream": True})[0] == 400 and seen == ["go"]


def test_health_stays_open_for_the_liveness_ping_and_never_shows_the_token(door):
    status, out = door("/health", token=None, method="GET")
    assert status == 200 and out["service"] == "claude-bridge"
    assert "door-token" not in json.dumps(out)


# ── the command it runs ─────────────────────────────────────────────────────
def test_the_command_is_full_permissions_with_no_plan_mode_and_no_prompt_on_the_command_line():
    cmd = bridge.build_cmd()
    assert cmd[0] == bridge.CLAUDE and cmd[1] == "-p"
    assert "--dangerously-skip-permissions" in cmd and cmd[cmd.index("--model") + 1] == bridge.AWE_MODEL
    assert "--permission-mode" not in cmd and "plan" not in cmd
    assert cmd[2:5] == ["--output-format", "stream-json", "--verbose"]
    assert not inspect.signature(bridge.build_cmd).parameters                 # no prompt, no mode: stdin carries the text


def test_a_multiline_prompt_arrives_whole_by_stdin_with_every_flag_intact(shop, monkeypatch):
    """The same newline trap as the workshop's (R01): claude.cmd's cmd.exe cuts a command line at the first newline, and
    the prompt used to be an argument, so a two-line request lost --output-format, --model and
    --dangerously-skip-permissions with it. The prompt now goes by stdin, like the workshop's message."""
    real = bridge.build_cmd
    monkeypatch.setattr(bridge, "build_cmd", lambda: [sys.executable, str(shop.fake), *real()[1:]])
    monkeypatch.setattr(bridge, "CWD", str(shop.repo))
    text = "first line\nsecond line with \"quotes\" & an ampersand\nthird %PATH% line"
    out = bridge.run_oneshot(text)
    assert out["returncode"] == 0 and "Working on it." in out["output"] and "Done." in out["output"]
    seen = calls(shop)[0]
    assert seen["stdin"] == text
    assert seen["argv"] == real()[1:]                                         # the flags, and nothing of the prompt
    assert not [a for a in seen["argv"] if "\n" in a or "line" in a]
    assert "SKIPPY_HOME_TOKEN" not in seen["env"]                             # the agent never holds the key to this door
    assert seen["git_env"] == {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


# ── flask-api's client against the real handler ─────────────────────────────
def test_flask_apis_client_sends_the_token_on_every_call():
    """The only caller of /solid-gold is flask-api's client; if it stopped sending the header the tiers would go dark."""
    src = (ROOT / "flask-api" / "app" / "clients" / "claude_bridge.py").read_text(encoding="utf-8")
    assert src.count("headers=_headers()") == 2                               # chat() and stream_chat()
    assert 'os.getenv("SKIPPY_HOME_TOKEN"' in src and "Bearer" in src
    assert "HTTP {code}" in src and "SKIPPY_HOME_TOKEN must be set" in src    # a refusal says what to fix, not just a number


def test_the_client_and_the_real_bridge_agree(door, monkeypatch):
    """End to end: flask-api's client against the real handler, with and without the token in its environment."""
    pytest.importorskip("httpx")                       # flask-api's dependency; CI's portal environment doesn't have it
    spec = importlib.util.spec_from_file_location("claude_bridge_client", ROOT / "flask-api" / "app" / "clients" / "claude_bridge.py")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    monkeypatch.setattr(client, "BRIDGE_URL", door.base)

    monkeypatch.setenv("SKIPPY_HOME_TOKEN", "door-token")
    assert client.chat("hello", mode="bypass") == "ran"
    assert door.ran == ["hello"]

    monkeypatch.setenv("SKIPPY_HOME_TOKEN", "the-wrong-one")
    with pytest.raises(client.BridgeUnavailable, match="refused the request .HTTP 401.*SKIPPY_HOME_TOKEN"):
        client.chat("hello", mode="bypass")
    monkeypatch.delenv("SKIPPY_HOME_TOKEN")
    with pytest.raises(client.BridgeUnavailable, match="HTTP 401"):
        list(client.stream_chat("hello", mode="bypass"))
    assert door.ran == ["hello"]                       # neither refused call reached Claude


@pytest.mark.parametrize("path", ["/solid-gold", "/workshop", "/workshop/cancel/abc"])
def test_a_refusal_reaches_the_client_even_with_a_big_unread_body(door, path):
    """The refusal is sent before the body is read. Closing a socket with unread data resets the connection on Windows,
    and the client saw WinError 10053 instead of the 401 (a flaky test; to the reporter it would have read as 'bridge
    down'). The handler drains the body first, so every one of these must come back as a clean 401."""
    body = {"text": "x" * 400_000, "mode": "bypass", "message": "y", "task": "aa", "conv": "c"}
    for _ in range(15):
        assert door(path, body, token="wrong")[0] == 401
        assert door(path, body, token=None)[0] == 401
    assert door.ran == []
