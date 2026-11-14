"""Purple Skippy as an MCP server: any model or application (Claude Desktop, a game, another agent) hands purple a task and reads back what it did.

Purple is the private Skippy: Claude Code on skippy-pc behind the claude-bridge (`POST /workshop`, `GET /workshop/run/<task>`), full permissions,
local models (Core's coder) by default. The bridge wants the home token; THIS server holds it, so the caller never sees it.

Tools (three, small on purpose):
    purple_ask(message, conv?, project?, wait_s?)   start a run (or follow up in `conv`); waits up to wait_s for it, returns what purple said so far
    purple_result(task, after?, wait_s?)            keep reading a run: new text since `after`, whether it is done and ok
    purple_cancel(task)                             stop a run

Transports (stdlib only, no install):
    stdio   python purple_mcp.py                       Claude starts it per session:  claude mcp add --scope user purple -- python <this file>
    http    python purple_mcp.py --http 47860          MCP Streamable HTTP, stateless, JSON replies: POST /mcp. A game or a remote client uses this.
            Binds 127.0.0.1. Anywhere else (--host 0.0.0.0, or the tailnet address) it REFUSES to start without PURPLE_MCP_KEY, and then every
            call must carry `Authorization: Bearer <PURPLE_MCP_KEY>`. Purple has every permission: this key is the only door.

Settings (environment): PURPLE_BRIDGE_URL (default http://127.0.0.1:9337), SKIPPY_HOME_TOKEN (else read from the vault .env like the bridge does),
PURPLE_MCP_KEY (the HTTP door's key), PURPLE_MCP_PORT.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROTOCOL = "2025-03-26"
MAX_WAIT_S = 100.0              # one tool call never blocks longer than this (the bridge answers a poll in at most 25 s)
POLL_S = 25.0
MAX_TEXT = 30_000               # what one reply carries back; `after` pages the rest

TOOLS = [
    {"name": "purple_ask",
     "description": ("Give purple Skippy (the private Skippy: an agent that can read and change the Captain's projects, run commands, test, commit "
                     "and deploy) a task, in plain words. Waits up to wait_s seconds and returns what purple said so far plus a `task` id. "
                     "A long job will not be done when this returns: call purple_result with the task id until done is true. "
                     "Pass the same `conv` again to follow up with the context of the last turn. One job at a time: 'busy' means wait and ask again."),
     "inputSchema": {"type": "object", "properties": {
         "message": {"type": "string", "description": "What purple should do, or the follow-up."},
         "conv": {"type": "string", "description": "A conversation id (8-64 letters, digits, dashes). Same id = same context. Omit for a new one; it is returned."},
         "project": {"type": "string", "description": "A project id from purple's registry (default: skippytel)."},
         "wait_s": {"type": "number", "description": f"Seconds to wait for an answer, 0-{int(MAX_WAIT_S)} (default 30)."}},
         "required": ["message"]}},
    {"name": "purple_result",
     "description": "Read more of a purple run: the text since `after` (0 = from the start), `next` (pass it as `after` next time), done and ok. Waits up to wait_s for news.",
     "inputSchema": {"type": "object", "properties": {
         "task": {"type": "string", "description": "The task id purple_ask returned."},
         "after": {"type": "integer", "description": "Event index to continue from (default 0)."},
         "wait_s": {"type": "number", "description": f"Seconds to wait, 0-{int(MAX_WAIT_S)} (default 30)."}},
         "required": ["task"]}},
    {"name": "purple_cancel",
     "description": "Stop a purple run that is going (the task id from purple_ask).",
     "inputSchema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}},
]

ENV_FILES = [r"G:\My Drive\vault\.env", r"D:\Aaron\development\.env"]


def home_token(env=os.environ, files=ENV_FILES) -> str:
    """SKIPPY_HOME_TOKEN from the environment, else the vault .env (the bridge's own order)."""
    token = (env.get("SKIPPY_HOME_TOKEN") or "").strip()
    if token:
        return token
    for path in files:
        try:
            for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("SKIPPY_HOME_TOKEN="):
                    return line.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            continue
    return ""


class Bridge:
    """The claude-bridge's workshop doors, with the home token. Every method returns (status, json); a bridge that is down is a status 0, said in words."""

    def __init__(self, url: str | None = None, token: str | None = None, timeout: float = 40.0):
        self.url = (url or os.environ.get("PURPLE_BRIDGE_URL") or "http://127.0.0.1:9337").rstrip("/")
        self._token, self.timeout = token, timeout

    def _call(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> tuple[int, dict]:
        token = self._token if self._token is not None else home_token()
        if not token:
            return 503, {"error": "no_token", "message": "SKIPPY_HOME_TOKEN is not set on this PC, so the bridge (and purple) stays shut."}
        req = urllib.request.Request(self.url + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:      # noqa: S310 — the bridge on this PC
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {"error": f"http_{e.code}"}
        except Exception as e:                                                          # noqa: BLE001 — said, never raised
            return 0, {"error": "unreachable", "message": f"The claude-bridge is not answering ({type(e).__name__}); is it running on skippy-pc?"}

    def start(self, task: str, conv: str, message: str, project: str = "") -> tuple[int, dict]:
        body = {"task": task, "conv": conv, "message": message, "who": "purple-mcp"}
        if project:
            body["project"] = project
        return self._call("POST", "/workshop", body)

    def poll(self, task: str, after: int, wait: float) -> tuple[int, dict]:
        return self._call("GET", f"/workshop/run/{task}?after={int(after)}&wait={min(POLL_S, max(0.0, wait)):g}", timeout=wait + 15)

    def cancel(self, task: str) -> tuple[int, dict]:
        return self._call("POST", f"/workshop/cancel/{task}", {})


def _wait(value, default: float = 30.0) -> float:
    try:
        return max(0.0, min(MAX_WAIT_S, float(value if value is not None else default)))
    except (TypeError, ValueError):
        return default


def _digest(events: list[dict]) -> tuple[str, int]:
    """What the caller needs of a run's events: purple's own words, and how many tool steps it took (the steps' commands stay with the bridge)."""
    text = "\n".join(str(e.get("text") or "") for e in events if e.get("t") == "text").strip()
    return text, sum(1 for e in events if e.get("t") == "tool")


def _read(bridge: Bridge, task: str, after: int, wait: float, clock=None) -> dict:
    """Poll until the run is done, there is news and the wait is up, or the wait is spent. Returns the tool's result object."""
    import time                                                                         # noqa: PLC0415
    now = clock or time.monotonic
    deadline, texts, steps, cursor, last = now() + wait, [], 0, after, {}
    while True:
        left = max(0.0, deadline - now())
        status, last = bridge.poll(task, cursor, min(POLL_S, left))
        if status != 200:
            return {"task": task, "ok": False, "done": True, "error": last.get("message") or last.get("error") or f"http {status}"}
        said, tools = _digest(last.get("events") or [])
        if said:
            texts.append(said)
        steps += tools
        cursor = int(last.get("next", cursor))
        if last.get("done") or now() >= deadline:
            break
        if texts and not last.get("events"):
            break                                                                       # said something, then a quiet poll: hand it over
    out = {"task": task, "text": "\n".join(texts)[:MAX_TEXT], "steps": steps, "next": cursor, "done": bool(last.get("done"))}
    if out["done"]:
        out["ok"] = bool(last.get("ok"))
        if last.get("error"):
            out["error"] = last["error"]
    return out


def call_tool(name: str, args: dict, bridge: Bridge, clock=None) -> tuple[str, bool]:
    """One tools/call: (text for the model, isError)."""
    if name == "purple_ask":
        message = str(args.get("message") or "").strip()
        if not message:
            return "Say what purple should do (message).", True
        conv = str(args.get("conv") or "").strip() or "mcp-" + secrets.token_hex(6)
        task = secrets.token_hex(8)
        status, body = bridge.start(task, conv, message, str(args.get("project") or "").strip())
        if status == 409:
            return json.dumps({"busy": True, "message": body.get("message") or "Purple is on another job; ask again shortly."}), False
        if status != 200:
            return body.get("message") or body.get("error") or f"The bridge answered {status}.", True
        out = _read(bridge, task, 0, _wait(args.get("wait_s")), clock)
        out["conv"] = conv
        return json.dumps(out), bool(out.get("error")) and not out.get("text")
    if name == "purple_result":
        task = str(args.get("task") or "").strip()
        if not task:
            return "Which task? (the id purple_ask returned)", True
        out = _read(bridge, task, int(args.get("after") or 0), _wait(args.get("wait_s")), clock)
        return json.dumps(out), bool(out.get("error")) and not out.get("text")
    if name == "purple_cancel":
        status, body = bridge.cancel(str(args.get("task") or "").strip())
        return json.dumps({"stopped": bool(body.get("ok")) and status == 200}), status != 200
    return f"No tool called {name}.", True


def handle(msg: dict, bridge: Bridge, clock=None) -> dict | None:
    """One JSON-RPC message in, its reply out (None for a notification)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "purple-skippy", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        text, is_error = call_tool(str(params.get("name") or ""), dict(params.get("arguments") or {}), bridge, clock)
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve_stdio(bridge: Bridge, stdin=sys.stdin, stdout=sys.stdout) -> None:
    for line in stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle(msg, bridge) if isinstance(msg, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


def is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def make_http_server(host: str, port: int, bridge: Bridge, key: str = "") -> ThreadingHTTPServer:
    """MCP Streamable HTTP, stateless: POST /mcp answers JSON. Refuses to exist off loopback without a key."""
    if not is_loopback(host) and not key:
        raise SystemExit("Refusing to listen beyond this PC without PURPLE_MCP_KEY: purple has every permission.")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):                                                       # quiet
            pass

        def _send(self, code: int, obj) -> None:
            raw = b"" if obj is None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authorised(self) -> bool:
            if not key:
                return True
            got = (self.headers.get("Authorization") or "")[7:].strip() if (self.headers.get("Authorization") or "").startswith("Bearer ") else ""
            return hmac.compare_digest(got.encode(), key.encode())

        def do_GET(self):                                                                # noqa: N802
            self._send(200 if self.path == "/health" else 405, {"ok": True, "service": "purple-mcp"} if self.path == "/health" else {"error": "use POST /mcp"})

        def do_POST(self):                                                               # noqa: N802
            if self.path.split("?")[0] != "/mcp":
                return self._send(404, {"error": "not_found"})
            if not self._authorised():
                return self._send(401, {"error": "unauthorized"})
            try:
                msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            if isinstance(msg, list):                                                    # a batch
                replies = [r for r in (handle(m, bridge) for m in msg if isinstance(m, dict)) if r is not None]
                return self._send(200 if replies else 202, replies or None)
            reply = handle(msg, bridge) if isinstance(msg, dict) else None
            self._send(200 if reply is not None else 202, reply)

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str]) -> None:
    import argparse                                                                      # noqa: PLC0415
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--http", type=int, nargs="?", const=int(os.environ.get("PURPLE_MCP_PORT", "47860")), help="serve MCP over HTTP on this port (default: stdio)")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)
    bridge = Bridge()
    if args.http is None:
        serve_stdio(bridge)
    else:
        server = make_http_server(args.host, args.http, bridge, os.environ.get("PURPLE_MCP_KEY", ""))
        print(f"purple-mcp on http://{args.host}:{args.http}/mcp", file=sys.stderr, flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
