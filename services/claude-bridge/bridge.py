"""solid-gold bridge — runs on the Windows host (NOT in docker).

Exposes one HTTP endpoint that shells out to Claude Code CLI on this PC.
SkippyTel's flask-api (in docker) calls it via host.docker.internal.
That keeps the call on Aaron's Claude Code OAuth subscription instead
of the metered API key.

Two modes, picked by request body `mode`:
  - "plan"   → solid gold (read-only planning, default Sonnet)
  - "bypass" → trust the awesomeness (full perms, Opus 4.7)

Plus the private Skippy (workshop_engine.py): POST /workshop, GET /workshop/run/<task>,
POST /workshop/cancel/<task>. Those need `Authorization: Bearer <SKIPPY_HOME_TOKEN>`
(the same token the home reporter holds), because they run Claude Code with
full permissions on the repo. Without the token configured they stay shut.

Stdlib-only on purpose: no pip install needed on the host.

ENV:
  CLAUDE_BRIDGE_PORT      default 9337
  CLAUDE_BRIDGE_CWD       default D:\\Aaron\\development
  CLAUDE_PATH             default 'claude' (override if not in PATH)
  CLAUDE_AWESOMENESS_MODEL default 'claude-opus-4-7'

Launch:  python bridge.py
"""
from __future__ import annotations

import glob
import hmac
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import workshop_engine as workshop


def _discover_claude() -> str:
    """Find the claude.exe binary. Override priority:
       1. CLAUDE_PATH env var pointing to an existing file
       2. `claude` in PATH (any of: claude / claude.cmd / claude.exe)
       3. Known Windows install locations, newest by mtime.
    """
    raw = os.getenv("CLAUDE_PATH", "claude")
    if raw and raw != "claude" and os.path.isfile(raw):
        return raw

    for name in ("claude", "claude.cmd", "claude.exe"):
        found = shutil.which(name)
        if found:
            return found

    home = os.path.expanduser("~")
    patterns = [
        os.path.join(home, "AppData/Roaming/Claude/claude-code/*/claude.exe"),
        os.path.join(home, ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe"),
        os.path.join(home, "AppData/Local/Packages/Claude_*/LocalCache/Roaming/Claude/claude-code/*/claude.exe"),
    ]
    candidates: list[str] = []
    for p in patterns:
        candidates.extend(glob.glob(p))
    if candidates:
        candidates.sort(key=os.path.getmtime, reverse=True)
        return candidates[0]

    return raw  # falls through; subprocess will FileNotFoundError with a clear msg


PORT      = int(os.getenv("CLAUDE_BRIDGE_PORT", "9337"))
CWD       = os.getenv("CLAUDE_BRIDGE_CWD", r"D:\Aaron\development")
CLAUDE    = _discover_claude()
AWE_MODEL = os.getenv("CLAUDE_AWESOMENESS_MODEL", "claude-opus-4-7")

WORKSHOP  = workshop.Workshop([CLAUDE])
_token_cache: tuple[float, str] = (0.0, "")


def current_token() -> str:
    """The workshop's bearer token, re-read every 30 s so re-pairing home doesn't need a restart here."""
    global _token_cache
    now = time.time()
    if now - _token_cache[0] > 30:
        _token_cache = (now, workshop.home_token())
    return _token_cache[1]


def build_cmd(prompt: str, mode: str) -> list[str]:
    """Construct the claude CLI invocation for the chosen mode."""
    base = [
        CLAUDE, "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
    ]
    if mode == "bypass":
        # trust the awesomeness — full perms, top model
        base += ["--dangerously-skip-permissions",
                 "--model", AWE_MODEL]
    else:
        # solid gold — read-only planning mode (default model)
        base += ["--permission-mode", "plan"]
    return base


def extract_text_chunks(line: str) -> list[str]:
    """Pull just the assistant text deltas out of a stream-json line.

    Claude Code emits NDJSON events. We only forward assistant prose to
    the phone — tool uses, system events, etc. stay invisible.
    """
    line = line.strip()
    if not line:
        return []
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        return []
    out: list[str] = []
    if evt.get("type") == "assistant":
        msg = evt.get("message") or {}
        for block in msg.get("content", []) or []:
            if block.get("type") == "text":
                t = block.get("text") or ""
                if t:
                    out.append(t)
    elif evt.get("type") == "result":
        # Final synthesized text — only emit if we never saw streaming
        # assistant blocks (some short prompts collapse to result-only).
        # Caller can dedupe if needed; we keep it simple and skip here.
        pass
    return out


def run_streaming(prompt: str, mode: str, write_chunk):
    """Spawn claude, parse NDJSON stdout, push text deltas via write_chunk."""
    cmd = build_cmd(prompt, mode)
    proc = subprocess.Popen(
        cmd,
        cwd=CWD,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,  # line-buffered
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            for chunk in extract_text_chunks(line):
                write_chunk(chunk)
    finally:
        proc.wait(timeout=5)
    return proc.returncode


def run_oneshot(prompt: str, mode: str) -> dict:
    """Synchronous: run claude, collect all text, return as one string."""
    parts: list[str] = []
    rc = run_streaming(prompt, mode, parts.append)
    return {"output": "".join(parts), "returncode": rc}


class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeBridge/1"

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            return {}

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/health":
            self._json(200, {"status": "ok", "service": "claude-bridge",
                             "cwd": CWD, "claude": CLAUDE,
                             "awe_model": AWE_MODEL,
                             "workshop": {"ready": bool(current_token()), "model": workshop.MODEL,
                                          "busy": WORKSHOP.busy()}})
            return
        if url.path.startswith("/workshop/run/"):
            self._workshop_run(url)
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/solid-gold":
            self._handle_solid_gold()
            return
        if path == "/workshop":
            self._workshop_start()
            return
        if path.startswith("/workshop/cancel/"):
            if self._workshop_ok():
                self._json(200, {"ok": WORKSHOP.cancel(path.rsplit("/", 1)[1])})
            return
        self._json(404, {"error": "not_found"})

    # ── the private Skippy (workshop.py) ─────────────────────────────────
    def _workshop_ok(self) -> bool:
        """It runs Claude Code with full permissions, so it wants the home token."""
        token = current_token()
        if not token:
            self._json(503, {"error": "workshop_disabled",
                             "message": "SKIPPY_HOME_TOKEN isn't configured on this PC"})
            return False
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Bearer ") or not hmac.compare_digest(
                header[7:].strip().encode("utf-8", "replace"), token.encode("utf-8", "replace")):
            self._json(401, {"error": "unauthorized"})
            return False
        return True

    def _workshop_start(self) -> None:
        if not self._workshop_ok():
            return
        body = self._read_body()
        try:
            run = WORKSHOP.start(str(body.get("task") or ""), str(body.get("conv") or ""),
                                 str(body.get("message") or ""))
        except workshop.Busy as exc:
            self._json(409, {"error": "busy", "message": str(exc)})
        except ValueError as exc:
            self._json(400, {"error": "bad_request", "message": str(exc)})
        else:
            self._json(200, {"ok": True, "task": run.task, "done": run.done})

    def _workshop_run(self, url) -> None:
        if not self._workshop_ok():
            return
        run = WORKSHOP.get(url.path.rsplit("/", 1)[1])
        if run is None:
            self._json(404, {"error": "unknown_task"})
            return
        query = parse_qs(url.query)
        try:
            after = int((query.get("after") or ["0"])[0])
            wait = min(25.0, float((query.get("wait") or ["0"])[0]))
        except ValueError:
            self._json(400, {"error": "bad_request"})
            return
        self._json(200, run.since(after, wait))

    def _handle_solid_gold(self):
        body = self._read_body()
        prompt = (body.get("text") or "").strip()
        mode   = body.get("mode") or "plan"   # 'plan' | 'bypass'
        stream = bool(body.get("stream"))

        if not prompt:
            self._json(400, {"error": "bad_request",
                             "message": "text is required"})
            return
        if mode not in ("plan", "bypass"):
            self._json(400, {"error": "bad_request",
                             "message": "mode must be 'plan' or 'bypass'"})
            return

        try:
            if stream:
                self._stream_text(prompt, mode)
            else:
                result = run_oneshot(prompt, mode)
                self._json(200, result)
        except FileNotFoundError as e:
            self._json(503, {"error": "claude_not_found",
                             "message": f"could not exec '{CLAUDE}': {e}. "
                                        "Set CLAUDE_PATH env var to the full claude.cmd path."})
        except Exception as e:
            traceback.print_exc()
            self._json(502, {"error": "bridge_error", "message": str(e)})

    def _stream_text(self, prompt: str, mode: str):
        """Stream NDJSON {"text": "..."} lines, one per assistant chunk,
        terminated by {"done": true}. Caller (flask-api client) parses."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def write_chunk(chunk: str):
            payload = json.dumps({"text": chunk}) + "\n"
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()

        rc = run_streaming(prompt, mode, write_chunk)
        done = json.dumps({"done": True, "returncode": rc}) + "\n"
        self.wfile.write(done.encode("utf-8"))
        self.wfile.flush()

    def _json(self, status: int, body: dict):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        # Silence default per-request stderr noise; print our own line.
        sys.stderr.write(f"[bridge] {self.address_string()} {fmt % args}\n")


def main():
    print(f"[bridge] PORT={PORT} CWD={CWD} CLAUDE={CLAUDE} AWE={AWE_MODEL}",
          file=sys.stderr)
    # Bind 0.0.0.0 so Docker Desktop's host.docker.internal can reach us.
    # On Windows + Docker Desktop, host-gateway routes container traffic to
    # the host's loopback through a special address; binding to 0.0.0.0
    # (any interface) covers it cleanly. Tailscale also reachable; firewall
    # rule should restrict if you don't want phone-direct access.
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[bridge] shutting down", file=sys.stderr)


if __name__ == "__main__":
    main()
