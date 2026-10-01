"""The bridge's only door is the private Skippy's (POST /workshop, GET /workshop/run/<task>, GET /workshop/runs,
POST /workshop/cancel/<task>). It runs Claude Code with every permission check skipped and listens on 0.0.0.0 so Docker
can reach it, so every route wants the home token, and with none configured it stays shut (Platinum section 19).

The old POST /solid-gold door ("trust the awesomeness" / "solid gold" voice tiers) was removed on 2026-09-30 at the
Captain's request. These tests stand up the real HTTP handler and prove: no token, no Claude; and that door is gone."""
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
    started = []
    monkeypatch.setattr(bridge.WORKSHOP, "start", lambda *a, **k: started.append(a) or (_ for _ in ()).throw(ValueError("x")))
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

    call.started, call.monkeypatch = started, monkeypatch
    yield call
    server.shutdown()


BODY = {"task": "abcdef12", "conv": "conv-12345678", "message": "hi"}


@pytest.mark.parametrize("token", [None, "wrong", "door-toke", "door-token-and-more", ""])
def test_claude_never_starts_without_the_token(door, token):
    assert door("/workshop", BODY, token=token)[0] == 401
    assert door("/workshop/runs", token=token, method="GET")[0] == 401
    assert door.started == []


def test_with_no_token_configured_the_whole_bridge_stays_shut(door):
    door.monkeypatch.setattr(bridge, "_token_cache", (time.time() + 3600, ""))
    door.monkeypatch.setattr(bridge.workshop, "home_token", lambda *a, **k: "")
    status, out = door("/workshop", BODY)
    assert status == 503 and out["error"] == "bridge_disabled" and door.started == []


def test_the_solid_gold_door_is_gone_even_with_the_right_token(door):
    for body in ({"text": "list the files", "mode": "bypass"}, {"text": "x", "mode": "plan"}, {"text": "x", "stream": True}):
        assert door("/solid-gold", body)[0] == 404
    assert not hasattr(bridge, "run_oneshot") and not hasattr(bridge, "build_cmd") and not hasattr(bridge, "AWE_MODEL")


def test_health_stays_open_for_the_liveness_ping_and_never_shows_the_token(door):
    status, out = door("/health", token=None, method="GET")
    assert status == 200 and out["service"] == "claude-bridge" and "awe_model" not in out
    assert "door-token" not in json.dumps(out)


@pytest.mark.parametrize("path", ["/workshop", "/workshop/cancel/abc"])
def test_a_refusal_reaches_the_client_even_with_a_big_unread_body(door, path):
    """The refusal is sent before the body is read. Closing a socket with unread data resets the connection on Windows,
    and the client saw WinError 10053 instead of the 401 (a flaky test; to the reporter it would have read as 'bridge
    down'). The handler drains the body first, so every one of these must come back as a clean 401."""
    body = {"text": "x" * 400_000, "message": "y", "task": "aa", "conv": "c"}
    for _ in range(15):
        assert door(path, body, token="wrong")[0] == 401
        assert door(path, body, token=None)[0] == 401
    assert door.started == []
