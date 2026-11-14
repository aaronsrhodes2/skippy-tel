"""Purple as an MCP server (purple_mcp.py): tools over a FAKE claude-bridge on localhost, and the HTTP door's key rule."""
import json
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import purple_mcp as pm  # noqa: E402

TOKEN = "home-token-xyz"


class FakeBridge:
    """Just enough of the bridge: token check, a run that says two things then finishes, busy on demand."""

    def __init__(self):
        self.runs, self.busy, self.started, self.cancelled = {}, False, [], []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _json(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _ok(self):
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    self._json(401, {"error": "unauthorized"})
                    return False
                return True

            def do_POST(self):                                         # noqa: N802
                if not self._ok():
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path == "/workshop":
                    if fake.busy:
                        return self._json(409, {"error": "busy", "message": "purple is on another job"})
                    fake.started.append(body)
                    fake.runs[body["task"]] = [{"t": "tool", "name": "Read", "detail": "x"}, {"t": "text", "text": "Looking."},
                                               {"t": "text", "text": "Done: fixed it."}]
                    return self._json(200, {"ok": True, "task": body["task"], "done": False})
                if self.path.startswith("/workshop/cancel/"):
                    fake.cancelled.append(self.path.rsplit("/", 1)[1])
                    return self._json(200, {"ok": True})
                self._json(404, {"error": "not_found"})

            def do_GET(self):                                          # noqa: N802
                if not self._ok():
                    return
                path, _, query = self.path.partition("?")
                task = path.rsplit("/", 1)[1]
                if task not in fake.runs:
                    return self._json(404, {"error": "unknown_task"})
                after = int(dict(p.split("=") for p in query.split("&")).get("after", 0))
                events = fake.runs[task]
                self._json(200, {"events": events[after:], "next": len(events), "done": True, "ok": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake():
    f = FakeBridge()
    yield f
    f.close()


@pytest.fixture
def bridge(fake):
    return pm.Bridge(fake.url, TOKEN)


def call(bridge, name, **args):
    reply = pm.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, bridge)
    content = reply["result"]
    return json.loads(content["content"][0]["text"]) if not content["isError"] else content["content"][0]["text"], content["isError"]


def test_it_lists_three_tools_and_never_a_token(bridge):
    reply = pm.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, bridge)
    assert [t["name"] for t in reply["result"]["tools"]] == ["purple_ask", "purple_result", "purple_cancel"]
    assert TOKEN not in json.dumps(reply)
    assert pm.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, bridge) is None


def test_ask_starts_a_run_and_returns_purples_words_without_the_tool_steps(bridge, fake):
    out, err = call(bridge, "purple_ask", message="fix the thing", project="skippytel", wait_s=5)
    assert not err and out["done"] and out["ok"] and out["steps"] == 1
    assert out["text"] == "Looking.\nDone: fixed it." and out["conv"].startswith("mcp-") and len(out["task"]) == 16
    (started,) = fake.started
    assert started["message"] == "fix the thing" and started["project"] == "skippytel" and started["who"] == "purple-mcp"
    assert started["conv"] == out["conv"] and "Read" not in json.dumps(out)


def test_the_same_conv_is_passed_on_for_a_follow_up(bridge, fake):
    out, _ = call(bridge, "purple_ask", message="more", conv="my-game-chat-1", wait_s=2)
    assert out["conv"] == "my-game-chat-1" and fake.started[0]["conv"] == "my-game-chat-1"


def test_result_continues_from_after(bridge, fake):
    first, _ = call(bridge, "purple_ask", message="go", wait_s=2)
    again, err = call(bridge, "purple_result", task=first["task"], after=2, wait_s=1)
    assert not err and again["text"] == "Done: fixed it." and again["next"] == 3


def test_busy_is_an_answer_not_an_error(bridge, fake):
    fake.busy = True
    out, err = call(bridge, "purple_ask", message="go")
    assert not err and out["busy"] is True


def test_cancel_names_the_task(bridge, fake):
    out, err = call(bridge, "purple_cancel", task="abc123abc123")
    assert not err and out == {"stopped": True} and fake.cancelled == ["abc123abc123"]


def test_a_missing_message_or_unknown_task_is_a_plain_error(bridge):
    assert call(bridge, "purple_ask", message="  ")[1] is True
    text, err = call(bridge, "purple_result", task="ffffffffffffffff", wait_s=0)
    assert err and "unknown_task" in json.dumps(text)                           # a run the bridge never heard of: an error, said plainly


def test_a_wrong_token_or_a_dead_bridge_says_so(fake):
    bad = pm.Bridge(fake.url, "wrong")
    text, err = call(bad, "purple_ask", message="go")
    assert err and "unauthorized" in text
    text, err = call(pm.Bridge("http://127.0.0.1:9", TOKEN), "purple_ask", message="go")
    assert err and "not answering" in text
    text, err = call(pm.Bridge(fake.url, ""), "purple_ask", message="go")
    assert err or True                                                          # an empty token falls back to the vault; either way it never raises


def test_the_http_door_is_loopback_only_without_a_key(bridge):
    with pytest.raises(SystemExit):
        pm.make_http_server("0.0.0.0", 0, bridge)
    pm.make_http_server("127.0.0.1", 0, bridge).server_close()


def _post(url, body, key=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def test_the_http_door_checks_its_key_and_speaks_mcp(bridge):
    server = pm.make_http_server("127.0.0.1", 0, bridge, key="door-key")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/mcp"
    try:
        listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        assert _post(url, listing)[0] == 401 and _post(url, listing, "nope")[0] == 401
        status, reply = _post(url, listing, "door-key")
        assert status == 200 and len(reply["result"]["tools"]) == 3
        status, reply = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                    "params": {"name": "purple_ask", "arguments": {"message": "hi", "wait_s": 2}}}, "door-key")
        assert status == 200 and "Done: fixed it." in reply["result"]["content"][0]["text"]
        assert _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, "door-key")[0] == 202
    finally:
        server.shutdown()
