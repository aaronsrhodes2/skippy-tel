"""The pop-up plumbing's test service (docs/popups.md): no model, so the whole path (chat or agent → patient queue → home reporter →
this → PUT /home/media → the chat's card) can be proven end to end. Standard library only.

  GET  /health  {"ready": true}
  POST /run     {"args": {...}, "input": {"type", "name", "b64"}?} → JSON {"echo": args, "input": {"type", "name", "bytes"}}, or with
                args.mirror and an input, the input's own bytes under its own type.
"""
from __future__ import annotations

import base64
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 4_000_000        # the job's attachment rides as base64: the portal holds it to about 1 MB of file


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send(200, b'{"ready": true}')
        self._send(404, b'{"error": "no such page"}')

    def do_POST(self):  # noqa: N802
        if self.path != "/run":
            return self._send(404, b'{"error": "no such page"}')
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_BODY:
            return self._send(413, b'{"error": "too big"}')
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
            args = body.get("args") if isinstance(body.get("args"), dict) else {}
            given = body.get("input") if isinstance(body.get("input"), dict) else None
            data = base64.b64decode(given["b64"], validate=True) if given else b""
        except (ValueError, KeyError, TypeError):
            return self._send(400, b'{"error": "that wasn\'t a pop-up request"}')
        if args.get("mirror") is True and given:
            return self._send(200, data, str(given.get("type") or "application/octet-stream"))
        out = {"echo": args, "input": {"type": given.get("type"), "name": given.get("name"), "bytes": len(data)} if given else None}
        self._send(200, json.dumps(out).encode())

    def log_message(self, *a):  # quiet: one line per request would fill the log
        pass


def serve(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


if __name__ == "__main__":
    serve(int(os.environ.get("PORT", "47410"))).serve_forever()
