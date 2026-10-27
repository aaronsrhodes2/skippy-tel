#!/usr/bin/env python3
"""The starter: skippy-pc's always-on service manager, which the portal can ask to start or stop the whole stack.

Runs on the Windows HOST (not in Docker: it has to work when Docker is down), started at sign-in by the SkippyStarter scheduled task
(tools/install_starter_startup.sh) and kept alive by tools/starter_run.sh. It makes OUTBOUND calls only, like the home reporter: every ~10 s it posts how things are to
the portal (POST /home/starter, the home token from the vault) and gets back the requests waiting for it. What it does:

  start    Docker Desktop if it is not running (and the documented clean restart if it will not come up), then `docker compose up -d`, then the bridge
  stop     the bridge (refused while a run is going: nothing is touched), then `docker compose stop`; Docker Desktop and this program stay up
  restart  stop, then start
  heal X   the doctor's (portal/doctor.py) way of putting ONE thing back: a compose service recreated (stop, rm, up --build; left alone
           while someone is building at that moment), the bridge restarted (never mid-run), Docker restarted clean, or Skippy Core
           repaired over SSH (`deployer.py repair` on the box)
  repair   the one Recovery button: everything back to baseline (repair() says what that is)

It also heals Docker by itself: hung or down for over two minutes (and not stopped on purpose), it gets the clean restart even with the
portal out of reach. Nothing else can be asked of it: an operation not in OPS, or a name not in its lists, is refused, and no text from the
portal is ever run. Processes are stopped by PID only (the one listening on the bridge's port, the supervisor's own pid file). It listens on no port except a loopback lock
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
OPS = ("start", "stop", "restart", "heal", "repair")
HEAL_SPECIAL = ("bridge", "docker", "core")             # the rest a heal may name: a service in `docker compose config --services`
CORE_SSH = os.environ.get("SKIPPY_CORE_SSH", "aaron@skippy-core")
CORE_REPAIR = ["sudo", "-n", "/usr/bin/python3", "/opt/skippy-core/doctor/deployer.py", "repair"]   # the doctor's own copy (core/deployer/install.sh)
DOCKER_DOWN_HEAL_S = 120
BRIDGE_PORT = 9337
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
# skippy-pc's own UPS (a CyberPower on USB, which Windows sees as a battery): after this long on battery the starter stops the stack
# and shuts Windows down, leaving the UPS's runtime to skippy-core and the cameras. skippy-core wakes it when the power is back
# (core/install/ups.sh). Win32_Battery.BatteryStatus: 1 discharging, 4 low, 5 critical, 11 partially charged on battery.
UPS_SHUTDOWN_AFTER_S = int(os.environ.get("UPS_SHUTDOWN_AFTER_S", "120"))
UPS_ON_BATTERY = (1, 4, 5, 11)
UPS_PS = ("Get-CimInstance Win32_Battery | Select-Object -First 1 BatteryStatus,EstimatedChargeRemaining,EstimatedRunTime | "
          "ConvertTo-Json -Compress")
BUILDING_PS = ("Get-CimInstance Win32_Process -Filter \"Name='docker.exe' or Name='docker-compose.exe'\" | "
               "ForEach-Object { $_.CommandLine }")      # read-only: is anyone building right now? (a session's own rebuild)


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
        self.on_battery_since: float | None = None
        self.power_down = False                   # the UPS shutdown has started: nothing else runs
        self._ups: dict = {}

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

    def container_list(self) -> list[dict]:
        """Every skippytel- container for the portal's Status map: name, state, status text, image (portal/status.py clean_containers)."""
        rc, out = self.run(["docker", "ps", "-a", "--filter", "name=skippytel-", "--format", "{{json .}}"], 20)
        rows = []
        for line in (out.splitlines() if rc == 0 else []):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("Names"):
                rows.append({"name": r["Names"], "state": r.get("State", ""), "status": r.get("Status", ""), "image": r.get("Image", "")})
        return rows

    def bridge(self) -> dict | None:
        try:
            return self.http(f"{BRIDGE_URL}/health", timeout=4)
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def gpu(self) -> dict:
        """skippy-pc's card, from nvidia-smi: how much of its memory is free and how busy it is (the portal starts a patient job, a
        picture or a song, only when it is mostly free: portal/patient.py). Empty when there is no nvidia-smi or it doesn't answer."""
        rc, out = self.run(["nvidia-smi", "--query-gpu=memory.total,memory.used,utilization.gpu", "--format=csv,noheader,nounits"], 15)
        try:
            total, used, util = (float(x) for x in out.splitlines()[0].split(","))
        except (ValueError, IndexError):
            return {}
        if rc != 0 or total <= 0:
            return {}
        return {"gpu_free_pct": int(round(100 * (total - used) / total)), "gpu_util_pct": int(round(util))}

    def ups(self) -> dict:
        """skippy-pc's UPS as Windows sees it: on battery or not, charge and minutes of runtime. Empty when there is none."""
        rc, out = self.run(["powershell.exe", "-NoProfile", "-Command", UPS_PS], 20)
        try:
            b = json.loads(out) if rc == 0 and out.strip() else None
        except ValueError:
            b = None
        if not isinstance(b, dict) or not isinstance(b.get("BatteryStatus"), int):
            self._ups = {}
            return {}
        num = lambda v: v if isinstance(v, int) and 0 <= v <= 999 else 0   # noqa: E731 (Windows says 71582788 for "unknown")
        self._ups = {"ups_on_battery": b["BatteryStatus"] in UPS_ON_BATTERY, "ups_charge": num(b.get("EstimatedChargeRemaining")),
                     "ups_runtime_min": num(b.get("EstimatedRunTime"))}
        return self._ups

    def watch_power(self) -> bool:
        """On battery for UPS_SHUTDOWN_AFTER_S: stop the bridge and the containers cleanly and shut Windows down (the power returning
        before then cancels it). Works with the portal and the internet down. Returns whether it started the shutdown."""
        if self.power_down:
            return False
        if not self.ups().get("ups_on_battery"):
            self.on_battery_since = None
            return False
        if self.on_battery_since is None:
            self.on_battery_since = self.clock()
            self.say("UPS: on battery")
            return False
        if self.clock() - self.on_battery_since < UPS_SHUTDOWN_AFTER_S:
            return False
        self.power_down, self.working = True, "UPS shutdown"
        self.say("UPS: on battery too long: stopping the stack and shutting down")
        with self._lock:
            self.results.append({"id": "ups-" + self.now().strftime("%H%M"), "ok": True,
                                 "summary": f"On battery for {UPS_SHUTDOWN_AFTER_S // 60} min: skippy-pc is shutting down."})
        try:
            self.poll_once(threaded=False)                    # tell the portal first (it DMs the Captain), while the network is up
        except Exception:  # noqa: BLE001 — going down regardless
            pass
        for pid in (self.supervisor_pid(), self.bridge_pid()):
            if pid:
                self.kill(pid)
        if self.docker_up():
            self.run(["docker", "compose", "stop"], 120, cwd=str(self.root))
        self.run(["shutdown", "/s", "/t", "20", "/c", "SkippyTel: on UPS battery, shutting down"], 30)
        return True

    def status(self) -> dict:
        up, total = self.containers() if self.docker_up() else (0, 0)
        b = self.bridge()
        return {"docker": total > 0 or self.docker_up(), "containers_up": up, "containers_total": total, "bridge": b is not None,
                "busy": bool(b and (b.get("workshop") or {}).get("busy")), "supervisor": b is not None, "working": self.working,
                "resume_at": self.resume_at.isoformat(timespec="minutes") if self.resume_at else "", **self.gpu(), **self._ups,
                "containers": self.container_list() if self.docker_up() else []}

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
        for pid in (self.supervisor_pid(), self.bridge_pid()):          # the supervisor first, so it doesn't start the bridge again
            if pid:
                self.kill(pid)
        self.working = "stopping the containers"
        rc, out = self.run(["docker", "compose", "stop"], 180, cwd=str(self.root)) if self.docker_up() else (0, "")
        if rc != 0:
            return False, f"The bridge is stopped but the containers did not all stop: {out[-200:]}"
        when = self.resume_time(resume)
        self._save_resume(when)
        back = f" It starts itself again at {when.strftime('%a %I:%M %p').replace(' 0', ' ')}." if when else " It stays down until you start it."
        return True, "Stopped: the bridge and every container. Docker Desktop and the starter are still up." + back

    # ── by PID only (never by name: a name filter once hit another session's servers) ──
    def bridge_pid(self) -> int | None:
        """The process listening on the bridge's port, from `netstat -ano`."""
        rc, out = self.run(["netstat", "-ano", "-p", "TCP"], 15)
        for line in out.splitlines() if rc == 0 else []:
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{BRIDGE_PORT}") and parts[3] == "LISTENING" and parts[4].isdigit():
                return int(parts[4])
        return None

    def supervisor_pid(self) -> int | None:
        try:
            pid = int((self.root / "state" / "bridge-supervisor.pid").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        rc, out = self.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], 15)
        return pid if rc == 0 and f'"{pid}"' in out else None

    def kill(self, pid: int) -> None:
        self.run(["taskkill", "/PID", str(pid), "/T", "/F"], 30)

    def services(self) -> list[str]:
        rc, out = self.run(["docker", "compose", "config", "--services"], 60, cwd=str(self.root))
        return out.split() if rc == 0 else []

    def someone_building(self) -> bool:
        rc, out = self.run(["powershell.exe", "-NoProfile", "-Command", BUILDING_PS], 30)
        return rc == 0 and any(("--build" in line or " build" in line) for line in out.splitlines())

    # ── healing one thing (the doctor), and putting everything back (the button) ──
    def restart_docker(self) -> tuple[bool, str]:
        self.working = "restarting Docker clean"
        self.run(["powershell.exe", "-NoProfile", "-File", CLEAN_PS1], 120)
        self.spawn([DOCKER_EXE])
        if not self._wait(self.docker_up, DOCKER_WAIT_S + DOCKER_CLEAN_WAIT_S):
            return False, "Docker Desktop would not come back, even after the clean restart: the PC needs a look in person."
        rc, out = self.run(["docker", "compose", "up", "-d"], 600, cwd=str(self.root))
        up, total = self.containers()
        return rc == 0, f"Docker restarted clean; {up} of {total} containers up."

    def restart_bridge(self) -> tuple[bool, str]:
        b = self.bridge()
        if b and (b.get("workshop") or {}).get("busy"):
            return False, "The bridge is in the middle of a run: left alone."
        self.working = "restarting the bridge"
        pid = self.bridge_pid()
        if pid:
            self.kill(pid)
        if self.supervisor_pid() and self._wait(lambda: self.bridge() is not None, BRIDGE_WAIT_S):
            return True, "Bridge restarted (its supervisor brought it back)."
        env = {**os.environ, "CLAUDE_BRIDGE_CWD": BRIDGE_CWD}
        self.spawn([os.environ.get("BASH_EXE", "bash"), "-l", str(self.root / "tools" / "bridge_supervisor.sh")], env=env, log=str(self.root / "state" / "starter-bridge.log"))
        if self._wait(lambda: self.bridge() is not None, BRIDGE_WAIT_S):
            return True, "Bridge started again, with its supervisor."
        return False, "The bridge did not come back (look at state/bridge-supervisor.log)."

    def recreate(self, service: str) -> tuple[bool, str]:
        self.working = f"recreating {service}"
        for cmd, t in ((["docker", "compose", "stop", service], 180), (["docker", "compose", "rm", "-f", service], 60),
                       (["docker", "compose", "up", "-d", "--build", service], 1200)):
            rc, out = self.run(cmd, t, cwd=str(self.root))
            if rc != 0:
                return False, f"{service}: `{' '.join(cmd[:3])}` failed: {out[-160:]}"
        return True, f"{service} recreated."

    def repair_core(self) -> tuple[bool, str]:
        self.working = "repairing Skippy Core"
        rc, out = self.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", CORE_SSH, *CORE_REPAIR], 3600)
        last = (out.strip().splitlines() or [""])[-1]
        try:
            row = json.loads(last)
        except ValueError:
            row = {}
        if rc == 0 and row.get("state") == "ok":
            return True, "Skippy Core repaired (recreated and smoke-tested)."
        if "a deploy or repair is going" in out:
            return True, "Skippy Core is already deploying or repairing itself (it ends with its own smoke test)."
        return False, "Skippy Core: " + (row.get("detail") or row.get("state") or out[-160:] or f"ssh said {rc}")

    def heal(self, name: str) -> tuple[bool, str]:
        if name == "docker":
            return self.restart_docker()
        if not self.docker_up() and name not in ("core",):
            return False, "Docker isn't answering: that comes first (heal docker)."
        if name == "bridge":
            return self.restart_bridge()
        if name == "core":
            return self.repair_core()
        if name not in self.services():
            return False, "I do not do that."
        if self.someone_building():
            return False, f"Someone is building on this PC right now: {name} is left alone until they finish."
        return self.recreate(name)

    def repair(self, failing: str = "") -> tuple[bool, str]:
        """Everything back to baseline: Docker answering (restarted clean if hung), main pulled into this checkout when that is a
        fast-forward (never discarding anything), every container up from it, the failing ones recreated, the bridge restarted when it
        isn't mid-run, Skippy Core repaired. Code is never reverted."""
        said, ok = [], True
        if not self.docker_up():
            good, text = self.restart_docker()
            said.append(text)
            if not good:
                return False, " ".join(said)
        self.working = "pulling main"
        if self.run(["git", "-C", str(self.root), "fetch", "--quiet", "origin", "main"], 120)[0] == 0:
            branch = self.run(["git", "-C", str(self.root), "rev-parse", "--abbrev-ref", "HEAD"], 15)[1].strip()
            if branch == "main":
                rc, out = self.run(["git", "-C", str(self.root), "merge", "--ff-only", "--quiet", "origin/main"], 120)
                said.append("main pulled." if rc == 0 else "main not pulled (local changes in the way: left exactly as they are).")
            else:
                said.append(f"the checkout is on {branch}, not main: left as it is.")
        self.working = "bringing every container up"
        rc, out = self.run(["docker", "compose", "up", "-d", "--build", "--remove-orphans"], 1800, cwd=str(self.root))
        up, total = self.containers()
        said.append(f"{up} of {total} containers up.")
        ok = ok and rc == 0
        known = set(self.services())
        for name in [n for n in failing.split(",") if n in known]:
            good, text = self.recreate(name)
            said.append(text)
            ok = ok and good
        good, text = self.restart_bridge()
        said.append(text)
        ok = ok and (good or "middle of a run" in text)
        good, text = self.repair_core()
        said.append(text)
        return ok and good, " ".join(said)

    def restart(self) -> tuple[bool, str]:
        ok, text = self.stop("manual")
        if not ok:
            return False, text
        ok2, text2 = self.start()
        return ok2, "Restarted. " + text2 if ok2 else text2

    def do(self, op: str, resume: str = "7am", arg: str = "") -> tuple[bool, str]:
        """Only an operation in OPS runs. Never raises."""
        if op not in OPS or (op == "stop" and resume not in RESUMES):
            return False, "I do not do that."
        if op == "heal" and not (arg in HEAL_SPECIAL or arg.replace("-", "").isalnum()):
            return False, "I do not do that."
        if op == "repair" and arg and not all(a.replace("-", "").isalnum() for a in arg.split(",")):
            return False, "I do not do that."
        try:
            if op == "heal":
                return self.heal(arg)
            if op == "repair":
                return self.repair(arg)
            return self.stop(resume) if op == "stop" else {"start": self.start, "restart": self.restart}[op]()
        except Exception as e:                                         # noqa: BLE001 — a failure is a result, not a crash
            return False, f"It went wrong ({type(e).__name__})."
        finally:
            self.working = ""

    # ── talking to the portal ──────────────────────────────────────────────────
    def _work(self, cid: str, op: str, resume: str = "7am", arg: str = "") -> None:
        ok, summary = self.do(op, resume, arg)
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
            arg = c.get("arg") if isinstance(c, dict) and isinstance(c.get("arg"), str) else ""
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
                threading.Thread(target=self._work, args=(cid, op, resume, arg[:300]), daemon=True).start()
            else:
                self._work(cid, op, resume, arg[:300])
        for old in [k for k, t in self.handled.items() if self.clock() - t > 3600]:
            self.handled.pop(old, None)
        return commands

    def watch_docker(self) -> bool:
        """Docker hung or down for over two minutes, and not stopped on purpose: the clean restart, by itself (it works with the portal out
        of reach). Returns whether it acted."""
        if self.working or self.resume_at is not None:
            self.docker_down_since = None
            return False
        if self.docker_up():
            self.docker_down_since = None
            return False
        since = getattr(self, "docker_down_since", None)
        if since is None:
            self.docker_down_since = self.clock()
            return False
        if self.clock() - since < DOCKER_DOWN_HEAL_S:
            return False
        self.working = "heal docker"
        try:
            ok, summary = self.restart_docker()
        finally:
            self.working = ""
        self.docker_down_since = None
        with self._lock:
            self.results.append({"id": "auto-docker" + self.now().strftime("%H%M"), "ok": ok, "summary": "By itself: " + summary})
        self.say(f"docker watch: {'ok' if ok else 'FAILED'}: {summary}")
        return True

    def forever(self) -> None:
        while True:
            try:
                if self.watch_power():                                              # going down: no more starting things
                    return
                self.check_resume()
                self.watch_docker()
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
