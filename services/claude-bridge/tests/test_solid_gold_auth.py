"""/solid-gold runs Claude Code (plan = read the whole tree, bypass = every permission check skipped) and the bridge
listens on 0.0.0.0 so Docker can reach it. It used to take no authentication (the README's own "Security note"); it now
wants the same bearer token as the workshop, and with no token configured it stays shut (Platinum section 19, condition 1).

These tests stand up the real HTTP door and prove the one thing that matters: Claude does not run without the token."""
import importlib.util
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


@pytest.fixture
def door(monkeypatch):
    ran = []

    def fake_run(prompt, mode):
        ran.append((prompt, mode))
        return {"output": "ran", "returncode": 0}

    monkeypatch.setattr(bridge, "run_oneshot", fake_run)
    monkeypatch.setattr(bridge, "_token_cache", (time.time() + 3600, "door-token"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(path, body=None, token="door-token", method="POST"):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        req = urllib.request.Request(base + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    call.ran, call.base, call.monkeypatch = ran, base, monkeypatch
    yield call
    server.shutdown()


@pytest.mark.parametrize("mode", ["plan", "bypass"])
def test_claude_never_runs_without_the_token(door, mode):
    body = {"text": "list the files", "mode": mode}
    assert door("/solid-gold", body, token=None)[0] == 401                   # no header
    assert door("/solid-gold", body, token="wrong")[0] == 401                # wrong token
    assert door("/solid-gold", body, token="door-toke")[0] == 401            # a prefix of the right one
    assert door("/solid-gold", body, token="door-token-and-more")[0] == 401
    assert door("/solid-gold", body, token="")[0] == 401
    assert door.ran == []                                                     # the runner was never reached, not once


@pytest.mark.parametrize("mode", ["plan", "bypass"])
def test_the_right_token_opens_it_in_both_modes(door, mode):
    status, out = door("/solid-gold", {"text": "list the files", "mode": mode})
    assert status == 200 and out["output"] == "ran"
    assert door.ran == [("list the files", mode)]


def test_with_no_token_configured_the_whole_bridge_stays_shut(door):
    door.monkeypatch.setattr(bridge, "_token_cache", (time.time() + 3600, ""))
    status, out = door("/solid-gold", {"text": "x", "mode": "bypass"})
    assert status == 503 and out["error"] == "bridge_disabled" and door.ran == []
    assert door("/solid-gold", {"text": "x"}, token=None)[0] == 503           # shut for everyone, header or not
    assert door("/workshop", {"task": "t", "conv": "c", "message": "m"})[0] == 503


def test_bad_input_is_refused_only_after_the_token_is_checked(door):
    assert door("/solid-gold", {"text": "", "mode": "plan"})[0] == 400        # authorised, but nothing to do
    assert door("/solid-gold", {"text": "x", "mode": "root"})[0] == 400
    assert door("/solid-gold", {"text": "", "mode": "plan"}, token="wrong")[0] == 401   # a stranger learns nothing about the input rules


def test_health_stays_open_for_the_liveness_ping_and_never_shows_the_token(door):
    status, out = door("/health", token=None, method="GET")
    assert status == 200 and out["service"] == "claude-bridge"
    assert "door-token" not in json.dumps(out)


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
