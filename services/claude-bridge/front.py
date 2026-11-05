"""The bridge's front door (the Captain, 2026-10-31: "working tasks finish on the old one before it poofs, and new jobs use the new one").

Listens on the bridge's port (9337: what the home reporter, the starter and the supervisor know) and runs bridge.py behind it as
GENERATIONS, each on a private loopback port. A HAND-OVER starts a new generation from the code on disk; from then on every NEW job
goes to it, while the old one finishes the run it has (even the run that asked for the hand-over: purple Skippy deploying a change
to itself) and is stopped only once it is idle. Nothing is cut off, and nobody has to wait for a quiet moment to deploy the bridge.

  * routing: a run's own calls (GET /workshop/run/<task>, cancel, a re-attach) go to the generation that holds it; a new run goes to
    the newest; GET /workshop/runs is every generation's runs together; /health is the newest one's, busy if any generation is busy.
  * one queue: while an older generation is still running, a new run is refused as busy, exactly as one bridge would.
  * a hand-over: POST /front/handover (the bridge's restart-bridge, host_ops.py), or by itself when the bridge's code on disk changes
    (a `git pull` of a deploy); BRIDGE_FRONT_AUTO=0 turns the second off.
  * the newest generation crashing or wedging (no /health for HEALTH_FAILS checks) is replaced; one draining dies, its runs are adopted.
  * this file is the one part that stays up: it holds no run, so restarting it (to change it) loses nothing. The generations are
    in state/bridge-front.json and a new front door takes them back.

Stdlib only, like bridge.py.   Run:  python front.py   (tools/bridge_supervisor.sh runs it)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
PORT = int(os.getenv("CLAUDE_BRIDGE_PORT", "9337"))
GEN_PORTS = [int(p) for p in os.getenv("BRIDGE_FRONT_PORTS", "9341,9342,9343,9344").split(",")]
STATE = Path(os.getenv("BRIDGE_FRONT_STATE", str(HERE.parents[1] / "state" / "bridge-front.json")))
AUTO = os.getenv("BRIDGE_FRONT_AUTO", "1") != "0"
TICK_S = float(os.getenv("BRIDGE_FRONT_TICK", "3"))
HEALTH_FAILS = 10                     # ~30 s of no /health from the newest generation: replace it
START_WAIT_S = 90                     # how long a new generation may take to answer /health
FORWARD_TIMEOUT_S = 60                # a run poll waits up to 25 s; a workshop start describes pictures first
_TASK_PATH = re.compile(r"^/workshop/(?:run|cancel)/([A-Za-z0-9_-]{1,80})$")
BUSY = {"error": "busy", "message": "Skippy is still working on the last thing"}


def log(msg: str) -> None:
    print(f"[front] {time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def code_hash(folder: Path = HERE) -> str:
    """What the bridge's code on disk is: a new value means a pull changed it and a hand-over is due. This file is left out (it is
    the front door itself, which a hand-over does not replace)."""
    h = hashlib.sha256()
    for p in sorted(folder.glob("*.py")) + [folder / "workshop_prompt.md", folder / "projects.json"]:
        if p.name == "front.py" or not p.is_file():
            continue
        try:
            h.update(p.name.encode() + b"\0" + p.read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:16]


class Gen:
    def __init__(self, n: int, port: int, pid: int, code: str, proc=None, draining: bool = False, started: float | None = None):
        self.n, self.port, self.pid, self.code, self.proc, self.draining = n, port, pid, code, proc, draining
        self.started = started or time.time()
        self.fails = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        return pid_alive(self.pid)

    def record(self) -> dict:
        return {"n": self.n, "port": self.port, "pid": self.pid, "code": self.code, "draining": self.draining, "started": self.started}


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def http(method: str, url: str, body: bytes | None = None, headers: dict | None = None, timeout: float = 10) -> tuple[int, bytes]:
    """(status, body); status 0 when nothing answered."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""
    except Exception:  # noqa: BLE001 — down, refused, timed out
        return 0, b""


def as_json(raw: bytes) -> dict:
    try:
        got = json.loads(raw or b"{}")
        return got if isinstance(got, dict) else {}
    except ValueError:
        return {}


class Front:
    def __init__(self, spawn=None, request=http, state: Path = STATE, code=code_hash, kill=kill_tree):
        self.spawn_proc = spawn or self._spawn_proc
        self.request, self.state, self.code, self.kill = request, state, code, kill
        self.gens: list[Gen] = []
        self.owner: dict[str, int] = {}               # task -> generation number
        self.last_n = 0                               # the highest number ever given: a replacement never reuses one (the chaos drill
                                                      # killed the only generation and its replacement was numbered 1 again, 2026-11-01)
        self.lock = threading.RLock()
        self.handing_over = threading.Lock()
        self.token = ""                               # the last Authorization a generation accepted: the adopt call carries it
        self.pending_code = ""                        # a changed code hash seen once (the second sighting starts the hand-over)

    # ── generations ───────────────────────────────────────────────────────────────────────────────────────────────────────
    def current(self) -> Gen | None:
        with self.lock:
            live = [g for g in self.gens if not g.draining]
            return live[-1] if live else None

    def by_n(self, n: int | None) -> Gen | None:
        with self.lock:
            return next((g for g in self.gens if g.n == n), None)

    def _spawn_proc(self, port: int, n: int, handover: bool):
        env = {**os.environ, "CLAUDE_BRIDGE_PORT": str(port), "BRIDGE_GENERATION": str(n), "BRIDGE_HANDOVER": "1" if handover else "0",
               "BRIDGE_FRONT_URL": f"http://127.0.0.1:{PORT}"}
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen([sys.executable, "bridge.py"], cwd=str(HERE), env=env, creationflags=flags)

    def start_gen(self, handover: bool) -> Gen | None:
        with self.lock:
            used = {g.port for g in self.gens}
            port = next((p for p in GEN_PORTS if p not in used), None)
            n = max([g.n for g in self.gens] + [self.last_n]) + 1
            self.last_n = n
        if port is None:
            log("no free port for a new generation (every one is still draining)")
            return None
        code = self.code()
        proc = self.spawn_proc(port, n, handover)
        gen = Gen(n, port, getattr(proc, "pid", 0), code, proc=proc)
        deadline = time.time() + START_WAIT_S
        while time.time() < deadline:
            if not gen.alive():
                log(f"generation {n} exited before it answered")
                return None
            if self.request("GET", gen.url + "/health", timeout=3)[0] == 200:
                with self.lock:
                    self.gens.append(gen)
                self.save()
                log(f"generation {n} up on :{port} (code {code}{', hand-over' if handover else ''})")
                return gen
            time.sleep(0.5)
        log(f"generation {n} never answered /health; stopping it")
        self.kill(gen.pid)
        return None

    def handover(self, why: str) -> tuple[bool, str]:
        """A new generation from the code on disk; the old ones drain. One at a time."""
        if not self.handing_over.acquire(blocking=False):
            return True, "A hand-over is already under way."
        try:
            old = self.current()
            new = self.start_gen(handover=old is not None)
            if new is None:
                return False, "The new bridge didn't come up, so the running one stays (see state/bridge-supervisor.log)."
            with self.lock:
                for g in self.gens:
                    if g is not new:
                        g.draining = True
            self.save()
            log(f"hand-over to generation {new.n} ({why})")
            return True, (f"New bridge (generation {new.n}) is taking new work; the old one finishes its run first, then stops."
                          if old else f"Bridge generation {new.n} started.")
        finally:
            self.handing_over.release()

    def tick(self) -> None:
        """Every few seconds: retire the drained, adopt the runs of the gone, replace a dead or wedged newest, notice new code."""
        gone = False
        with self.lock:
            gens = list(self.gens)
        for g in gens:
            if g.draining:
                if not g.alive():
                    self._drop(g, "exited")
                    gone = True
                elif not self._busy(g):
                    self.kill(g.pid)
                    self._drop(g, "drained")
                    gone = True
        if gone:
            cur = self.current()
            if cur is not None:
                self.request("POST", cur.url + "/workshop/adopt", json.dumps({"keep": self._held(cur)}).encode(), self._auth(), timeout=30)
        cur = self.current()
        if cur is None or not cur.alive():
            if cur is not None:
                self._drop(cur, "died")
            self.start_gen(handover=any(g.alive() for g in self.gens))
            return
        if self.request("GET", cur.url + "/health", timeout=5)[0] == 200:
            cur.fails = 0
        else:
            cur.fails += 1
            if cur.fails >= HEALTH_FAILS:
                log(f"generation {cur.n} stopped answering; replacing it")
                self.kill(cur.pid)
                self._drop(cur, "wedged")
                self.start_gen(handover=False)
                return
        if AUTO:
            now = self.code()
            if now != cur.code:
                if self.pending_code == now:
                    self.pending_code = ""
                    self.handover("the bridge's code on disk changed")
                else:
                    self.pending_code = now               # seen once: a pull may still be writing files
            else:
                self.pending_code = ""

    def _drop(self, g: Gen, why: str) -> None:
        with self.lock:
            if g in self.gens:
                self.gens.remove(g)
            for t in [t for t, n in self.owner.items() if n == g.n]:
                del self.owner[t]
        self.save()
        log(f"generation {g.n} {why}")

    def _auth(self) -> dict:
        return {"Content-Type": "application/json", **({"Authorization": self.token} if self.token else {})}

    def _runs(self, g: Gen) -> list[dict] | None:
        status, raw = self.request("GET", g.url + "/workshop/runs", None, self._auth(), timeout=10)
        if status != 200:
            return None
        runs = [r for r in as_json(raw).get("runs") or [] if isinstance(r, dict)]
        with self.lock:
            for r in runs:
                if not r.get("done") and isinstance(r.get("task"), str):
                    self.owner[r["task"]] = g.n
        return runs

    def _held(self, cur: Gen) -> list[str]:
        """The unfinished runs the other generations that are still alive hold: the adopt must leave them to finish there."""
        with self.lock:
            others = [g for g in self.gens if g is not cur and g.alive()]
        return sorted({r["task"] for g in others for r in (self._runs(g) or []) if not r.get("done") and isinstance(r.get("task"), str)})

    def _busy(self, g: Gen, brain: str | None = None) -> bool:
        """True unless the generation says it holds no unfinished run (of that brain, when one is named). Not knowing counts as busy: never stop a run by
        guessing."""
        runs = self._runs(g)
        return runs is None or any(not r.get("done") and (brain is None or str(r.get("brain") or "core") == brain) for r in runs)

    def save(self) -> None:
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            with self.lock:
                data = {"front_pid": os.getpid(), "last_n": self.last_n, "gens": [g.record() for g in self.gens]}
            tmp = self.state.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self.state)
        except OSError as exc:
            log(f"couldn't save {self.state}: {exc}")

    def restore(self) -> None:
        """A restarted front door takes back the generations that still answer."""
        try:
            data = json.loads(self.state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        try:
            self.last_n = int(data.get("last_n") or 0)
        except (TypeError, ValueError):
            pass
        for rec in data.get("gens") or []:
            try:
                g = Gen(int(rec["n"]), int(rec["port"]), int(rec["pid"]), str(rec.get("code") or ""), draining=bool(rec.get("draining")),
                        started=float(rec.get("started") or 0) or None)
            except (KeyError, TypeError, ValueError):
                continue
            if g.alive() and self.request("GET", g.url + "/health", timeout=3)[0] == 200:
                self.gens.append(g)
                log(f"took back generation {g.n} on :{g.port}")
        self.gens.sort(key=lambda g: g.n)

    # ── routing ───────────────────────────────────────────────────────────────────────────────────────────────────────────
    def route(self, method: str, path: str, query: str, body: bytes, headers: dict) -> tuple[int, bytes]:
        p = urlparse(path).path
        if p == "/front/handover" and method == "POST":
            if not self._authorized(headers):
                return 401, json.dumps({"error": "unauthorized"}).encode()
            ok, summary = self.handover("asked")
            return (200 if ok else 503), json.dumps({"ok": ok, "summary": summary}).encode()
        if p == "/front/status":
            if not self._authorized(headers):
                return 401, json.dumps({"error": "unauthorized"}).encode()
            with self.lock:
                return 200, json.dumps({"gens": [g.record() for g in self.gens], "owners": dict(self.owner)}).encode()
        cur = self.current()
        if cur is None:
            return 503, json.dumps({"error": "starting", "message": "The bridge is starting; try again in a few seconds."}).encode()
        target = cur
        m = _TASK_PATH.match(p)
        task = m.group(1) if m else ""
        if p == "/workshop" and method == "POST":
            task = str(as_json(body).get("task") or "")
        if task:
            target = self._owner_of(task) or cur
        if p == "/workshop" and method == "POST" and target is cur:
            brain = str(as_json(body).get("brain") or "core")
            if any(g is not cur and g.alive() and self._busy(g, brain) for g in self._draining()):
                return 409, json.dumps(BUSY).encode()        # one queue per brain (the Captain, 2026-11-05): an older generation's Core run holds up Core, not Claude
        if p == "/workshop/runs" and method == "GET":
            return self._all_runs(headers, query)
        status, raw = self.request(method, target.url + p + (("?" + query) if query else ""), body or None,
                                   {k: v for k, v in headers.items() if k.lower() in ("authorization", "content-type")},
                                   timeout=FORWARD_TIMEOUT_S)
        if status == 0:
            return 502, json.dumps({"error": "bridge_unreachable", "message": f"bridge generation {target.n} didn't answer"}).encode()
        if 200 <= status < 300 and headers.get("Authorization"):
            self.token = headers["Authorization"]
        if p == "/workshop" and method == "POST" and status == 200:
            got = as_json(raw)
            if isinstance(got.get("task"), str):
                with self.lock:
                    self.owner[got["task"]] = target.n
        if p == "/health" and status == 200:
            raw = self._health(raw)
        return status, raw

    def _draining(self) -> list[Gen]:
        with self.lock:
            return [g for g in self.gens if g.draining]

    def _owner_of(self, task: str) -> Gen | None:
        with self.lock:
            g = self.by_n(self.owner.get(task))
        if g is not None and g.alive():
            return g
        for d in self._draining():                    # not seen yet (a front door restarted): ask the older ones
            if d.alive():
                self._runs(d)
        with self.lock:
            g = self.by_n(self.owner.get(task))
        return g if g is not None and g.alive() else None

    def _authorized(self, headers: dict) -> bool:
        cur = self.current()
        auth = headers.get("Authorization") or ""
        if cur is None or not auth:
            return False
        return self.request("GET", cur.url + "/workshop/runs", None, {"Authorization": auth}, timeout=10)[0] == 200

    def _all_runs(self, headers: dict, query: str) -> tuple[int, bytes]:
        merged: dict[str, dict] = {}
        first = None
        with self.lock:
            gens = list(reversed(self.gens))          # newest first
        for g in gens:
            status, raw = self.request("GET", g.url + "/workshop/runs" + (("?" + query) if query else ""), None,
                                       {k: v for k, v in headers.items() if k.lower() == "authorization"}, timeout=15)
            if status != 200:
                if g is gens[0]:
                    first = (status or 502, raw or json.dumps({"error": "bridge_unreachable"}).encode())
                continue
            if headers.get("Authorization"):
                self.token = headers["Authorization"]
            for r in as_json(raw).get("runs") or []:
                if not isinstance(r, dict) or not isinstance(r.get("task"), str):
                    continue
                if not r.get("done"):
                    with self.lock:
                        self.owner[r["task"]] = g.n
                have = merged.get(r["task"])
                if have is None or (have.get("done") and not r.get("done")):
                    merged[r["task"]] = r
        if first is not None and not merged:
            return first
        runs = sorted(merged.values(), key=lambda r: r.get("age_s") or 0)
        return 200, json.dumps({"runs": runs}).encode()

    def _health(self, raw: bytes) -> bytes:
        body = as_json(raw)
        ws = body.get("workshop")
        if isinstance(ws, dict) and not ws.get("busy"):
            ws["busy"] = any(g.alive() and self._busy(g) for g in self._draining())
        with self.lock:
            body["front"] = {"generations": [{"n": g.n, "draining": g.draining} for g in self.gens]}
        return json.dumps(body).encode()


FRONT: Front | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeBridgeFront/1"

    def _go(self, method: str) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        u = urlparse(self.path)
        status, raw = FRONT.route(method, u.path, u.query, body, {k: v for k, v in self.headers.items()})
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        self._go("GET")

    def do_POST(self):  # noqa: N802
        self._go("POST")

    def log_message(self, fmt, *args):         # the generations log their own requests
        return


def main() -> None:
    global FRONT
    FRONT = Front()
    FRONT.restore()
    if FRONT.current() is None:
        FRONT.start_gen(handover=any(g.alive() for g in FRONT.gens))

    def loop():
        while True:
            time.sleep(TICK_S)
            try:
                FRONT.tick()
            except Exception as exc:  # noqa: BLE001 — the front door must outlive anything a tick hits
                log(f"tick failed: {type(exc).__name__}: {exc}")

    threading.Thread(target=loop, name="front-tick", daemon=True).start()
    log(f"front door on :{PORT}, generations on {GEN_PORTS}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
