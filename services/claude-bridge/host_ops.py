"""The Admin page's skippy-pc buttons, carried out here on the host (the bridge is the one piece of the chain that runs on
Windows itself, with docker, git and tailscale on its PATH; the home reporter is a container and can't touch them).

The chain: Admin button -> portal job `host_op` -> home reporter -> POST /host/<op> on this bridge (home token) -> here.

Four operations, each a fixed command list; nothing a person types becomes part of a command line except a name that was
first found in an allow-list:

  restart-service <name>   `docker compose restart <name>` for one of SERVICES (no rebuild: the same image, a fresh process)
  restart-bridge           this bridge exits and tools/bridge_supervisor.sh starts it again (refused when no supervisor runs:
                           nothing would bring it back)
  reset-workshop <project> the private Skippy's own clone of that project back to origin/<branch>. Nothing is thrown away:
                           uncommitted work goes into a stash, unpushed commits stay on a rescue/<stamp> branch. Refused
                           while a run is going
  funnel on|off|status     Tailscale Funnel's two doors: 443 -> music gate (3005), 8443 -> video gate (3006)

Pure where it can be: `runner` (subprocess) and `clock` are arguments, so the tests drive it without docker or git.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SERVICES = ("flask-api", "ollama", "whisper", "music-studio", "music-gate", "video-gate", "home-reporter", "mentat-home")
COMPOSE_DIR = os.getenv("SKIPPY_COMPOSE_DIR", r"D:\Aaron\development\SkippyTel")
SUPERVISOR_PID = Path(COMPOSE_DIR) / "state" / "bridge-supervisor.pid"
FUNNEL_DOORS = (("443", "3005"), ("8443", "3006"))     # (public https port, local port): music gate, video gate
OUTPUT_MAX = 1500
TIMEOUT_S = 120
SELF_RESTART_DELAY_S = 1.5        # long enough for the reply to leave before this process (or the reporter) goes away


class Refused(Exception):
    """The operation can't be done right now (or with that argument). The message is a plain sentence for the card."""


@dataclass
class Result:
    ok: bool
    summary: str                  # one line for the card
    output: str = ""              # the commands' own words, trimmed


def _tool(name: str, fallback: str) -> str:
    return shutil.which(name) or (fallback if os.path.exists(fallback) else name)


DOCKER = _tool("docker", r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
TAILSCALE = _tool("tailscale", r"C:\Program Files\Tailscale\tailscale.exe")
GIT = _tool("git", r"C:\Program Files\Git\cmd\git.exe")


def default_runner(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_S,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
    except FileNotFoundError:
        return 127, f"{cmd[0]} isn't installed on this PC"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(cmd[:3])} took longer than {TIMEOUT_S} s"
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def _trim(text: str) -> str:
    text = text.strip()
    return text if len(text) <= OUTPUT_MAX else "…" + text[-OUTPUT_MAX:]


def supervised(pidfile: Path = SUPERVISOR_PID) -> bool:
    """Is tools/bridge_supervisor.sh running (its pid file names a live process)? Only then may the bridge exit on request."""
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, SystemExit):
        return False
    return True


class HostOps:
    def __init__(self, workshop, runner=default_runner, later=None, is_supervised=supervised, clock=time.time):
        self.workshop = workshop                  # workshop_engine.Workshop: busy() and project(id)
        self.run = runner
        self.later = later or self._later         # later(seconds, fn): the restarts that end this process or the caller
        self.is_supervised = is_supervised
        self.clock = clock
        self._lock = threading.Lock()             # one operation at a time

    @staticmethod
    def _later(seconds: float, fn) -> None:
        t = threading.Timer(seconds, fn)
        t.daemon = True
        t.start()

    def do(self, op: str, arg: str = "") -> Result:
        if not self._lock.acquire(blocking=False):
            raise Refused("Another skippy-pc operation is still running: try again in a minute.")
        try:
            if op == "restart-service":
                return self.restart_service(arg)
            if op == "restart-bridge":
                return self.restart_bridge()
            if op == "reset-workshop":
                return self.reset_workshop(arg)
            if op == "funnel":
                return self.funnel(arg)
            raise Refused(f"Unknown operation {op!r}.")
        finally:
            self._lock.release()

    # ── docker ───────────────────────────────────────────────────────────
    def restart_service(self, name: str) -> Result:
        if name not in SERVICES:
            raise Refused(f"{name!r} isn't one of the services this button restarts.")
        cmd = [DOCKER, "compose", "restart", name]
        if name == "home-reporter":
            # The reporter is the one asking: restarting it now would cut off this answer. Reply first, restart after.
            self.later(SELF_RESTART_DELAY_S, lambda: self.run(cmd, COMPOSE_DIR))
            return Result(True, "home-reporter restarts in a moment; skippy-pc goes quiet for about 30 s.")
        code, out = self.run(cmd, COMPOSE_DIR)
        if code != 0:
            return Result(False, f"docker compose restart {name} failed (exit {code}).", _trim(out))
        _, ps = self.run([DOCKER, "compose", "ps", name, "--format", "{{.Name}} {{.Status}}"], COMPOSE_DIR)
        return Result(True, f"{name} restarted.", _trim(ps))

    # ── the bridge itself ────────────────────────────────────────────────
    def restart_bridge(self) -> Result:
        if not self.is_supervised():
            raise Refused("The bridge isn't running under tools/bridge_supervisor.sh, so nothing would start it again. "
                          "Not restarting it.")
        if self.workshop.busy():
            raise Refused("The private Skippy is in the middle of a run; cancel it first (the card above).")
        self.later(SELF_RESTART_DELAY_S, lambda: os._exit(0))
        return Result(True, "The bridge restarts now; its supervisor brings it back within a few seconds.")

    # ── the private Skippy's clone ───────────────────────────────────────
    def reset_workshop(self, project_id: str) -> Result:
        try:
            project = self.workshop.project(project_id or None)
        except KeyError:
            raise Refused(f"skippy-pc has no project {project_id!r}.") from None
        if self.workshop.busy():
            raise Refused("The private Skippy is in the middle of a run; cancel it first (the card above).")
        clone = project.clone
        if not os.path.isdir(os.path.join(clone, ".git")):
            return Result(True, f"{project.name} has no clone yet: the next run makes a fresh one.")
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        steps: list[str] = []

        def git(*args: str) -> tuple[int, str]:
            code, out = self.run([GIT, *args], clone)
            steps.append(f"$ git {' '.join(args)}" + (f"\n{out}" if out else ""))
            return code, out

        code, _ = git("fetch", "--prune", "origin")
        if code != 0:
            return Result(False, f"Couldn't fetch {project.repo}; the clone is untouched.", _trim("\n".join(steps)))
        kept = []
        _, dirty = git("status", "--porcelain")
        if dirty.strip():
            code, _ = git("stash", "push", "--include-untracked", "-m", f"rescue {stamp} (Admin reset)")
            if code != 0:
                return Result(False, "Couldn't stash the uncommitted work; the clone is untouched.", _trim("\n".join(steps)))
            kept.append("uncommitted work in `git stash list`")
        _, ahead = git("rev-list", "--count", f"origin/{project.branch}..HEAD")
        if ahead.strip().isdigit() and int(ahead.strip()) > 0:
            git("branch", f"rescue/{stamp}", "HEAD")
            kept.append(f"{ahead.strip()} unpushed commit(s) on branch rescue/{stamp}")
        code, _ = git("checkout", "-B", project.branch, f"origin/{project.branch}")
        if code != 0:
            return Result(False, "The checkout failed; see the output.", _trim("\n".join(steps)))
        git("reset", "--hard", f"origin/{project.branch}")
        summary = f"{project.name}'s clone is back on origin/{project.branch}."
        if kept:
            summary += " Kept: " + "; ".join(kept) + "."
        return Result(True, summary, _trim("\n".join(steps)))

    # ── Tailscale Funnel ─────────────────────────────────────────────────
    def funnel(self, what: str) -> Result:
        if what not in ("on", "off", "status"):
            raise Refused("Funnel takes on, off or status.")
        outs, ok = [], True
        if what != "status":
            for public, local in FUNNEL_DOORS:
                cmd = [TAILSCALE, "funnel", f"--https={public}", "--bg", local] if what == "on" else \
                      [TAILSCALE, "funnel", f"--https={public}", "off"]
                code, out = self.run(cmd)
                ok = ok and code == 0
                outs.append(f"$ tailscale funnel --https={public} {'--bg ' + local if what == 'on' else 'off'}\n{out}".strip())
        _, status = self.run([TAILSCALE, "funnel", "status"])
        outs.append(status)
        word = {"on": "Funnel is on: music on :443, video on :8443.", "off": "Funnel is off: the audio and video "
                "services can't be reached from outside until it's back on.", "status": "Funnel's current state:"}[what]
        return Result(ok, word if ok else "Funnel didn't do all of that; see the output.", _trim("\n\n".join(outs)))
