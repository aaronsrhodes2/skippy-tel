"""The Captain's private MCP servers for /me: listed with no secrets, tested by name only, and behind the home token."""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "claude-bridge"))

import bridge  # noqa: E402
import mcp_access  # noqa: E402

FAKE_STDIO = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    if msg["method"] == "initialize":
        res = {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "fake", "version": "9"}}
    elif msg["method"] == "tools/list":
        print("a stray log line on stdout", flush=True)
        res = {"tools": [{"name": "alpha"}, {"name": "beta"}]}
    else:
        res = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
'''
SILENT = "import time\ntime.sleep(60)\n"


class FakeHttpMcp(BaseHTTPRequestHandler):
    seen_auth = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeHttpMcp.seen_auth.append(self.headers.get("Authorization"))
        if "id" not in body:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        res = {"serverInfo": {"name": "web", "version": "1"}} if body["method"] == "initialize" else {"tools": [{"name": "ping"}]}
        out = ("event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": res}) + "\n\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def config(tmp_path, monkeypatch):
    fake = tmp_path / "fake_mcp.py"
    fake.write_text(FAKE_STDIO)
    silent = tmp_path / "silent_mcp.py"
    silent.write_text(SILENT)
    http = ThreadingHTTPServer(("127.0.0.1", 0), FakeHttpMcp)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    cfg = tmp_path / "claude.json"
    cfg.write_text(json.dumps({"mcpServers": {
        "fake": {"command": sys.executable, "args": [str(fake), "--secret-arg"], "env": {"API_KEY": "sk-should-never-leave"}},
        "silent": {"command": sys.executable, "args": [str(silent)]},
        "broken": {"command": str(tmp_path / "no-such-program")},
        "web": {"type": "http", "url": f"http://127.0.0.1:{http.server_port}/mcp", "headers": {"Authorization": "Bearer web-secret"}},
    }, "projects": {}}))
    monkeypatch.setenv("CLAUDE_CONFIG", str(cfg))
    yield cfg
    http.shutdown()


def test_the_list_names_each_server_and_how_it_is_reached_and_never_a_secret(config):
    got = mcp_access.listing()["servers"]
    assert [s["name"] for s in got] == ["broken", "fake", "silent", "web"]
    web = next(s for s in got if s["name"] == "web")
    assert web == {"name": "web", "transport": "http", "via": "127.0.0.1"}
    fake = next(s for s in got if s["name"] == "fake")
    assert fake["transport"] == "stdio" and fake["via"].lower().startswith("python")
    text = json.dumps(got)
    for secret in ("sk-should-never-leave", "web-secret", "--secret-arg", "API_KEY", "Authorization"):
        assert secret not in text


def test_a_missing_or_garbled_config_is_an_empty_list_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG", str(tmp_path / "nothing.json"))
    assert mcp_access.listing() == {"servers": []}
    (tmp_path / "bad.json").write_text("{not json")
    monkeypatch.setenv("CLAUDE_CONFIG", str(tmp_path / "bad.json"))
    assert mcp_access.listing() == {"servers": []}


def test_testing_a_stdio_server_does_the_handshake_and_lists_its_tools(config):
    status, body = mcp_access.test("fake", seconds=20)
    assert status == 200 and body["ok"] is True and body["tools"] == ["alpha", "beta"] and body["server"] == "fake 9" and body["ms"] >= 0
    assert "sk-should-never-leave" not in json.dumps(body)


def test_testing_an_http_server_uses_its_headers_but_never_returns_them(config):
    FakeHttpMcp.seen_auth.clear()
    status, body = mcp_access.test("web", seconds=20)
    assert status == 200 and body["ok"] is True and body["tools"] == ["ping"] and body["server"] == "web 1"
    assert "Bearer web-secret" in FakeHttpMcp.seen_auth and "web-secret" not in json.dumps(body)


def test_a_server_that_will_not_start_or_never_answers_is_a_reason_not_a_hang(config):
    status, body = mcp_access.test("broken", seconds=5)
    assert status == 200 and body["ok"] is False and body["error"]
    started = time.monotonic()
    status, body = mcp_access.test("silent", seconds=2)
    assert body["ok"] is False and "no answer" in body["error"] and time.monotonic() - started < 6


def test_only_a_configured_name_can_be_tested(config):
    status, body = mcp_access.test("rm -rf /")
    assert status == 404 and body["ok"] is False
    assert mcp_access.test("")[0] == 404


def test_the_bridge_doors_want_the_home_token(config, monkeypatch):
    import urllib.error
    import urllib.request
    monkeypatch.setattr(bridge, "_token_cache", (time.time() + 3600, "door-token"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    def call(path, body=None, token="door-token"):
        req = urllib.request.Request(base + path, method="POST" if body is not None else "GET", data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    try:
        for token in (None, "wrong", ""):
            assert call("/mcp/list", token=token)[0] == 401 and call("/mcp/test", {"name": "fake"}, token=token)[0] == 401
        assert [s["name"] for s in call("/mcp/list")[1]["servers"]] == ["broken", "fake", "silent", "web"]
        assert call("/mcp/test", {"name": "fake"})[1]["tools"] == ["alpha", "beta"]
        assert call("/mcp/test", {"name": "nope"})[0] == 404
    finally:
        server.shutdown()
