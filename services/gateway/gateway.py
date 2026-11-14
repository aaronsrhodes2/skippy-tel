"""The Skippy gateway (the Captain, 2026-11-13): ONE MCP address in front of every door the house has, for a game mod, a hub, another agent
or any MCP client. It proxies; it thinks about nothing.

    POST http://127.0.0.1:47863/mcp      MCP Streamable HTTP, stateless, JSON replies         GET /health

Backends (each its own MCP door; a missing or dead one is just absent for now, never an error for the others):
    purple   the development agent (services/purple-mcp, :47860): purple_ask / purple_result / purple_cancel. Slow: it works in jobs.
    house    the house (services/satellite/house_mcp.py, :47861): lights, switches, music, speakers, Spotify, texts, vision moments.
    mentat   physics, from skippy-core (:7350): ask / find_tools / call_tool.
Override or add with GATEWAY_BACKENDS (JSON {"name": {"url", "key"?}}); keys for the first two also come from PURPLE_MCP_KEY / HOUSE_MCP_KEY.

Tools listed (short on purpose: Mentat's 220 tools once cost a chat ~73k tokens; GATEWAY_FULL=1 lists every backend tool):
    find_tools(query?, backend?)    names and one-line descriptions of every backend tool, filtered
    call_tool(name, arguments?)     run any backend tool by name (or "backend.name" when two share one)
    backends()                      which doors answer right now
Any real tool name also works directly in tools/call. Rules of the doors stay: a lock or garage door never completes from text (the house door
drops `confirm`); off loopback the gateway REFUSES to start without GATEWAY_KEY (Authorization: Bearer ...).
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
import urllib.request

PROTOCOL = "2025-03-26"
DEFAULT_BACKENDS = {
    "purple": {"url": "http://127.0.0.1:47860/mcp", "key_env": "PURPLE_MCP_KEY"},
    "house": {"url": "http://127.0.0.1:47861/mcp", "key_env": "HOUSE_MCP_KEY"},
    "mentat": {"url": "http://skippy-core:7350/mcp"},
}
LIST_TTL_S = 60.0
CALL_TIMEOUT_S = 130.0              # purple_ask may wait up to 100 s inside its own tool
LISTED = [
    {"name": "find_tools", "description": "List the tools of every Skippy door (house, purple, mentat): name and one line each. Filter with a word in `query` or one door in `backend`.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "backend": {"type": "string"}}}},
    {"name": "call_tool", "description": "Run any tool found with find_tools. `name` is the tool's name (or backend.name); `arguments` is its argument object.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["name"]}},
    {"name": "backends", "description": "Which doors answer right now, and how many tools each has.", "inputSchema": {"type": "object", "properties": {}}},
]


def load_backends(env=None) -> dict:
    env = os.environ if env is None else env
    out = {}
    for name, spec in DEFAULT_BACKENDS.items():
        out[name] = {"url": env.get(f"GATEWAY_{name.upper()}_URL") or spec["url"], "key": env.get(spec.get("key_env", ""), "") if spec.get("key_env") else ""}
    try:
        for name, spec in (json.loads(env.get("GATEWAY_BACKENDS") or "{}") or {}).items():
            if isinstance(spec, dict) and spec.get("url"):
                out[str(name)] = {"url": str(spec["url"]), "key": str(spec.get("key") or "")}
    except ValueError:
        pass
    return out


def rpc(url: str, key: str, method: str, params: dict | None = None, timeout: float = 15.0):
    """One JSON-RPC call to a backend door; the `result`, or a RuntimeError with its words."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                                          **({"Authorization": f"Bearer {key}"} if key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:                                     # noqa: S310 — a configured door
        raw = r.read().decode("utf-8", "replace")
    if raw.lstrip().startswith("event:") or raw.lstrip().startswith("data:"):                    # an SSE-framed reply: take the last data line
        raw = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")][-1]
    reply = json.loads(raw)
    if reply.get("error"):
        raise RuntimeError(str((reply["error"] or {}).get("message") or reply["error"]))
    return reply.get("result")


class Gateway:
    def __init__(self, backends: dict | None = None, rpc_fn=rpc, now=time.monotonic):
        self.backends, self.rpc, self.now = backends if backends is not None else load_backends(), rpc_fn, now
        self._catalog: dict = {}                    # backend -> (fetched_at, tools | None)
        self._lock = threading.Lock()

    def _tools_of(self, name: str) -> list[dict] | None:
        """A backend's tools (cached LIST_TTL_S), or None while it does not answer."""
        with self._lock:
            hit = self._catalog.get(name)
            if hit and self.now() - hit[0] < LIST_TTL_S:
                return hit[1]
        spec = self.backends[name]
        try:
            tools = list((self.rpc(spec["url"], spec["key"], "tools/list", None, 8.0) or {}).get("tools") or [])
        except Exception:                           # noqa: BLE001 — a dead door is simply absent
            tools = None
        with self._lock:
            self._catalog[name] = (self.now(), tools)
        return tools

    def catalog(self) -> dict[str, tuple[str, dict]]:
        """tool name -> (backend, tool). The first backend (in order) to offer a name owns it; `backend.name` always reaches a given one."""
        out: dict[str, tuple[str, dict]] = {}
        for backend in self.backends:
            for t in self._tools_of(backend) or []:
                n = str(t.get("name") or "")
                if n:
                    out.setdefault(n, (backend, t))
                    out[f"{backend}.{n}"] = (backend, t)
        return out

    def _resolve(self, name: str):
        return self.catalog().get(name)

    def find(self, query: str = "", backend: str = "") -> list[dict]:
        q = (query or "").lower().split()
        rows = []
        for backend_name in self.backends:
            if backend and backend_name != backend:
                continue
            for t in self._tools_of(backend_name) or []:
                text = f"{t.get('name', '')} {t.get('description', '')}".lower()
                if all(w in text for w in q):
                    rows.append({"backend": backend_name, "name": t.get("name"), "description": " ".join(str(t.get("description") or "").split())[:200],
                                 "arguments": sorted(((t.get("inputSchema") or {}).get("properties") or {}).keys())})
        return rows

    def call(self, name: str, arguments: dict) -> dict:
        found = self._resolve(name)
        if not found:
            return {"content": [{"type": "text", "text": f"No door has a tool called '{name}'. Use find_tools to see them."}], "isError": True}
        backend, tool = found
        spec = self.backends[backend]
        try:
            return self.rpc(spec["url"], spec["key"], "tools/call", {"name": tool["name"], "arguments": arguments or {}}, CALL_TIMEOUT_S) or {}
        except Exception as e:                      # noqa: BLE001 — said in words, never raised
            with self._lock:
                self._catalog.pop(backend, None)    # look at that door again next time
            return {"content": [{"type": "text", "text": f"The {backend} door is not answering right now ({type(e).__name__}: {e})."}], "isError": True}

    def status(self) -> dict:
        return {name: {"url": spec["url"], "up": (tools := self._tools_of(name)) is not None, "tools": len(tools or [])} for name, spec in self.backends.items()}

    def tool_list(self) -> list[dict]:
        if os.environ.get("GATEWAY_FULL") == "1":
            return [t for n, (_, t) in self.catalog().items() if "." not in n]
        return LISTED

    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None
        if method == "initialize":
            result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "skippy-gateway", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": self.tool_list()}
        elif method == "tools/call":
            params = msg.get("params") or {}
            name, args = str(params.get("name") or ""), dict(params.get("arguments") or {})
            if name == "find_tools":
                result = {"content": [{"type": "text", "text": json.dumps(self.find(str(args.get("query") or ""), str(args.get("backend") or "")), indent=1)}], "isError": False}
            elif name == "backends":
                result = {"content": [{"type": "text", "text": json.dumps(self.status(), indent=1)}], "isError": False}
            elif name == "call_tool":
                inner = str(args.get("name") or "")
                result = self.call(inner, dict(args.get("arguments") or {})) if inner else \
                    {"content": [{"type": "text", "text": "call_tool needs a `name`."}], "isError": True}
            else:
                result = self.call(name, args)
        elif method == "ping":
            result = {}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def make_http_server(host: str, port: int, gateway: Gateway, key: str = ""):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    if host not in ("127.0.0.1", "localhost", "::1") and not key:
        raise SystemExit("Refusing to listen beyond this PC without GATEWAY_KEY: the gateway reaches the house and purple.")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
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
            head = self.headers.get("Authorization") or ""
            return head.startswith("Bearer ") and hmac.compare_digest(head[7:].strip().encode(), key.encode())

        def do_GET(self):                                           # noqa: N802
            self._send(200, {"ok": True, "service": "skippy-gateway", "backends": gateway.status()}) if self.path == "/health" else self._send(405, {"error": "use POST /mcp"})

        def do_POST(self):                                          # noqa: N802
            if self.path.split("?")[0] != "/mcp":
                return self._send(404, {"error": "not_found"})
            if not self._authorised():
                return self._send(401, {"error": "unauthorized"})
            try:
                msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            reply = gateway.handle(msg) if isinstance(msg, dict) else None
            self._send(200 if reply is not None else 202, reply)

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str]) -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, nargs="?", const=int(os.environ.get("GATEWAY_PORT", "47863")), default=int(os.environ.get("GATEWAY_PORT", "47863")))
    ap.add_argument("--host", default=os.environ.get("GATEWAY_HOST", "127.0.0.1"))
    args = ap.parse_args(argv)
    server = make_http_server(args.host, args.http, Gateway(), os.environ.get("GATEWAY_KEY", ""))
    print(f"skippy-gateway on http://{args.host}:{args.http}/mcp", file=sys.stderr, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
