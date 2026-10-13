#!/usr/bin/env python3
"""The starter: skippy-pc's always-on service manager, which the portal can ask to start or stop the whole stack.

Runs on the Windows HOST (not in Docker: it has to work when Docker is down), started at sign-in by the SkippyStarter scheduled task
(tools/install_starter_startup.sh) and kept alive by tools/starter_run.sh. It makes OUTBOUND calls only, like the home reporter: every ~10 s it posts how things are to
the portal (POST /home/starter, the home token from the vault) and gets back the requests waiting for it. It does exactly three things, with no arguments:

  start    Docker Desktop if it is not running (and the documented clean restart if it will not come up), then `docker compose up -d`, then the bridge
  stop     the bridge (refused while a run is going: nothing is touched), then `docker compose stop`; Docker Desktop and this program stay up
  restart  stop, then start

Nothing else can be asked of it: an operation that is not in OPS is refused, and no text from the portal is ever run. It listens on no port except a loopback lock
so a second copy cannot run. The portal side is portal/starter.py; docs/portal/remote-recovery.md says what it cannot do (wake a PC that is off).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPS = ("start", "stop", "restart")
RESUMES = ("7am", "4h", "manual")
POLL_S = 10
PORTAL = os.environ.get("PORTAL_URL", "https://skippy-portal-1015050363686.us-west1.run.app").rstrip("/")
BRIDGE_URL = os.environ.get("CLAUDE_BRIDGE_URL", "http://127.0.0.1:9337").rstrip("/")
DOCKER_EXE = os.environ.get("DOCKER_DESKTOP_EXE", r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
CLEAN_PS1 = os.environ.get("DOCKER_CLEAN_PS1", r"D:\Aaron\development\start-docker-clean.ps1")
BRIDGE_CWD = os.environ.get("CLAUDE_BRIDGE_CWD", "D:/Aaron/development")
DOCKER_WAIT_S, DOCKER_CLEAN_WAIT_S, BRIDGE_WAIT_S = 150, 120, 40
LOCK_PORT = 9339
VAULTS = ("D:/Aaron/development/.env", "G:/My Drive/vault/.env")
KILL_BRIDGE_PS = ("Get-CimInstance Win32_Process | Where-Object { ($_.CommandLine -match 'bridge_supervisor\\.sh' -or $_.CommandLine -match 'claude-bridge.{0,3}bridge\\.py') "
                  "-and $_.CommandLine -notmatch 'Get-CimInstance' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")


def load_token(env=os.environ, vaults=VAULTS) -> str:
    if env.get("SKIPPY_HOME_TOKEN"):
        return env["SKIPPY_HOME_TOKEN"]
    for v in (env.get("SKIPPY_VAULT"), *vaults):
        if v and Path(v).is_file():
            for line in Path(v).read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("SKIPPY_HOME_TOKEN="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def run(cmd: list[str], timeout: float = 120, cwd: str | None = None, env: dict | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as e:
        return 127, str(e)


def http(url: str, data: dict | None = None, headers: dict | None = None, timeout: float = 15) -> dict:
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


class Starter:
    def __init__(self, run=run, http=http, spawn=None, sleep=time.sleep, clock=time.monotonic, token: str = "", root: Path = ROOT, say=print, now=datetime.now, state_file=None):
        self.run, self.http, self.sleep, self.clock, self.token, self.root, self.say, self.now = run, http, sleep, clock, token, root, say, now
        self.state_file = Path(state_file) if state_file else root / "state" / "starter.json"
        self.spawn = spawn or self._spawn
        self.working = ""
        self.results: list[dict] = []
        self.handled: dict[str, float] = {}
        self._lock = threading.Lock()
        self.resume_at: datetime | None = self._load_resume()

    # ── the "back on by itself" setting: kept HERE, in a file, so it works with the portal or the internet down and survives a reboot ──
    def _load_resume(self) -> datetime | None:
        try:
            return datetime.fromisoformat(json.loads(self.state_file.read_text(encoding="utf-8")).get("resume_at") or "")
        except (OSError, ValueError, TypeError):
            return None

    def _save_resume(self, when: datetime | None) -> None:
        self.resume_at = when
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"resume_at": when.isoformat(timespec="minutes") if when else ""}), encoding="utf-8")
        except OSError:
            pass                                                                    # the schedule still holds for this run

    def resume_time(self, resume: str) -> datetime | None:
        now = self.now()
        if resume == "4h":
            return now + timedelta(hours=4)
        if resume == "7am":
            seven = now.replace(hour=7, minute=0, second=0, microsecond=0)
            return seven if seven > now + timedelta(minutes=30) else seven + timedelta(days=1)         # stopped at 6:50: that is tomorrow, not in ten minutes
        return None

    def check_resume(self) -> bool:
        """Time to bring it back? Starts the stack once, when the time has come and it is not busy. Returns whether it did."""
        if self.resume_at is None or self.working or self.now() < self.resume_at:
            return False
        self.working = "start"
        ok, summary = self.do("start")
        with self._lock:
            self.results.append({"id": "auto" + self.now().strftime("%H%M"), "ok": ok, "summary": "Back on by itself, as planned. " + summary})
        self.say(f"auto-start: {'ok' if ok else 'FAILED'}: {summary}")
        if not ok:
            self._save_resume(self.now() + timedelta(minutes=10))                  # try again shortly
        return ok

    # ── looking ────────────────────────────────────────────────────────────────
    def docker_up(self) -> bool:
        return self.run(["docker", "ps", "-q"], 15)[0] == 0

    def containers(self) -> tuple[int, int]:
        rc, out = self.run(["docker", "ps", "-a", "--filter", "name=skippytel-", "--format", "{{.State}}"], 15)
        states = out.split() if rc == 0 else []
        return sum(1 for s in states if s == "running"), len(states)

    def bridge(self) -> dict | None:
        try:
            return self.http(f"{BRIDGE_URL}/health", timeout=4)
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def status(self) -> dict:
        up, total = self.containers() if self.docker_up() else (0, 0)
        b = self.bridge()
        return {"docker": total > 0 or self.docker_up(), "containers_up": up, "containers_total": total, "bridge": b is not None,
                "busy": bool(b and (b.get("workshop") or {}).get("busy")), "supervisor": b is not None, "working": self.working,
                "resume_at": self.resume_at.isoformat(timespec="minutes") if self.resume_at else ""}

    # ── doing ──────────────────────────────────────────────────────────────────
    def _spawn(self, cmd: list[str], env: dict | None = None, log: str = "") -> None:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        out = open(log, "ab") if log else subprocess.DEVNULL
        subprocess.Popen(cmd, stdout=out, stderr=out, stdin=subprocess.DEVNULL, env=env, creationflags=flags, close_fds=True, cwd=str(self.root))

    def _wait(self, check, seconds: float) -> bool:
        end = self.clock() + seconds
        while self.clock() < end:
            if check():
                return True
            self.sleep(3)
        return check()

    def ensure_docker(self) -> str | None:
        """None when Docker answers; otherwise why not."""
        if self.docker_up():
            return None
        self.working = "starting Docker Desktop"
        self.spawn([DOCKER_EXE])
        if self._wait(self.docker_up, DOCKER_WAIT_S):
            return None
        self.working = "Docker would not start: cleaning up and trying again"
        self.run(["powershell.exe", "-NoProfile", "-File", CLEAN_PS1], 90)           # the documented recovery (CLAUDE.md, Docker Desktop clean startup)
        if self._wait(self.docker_up, DOCKER_CLEAN_WAIT_S):
            return None
        return "Docker Desktop would not start, even after the clean restart: the PC needs a look in person."

    def start(self) -> tuple[bool, str]:
        steps = []
        why = self.ensure_docker()
        if why:
            return False, why
        steps.append("Docker up")
        self.working = "starting the containers"
        rc, out = self.run(["docker", "compose", "up", "-d"], 300, cwd=str(self.root))
        up, total = self.containers()
        if rc != 0:
            return False, f"Docker is up but the containers did not all start ({up} of {total}): {out[-200:]}"
        steps.append(f"{up} of {total} containers")
        if self.bridge() is None:
            self.working = "starting the bridge"
            env = {**os.environ, "CLAUDE_BRIDGE_CWD": BRIDGE_CWD}
            self.spawn([os.environ.get("BASH_EXE", "bash"), "-l", str(self.root / "tools" / "bridge_supervisor.sh")], env=env, log=str(self.root / "state" / "starter-bridge.log"))
            if not self._wait(lambda: self.bridge() is not None, BRIDGE_WAIT_S):
                return False, "; ".join(steps) + "; but the bridge did not answer in time (look at state/bridge-supervisor.log)."
        steps.append("bridge answering")
        self._save_resume(None)                                                     # it is up: any "back on at" setting is done
        return True, "; ".join(steps) + "."

    def stop(self, resume: str = "7am") -> tuple[bool, str]:
        b = self.bridge()
        if b and (b.get("workshop") or {}).get("busy"):
            return False, "Skippy Desktop is in the middle of a run. Nothing was stopped: cancel the run first (Admin, recovery)."
        self.working = "stopping the bridge"
        if b is not None:
            self.run(["powershell.exe", "-NoProfile", "-Command", KILL_BRIDGE_PS], 30)
        self.working = "stopping the containers"
        rc, out = self.run(["docker", "compose", "stop"], 180, cwd=str(self.root)) if self.docker_up() else (0, "")
        if rc != 0:
            return False, f"The bridge is stopped but the containers did not all stop: {out[-200:]}"
        when = self.resume_time(resume)
        self._save_resume(when)
        back = f" It starts itself again at {when.strftime('%a %I:%M %p').replace(' 0', ' ')}." if when else " It stays down until you start it."
        return True, "Stopped: the bridge and every container. Docker Desktop and the starter are still up." + back

    def restart(self) -> tuple[bool, str]:
        ok, text = self.stop("manual")
        if not ok:
            return False, text
        ok2, text2 = self.start()
        return ok2, "Restarted. " + text2 if ok2 else text2

    def do(self, op: str, resume: str = "7am") -> tuple[bool, str]:
        """Only an operation in OPS runs. Never raises."""
        if op not in OPS or (op == "stop" and resume not in RESUMES):
            return False, "I do not do that."
        try:
            return self.stop(resume) if op == "stop" else {"start": self.start, "restart": self.restart}[op]()
        except Exception as e:                                         # noqa: BLE001 — a failure is a result, not a crash
            return False, f"It went wrong ({type(e).__name__})."
        finally:
            self.working = ""

    # ── talking to the portal ──────────────────────────────────────────────────
    def _work(self, cid: str, op: str, resume: str = "7am") -> None:
        ok, summary = self.do(op, resume)
        with self._lock:
            self.results.append({"id": cid, "ok": ok, "summary": summary})
        self.say(f"{op}: {'ok' if ok else 'FAILED'}: {summary}")

    def poll_once(self, threaded: bool = True) -> list[dict]:
        """One call to the portal; starts any new request (once per id) in the background so polling goes on while Docker starts. Returns the commands it got."""
        with self._lock:
            sent, self.results = self.results, []
        try:
            reply = self.http(f"{PORTAL}/home/starter", {"status": self.status(), "results": sent}, {"Authorization": f"Bearer {self.token}"})
        except (OSError, ValueError, urllib.error.URLError) as e:
            with self._lock:
                self.results = sent + self.results                    # not delivered: try again next time
            self.say(f"portal not reachable ({type(e).__name__})")
            return []
        commands = reply.get("commands") if isinstance(reply.get("commands"), list) else []
        for c in commands:
            cid, op = (c.get("id"), c.get("op")) if isinstance(c, dict) else (None, None)
            resume = c.get("resume") if isinstance(c, dict) and c.get("resume") in RESUMES else "7am"
            if not isinstance(cid, str) or cid in self.handled:
                continue
            self.handled[cid] = self.clock()
            if op not in OPS:                                           # a request for anything else is answered, and nothing runs
                with self._lock:
                    self.results.append({"id": cid, "ok": False, "summary": "I do not do that."})
                continue
            if self.working:                                            # one thing at a time
                with self._lock:
                    self.results.append({"id": cid, "ok": False, "summary": f"Busy: {self.working}."})
                continue
            self.working = op
            if threaded:
                threading.Thread(target=self._work, args=(cid, op, resume), daemon=True).start()
            else:
                self._work(cid, op, resume)
        for old in [k for k, t in self.handled.items() if self.clock() - t > 3600]:
            self.handled.pop(old, None)
        return commands

    def forever(self) -> None:
        while True:
            try:
                self.check_resume()
            except Exception as e:                                                  # noqa: BLE001 — the loop must never die
                self.say(f"auto-start crashed ({type(e).__name__})")
            self.poll_once()
            self.sleep(POLL_S)


def one_copy() -> socket.socket | None:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        return None


def main() -> int:
    lock = one_copy()
    if lock is None:
        print("the starter is already running", file=sys.stderr)
        return 0
    token = load_token()
    if not token:
        print("no SKIPPY_HOME_TOKEN in the vault: nothing to authenticate with", file=sys.stderr)
        return 2
    print(f"starter: calling {PORTAL} every {POLL_S} s as the home token; docs/portal/remote-recovery.md", flush=True)
    Starter(token=token).forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
