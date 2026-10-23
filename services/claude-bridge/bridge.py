"""Claude bridge — runs on the Windows host (NOT in docker): the engine of the private Skippy on the portal's /me.

It shells out to the Claude Code CLI on this PC (the Captain's subscription, not the metered API). The home reporter
(in docker) reaches it via host.docker.internal. The old POST /solid-gold door (the "trust the awesomeness" and
"solid gold" voice tiers) was removed on 2026-09-30 at the Captain's request: the private Skippy has full access.

The Admin page's skippy-pc buttons (host_ops.py): POST /host/<op> {"arg": ...}, same token.
Skippy Memory (memory_service.py): GET /memory/list, GET /memory/conversation?key=<source:id>, same token; and an
import every 30 minutes in a background thread.

The private Skippy (workshop_engine.py): POST /workshop, GET /workshop/run/<task>,
GET /workshop/runs, POST /workshop/cancel/<task>. Those need `Authorization: Bearer <SKIPPY_HOME_TOKEN>`
(the same token the home reporter holds), because they run Claude Code with
full permissions on the repo. Without the token configured they stay shut.

Stdlib-only on purpose: no pip install needed on the host.

ENV:
  CLAUDE_BRIDGE_PORT      default 9337
  CLAUDE_BRIDGE_CWD       default D:\\Aaron\\development
  CLAUDE_PATH             default 'claude' (override if not in PATH)

Launch:  python bridge.py
"""
from __future__ import annotations

import glob
import hmac
import json
import os
import shutil
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import host_ops
import mcp_access
import memory_service
import projects as projects_mod
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


MAX_DRAIN_BYTES = 1_000_000        # an unwanted request body up to this is read and dropped so the refusal arrives as a reply
PORT      = int(os.getenv("CLAUDE_BRIDGE_PORT", "9337"))
CWD       = os.getenv("CLAUDE_BRIDGE_CWD", r"D:\Aaron\development")
CLAUDE    = _discover_claude()

def _registry():
    """projects.json, checked; None (SkippyTel only, from workshop_engine's defaults) if it can't be read. A broken
    registry must not keep the bridge, and with it the Captain's chat, from starting: he can still fix it from SkippyTel."""
    try:
        return projects_mod.load()
    except (OSError, ValueError) as exc:
        print(f"[bridge] projects.json is unusable ({exc}); only SkippyTel is offered", file=sys.stderr)
        return None


WORKSHOP  = workshop.Workshop([CLAUDE], projects=_registry(),   # folders under the development root open in place
                              dev_root=os.getenv("WORKSHOP_DEV_ROOT") or os.path.dirname(os.path.normpath(workshop.PRIMARY)))
HOST_OPS  = host_ops.HostOps(WORKSHOP)
MEMORY    = memory_service.MemoryService()
TOKEN_REREAD_S = 30.0              # how long a token that is held stays unchecked
TOKEN_RETRY_S = 5.0                # while there is none at all (not mounted yet at logon), look again sooner
_token_cache: tuple[float, str] = (0.0, "")      # (when it was last looked at, the last NON-EMPTY token seen)
_token_lock = threading.Lock()


def current_token(now: float | None = None) -> str:
    """The workshop's bearer token, re-read every 30 s so re-pairing home doesn't need a restart here.

    The cache keeps the last NON-EMPTY token and a read only replaces it with another non-empty one. The vault .env is
    on Google Drive (G:), and Drive restarting, pausing or signing out makes the read come back empty; trusting that
    empty answer used to flip every workshop route to 503 within 30 s, which cut a run in flight off from its relay
    (R07). The cost of keeping it: a token deleted from the vault stays honoured until the bridge restarts, which is
    the same trust the bridge already had a minute before the deletion."""
    global _token_cache
    now = time.time() if now is None else now
    with _token_lock:                  # one reader at a time: two threads must not leave the older answer standing
        looked_at, held = _token_cache
        if now - looked_at > (TOKEN_REREAD_S if held else TOKEN_RETRY_S):
            read = workshop.home_token()
            _token_cache = (now, read or held)
        return _token_cache[1]


def workshop_health() -> dict:
    """The /health `workshop` block: can the private Skippy take a message right now, and if not, why in words the
    chat can show. Not ready means no token on this PC, or Claude's sign-in known to be expired (see LoginProbe: an
    unknown login does not count against it)."""
    token = bool(current_token())
    login = WORKSHOP.login()
    info = {"ready": token and login != "expired", "model": workshop.MODEL, "busy": WORKSHOP.busy(), "login": login,
            "projects": WORKSHOP.projects(),         # ids, names and GitHub repos only (no paths): the chat's project picker
            "brains": [{"id": "claude", "label": "Claude Sonnet", "ready": token and login != "expired"},
                       {"id": "core", "label": "Skippy Core coder", "ready": token and WORKSHOP.core_ready()}]}
    if not token:
        info["reason"] = "SKIPPY_HOME_TOKEN isn't configured on this PC"
    elif login == "expired":
        info["reason"] = "Claude's login on skippy-pc expired: run `claude auth login` there"
    return info


class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeBridge/1"

    _body_read = False

    def _drain(self) -> None:
        """Read and drop a request body nobody wanted (a refused request), before the reply goes out. Closing a socket
        with unread data in it makes Windows reset the connection, and the client then sees WinError 10053/10054
        instead of the 401/503 that was sent (found as a flaky test; the reporter and flask-api would have read it as
        'the bridge is down')."""
        if self._body_read:
            return
        self._body_read = True
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            return
        if 0 < length <= MAX_DRAIN_BYTES:
            self.rfile.read(length)

    def _read_body(self) -> dict:
        self._body_read = True
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
                             "workshop": workshop_health()})
            return
        if url.path == "/workshop/runs":
            self._workshop_runs()
            return
        if url.path == "/workshop/conversations":
            self._workshop_conversations()
            return
        if url.path.startswith("/workshop/conversation/"):
            self._workshop_conversation(url)
            return
        if url.path.startswith("/workshop/run/"):
            self._workshop_run(url)
            return
        if url.path in ("/memory/list", "/memory/conversation"):
            self._memory(url)
            return
        if url.path == "/mcp/list":
            if self._workshop_ok():
                self._json(200, mcp_access.listing())
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/workshop":
            self._workshop_start()
            return
        if path == "/workshop/folders":                         # New folder: the folder, its git repo, its GitHub repo
            if self._workshop_ok():
                try:
                    self._json(200, WORKSHOP.new_folder(str(self._read_body().get("name") or "")))
                except ValueError as exc:
                    self._json(409, {"error": "refused", "message": str(exc)})
            return
        if path.startswith("/workshop/cancel/"):
            if self._workshop_ok():
                self._json(200, {"ok": WORKSHOP.cancel(path.rsplit("/", 1)[1])})
            return
        if path.startswith("/host/"):
            self._host_op(path[len("/host/"):])
            return
        if path == "/mcp/test":
            if self._workshop_ok():
                status, body = mcp_access.test(str(self._read_body().get("name") or ""))
                self._json(status, body)
            return
        self._json(404, {"error": "not_found"})

    def _host_op(self, op: str) -> None:
        """The Admin page's skippy-pc buttons (host_ops.py): restart a service or this bridge, reset a workshop clone,
        Funnel on/off. Same token as the workshop: these change the PC."""
        if not self._workshop_ok():
            return
        body = self._read_body()
        try:
            result = HOST_OPS.do(op, str(body.get("arg") or ""))
        except host_ops.Refused as exc:
            self._json(409, {"ok": False, "summary": str(exc)})
            return
        self._json(200, {"ok": result.ok, "summary": result.summary, "output": result.output})

    # ── the private Skippy (workshop.py) ─────────────────────────────────
    def _workshop_ok(self) -> bool:
        """Everything here runs Claude Code (with every permission check skipped), so
        every door wants the home token, and with none configured they all stay shut (Platinum section 19,
        condition 1). The bridge listens on 0.0.0.0 so Docker can reach it: anything on the network can knock, only
        the token opens."""
        token = current_token()
        if not token:
            self._json(503, {"error": "bridge_disabled",
                             "message": "SKIPPY_HOME_TOKEN isn't configured on this PC: the bridge stays shut"})
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
                                 str(body.get("message") or ""), who=str(body.get("who") or ""),
                                 attach=bool(body.get("attach")), project=str(body.get("project") or ""),
                                 images=body.get("images"), brain=str(body.get("brain") or "claude"))
        except workshop.Busy as exc:
            self._json(409, {"error": "busy", "message": str(exc)})
        except workshop.UnknownTask:
            self._json(404, {"error": "unknown_task"})      # a re-attach never starts anything
        except ValueError as exc:
            self._json(400, {"error": "bad_request", "message": str(exc)})
        else:
            self._json(200, {"ok": True, "task": run.task, "done": run.done})

    def _workshop_runs(self) -> None:
        """Which runs this bridge holds (counts and state only, no text): the reporter compares it with the portal's
        records every few seconds and repairs any difference."""
        if not self._workshop_ok():
            return
        self._json(200, {"runs": WORKSHOP.listing()})

    def _workshop_conversations(self) -> None:
        """The newest 50 conversations, summarised (title, project, when, turns, last state), for the chat's
        Conversations list. Behind the same token as everything here: titles are the Captain's own words."""
        if not self._workshop_ok():
            return
        self._json(200, {"conversations": WORKSHOP.conversations()})

    def _workshop_conversation(self, url) -> None:
        """One conversation's turns (his messages, Skippy's text and tool lines), for re-opening it in the chat."""
        if not self._workshop_ok():
            return
        found = WORKSHOP.conversation(url.path.rsplit("/", 1)[1])
        if found is None:
            self._json(404, {"error": "unknown_conversation"})
            return
        self._json(200, found)
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

    def _memory(self, url) -> None:
        """Skippy Memory (memory_service.py): the list of every saved AI conversation, or one conversation's text, for the
        chat's "Continue with Skippy". The Captain's own words from every AI: same token as the workshop."""
        if not self._workshop_ok():
            return
        if url.path == "/memory/list":
            status, body = MEMORY.listing()
        else:
            status, body = MEMORY.read((parse_qs(url.query).get("key") or [""])[0])
        self._json(status, body)

    def _json(self, status: int, body: dict):
        self._drain()
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @staticmethod
    def _routine_success(fmt, args) -> bool:
        """True for the access-log line of a SUCCESSFUL (2xx) GET /health, GET /workshop/run/<task> or GET
        /workshop/runs. The reporter long-polls a run every few seconds and probes /health constantly, so under the
        supervisor (which appends the bridge's stderr to one log file for months) those lines would be nearly all of
        it, burying the ones that matter. Failures (401/404/5xx) and every POST are still logged. Only the stdlib's
        access-line shape ('"%s" %s %s': request line, status, size) is ever skipped: any other line (log_error's
        too), or a shape that changes, falls through to being logged, so the failure mode is noise, never silence."""
        if fmt != '"%s" %s %s' or len(args) != 3:
            return False
        try:
            method, target = str(args[0]).split()[:2]
            status = int(args[1])
        except ValueError:
            return False
        path = target.split("?", 1)[0]
        return (method == "GET" and 200 <= status < 300
                and (path in ("/health", "/workshop/runs") or path.startswith("/workshop/run/")))

    def log_message(self, fmt, *args):
        # Silence default per-request stderr noise; print our own line (minus the routine polls).
        if self._routine_success(fmt, args):
            return
        sys.stderr.write(f"[bridge] {self.address_string()} {fmt % args}\n")


def main():
    print(f"[bridge] PORT={PORT} CWD={CWD} CLAUDE={CLAUDE}",
          file=sys.stderr)
    # Take back the runs saved on disk before the first request, so a restart (a crash, a reboot, the supervisor) keeps
    # finished runs deliverable and ends the interrupted ones with an error the chat can show. Whatever is wrong with
    # that directory, the bridge still starts: being reachable matters more than remembering.
    try:
        print(f"[bridge] {WORKSHOP.load()} saved run(s) restored from {WORKSHOP.state_dir}", file=sys.stderr)
    except Exception:  # noqa: BLE001
        print("[bridge] couldn't restore saved runs; starting without them:", file=sys.stderr)
        traceback.print_exc()
    MEMORY.start()      # Skippy Memory: imports this PC's Claude Code sessions and the inbox every 30 min (memory_service.py)
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
