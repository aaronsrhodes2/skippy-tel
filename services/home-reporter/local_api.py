"""A loopback-only door for the private Skippy's tools on skippy-pc (services/purple-tools): run one of the QUICK pop-ups (docs/popups.md,
`box: pc`) and get its result straight back, instead of through the portal's queue and a card.

The reporter owns the GPU lock and the pop-up registry, so a tool call goes through the same code as a portal job (`call_popup`: the lock,
the card made free, the type check), and a song, a picture and a pop-up can never share the card. Compose publishes the port on 127.0.0.1
only: a process on this PC may ask, nothing else can. The slow ones (`box: core`, minutes of Core's card) are refused here on purpose: they
wait for the box to be quiet through the portal's patient queue (a `SKIPPY_POPUP:` line), not while the agent that asked is still running.

    GET  /popups              the quick tools: name, description, inputs, what they take and give, typical seconds
    POST /popup/<name>        {"args": {...}, "input": {"type", "name", "b64"}?}  ->  {"type", "b64"}  or  {"error"}
"""
from __future__ import annotations

import base64
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 3_000_000                      # a picture of about 1 MB, base64 (docs/popups.md)
NAME = re.compile(r"[a-z][a-z0-9_]{1,60}")
SLOW = ("that one runs on skippy-core and takes minutes of its card: ask for it with a SKIPPY_POPUP: line, "
        "which waits for the box to be quiet and puts the result in the chat")


def public(entry: dict) -> dict:
    return {k: entry.get(k) for k in ("name", "description", "inputs", "media_in", "media_required", "outputs", "est_seconds", "vram_gb", "mem_gb", "box", "tier")}


def listing(registry: dict) -> list[dict]:
    return [public(e) for _, e in sorted(registry.items()) if e.get("box") == "pc"]


def run(name: str, body: dict, registry: dict, call) -> tuple[int, dict]:
    """(HTTP status, JSON answer) for one request. `call(entry, body)` is the reporter's call_popup: (bytes, content type) or {"error"}."""
    entry = registry.get(name) if NAME.fullmatch(name or "") else None
    if not entry:
        return 404, {"error": f"there is no pop-up called {name!r}"}
    if entry.get("box") != "pc":
        return 409, {"error": SLOW}
    args = body.get("args") if isinstance(body.get("args"), dict) else {}
    request = {"args": args}
    given = body.get("input")
    if isinstance(given, dict) and given.get("b64"):
        request["input"] = {"type": str(given.get("type") or ""), "name": str(given.get("name") or "file")[:80], "b64": str(given["b64"])}
    elif entry.get("media_required"):
        return 400, {"error": f"{name} needs a file (input)"}
    got = call(entry, request)
    if isinstance(got, dict):
        return 502, {"error": got.get("error") or f"{name} failed"}
    data, ctype = got
    return 200, {"type": ctype, "b64": base64.b64encode(data).decode("ascii"), "bytes": len(data)}


def make_handler(registry_of, call):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):          # quiet: the reporter's log is for the reporter
            pass

        def _send(self, status: int, payload) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/popups":
                self._send(200, listing(registry_of()))
            elif self.path == "/health":
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not here"})

        def do_POST(self):
            m = re.fullmatch(r"/popup/([a-z0-9_]+)", self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if not m:
                return self._send(404, {"error": "not here"})
            if length > MAX_BODY:
                return self._send(413, {"error": "too big"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "that wasn't JSON"})
            self._send(*run(m.group(1), body if isinstance(body, dict) else {}, registry_of(), call))
    return Handler


def serve(port: int, registry_of, call) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(registry_of, call))
    server.daemon_threads = True
    return server
