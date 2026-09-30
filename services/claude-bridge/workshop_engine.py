"""The private Skippy's engine: Claude Code (Opus, full permissions) working on
the SkippyTel repo for the Captain, driven from his portal dashboard.

The portal can't reach this PC, so the home reporter carries each chat message
here (POST /workshop), then reads the run's events back (GET /workshop/run/<task>)
and hands them to the portal. A run lives here, keyed by the portal's task id:
asking again with the same id re-attaches instead of starting a second run, so
a reporter or portal restart (a deploy!) never loses or repeats work.

The model is hard-coded (MODEL): no environment variable and no request field
can change it. The agent works in its own clone of the repo (REPO), never the
Captain's checkout, and keeps one Claude Code session per chat conversation so
"do the next thing" has the context of the last one. Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

MODEL = "claude-opus-5-5"          # Opus, latest, on purpose: nothing else picks it
REPO = os.getenv("WORKSHOP_REPO", r"D:\Aaron\development\SkippyTel-workshop")
PRIMARY = os.getenv("WORKSHOP_PRIMARY", r"D:\Aaron\development\SkippyTel")
REMOTE = os.getenv("WORKSHOP_REMOTE", "https://github.com/aaronsrhodes2/skippy-tel.git")
SESSIONS = os.getenv("WORKSHOP_SESSIONS", os.path.join(os.path.dirname(REPO), ".skippy-workshop-sessions.json"))
PROMPT_FILE = Path(__file__).with_name("workshop_prompt.md")
ENV_FILES = ("G:/My Drive/vault/.env",)        # where SKIPPY_HOME_TOKEN lives if the environment doesn't have it

MAX_MESSAGE = 8000
MAX_TEXT = 4000                    # one text event
MAX_EVENTS = 5000
KEEP_FINISHED_S = 3600
GIT_TIMEOUT = 180
_CONV = re.compile(r"[A-Za-z0-9-]{8,64}")
_TASK = re.compile(r"[a-f0-9]{8,32}")

# Secrets that might appear in a command line the agent runs; the chat shows commands, never their output.
_SECRETS = re.compile(
    r"(?i)(bearer\s+\S+|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}"
    r"|(?:token|secret|password|passwd|api[_-]?key)\s*[=:]\s*\S+)")


class Busy(Exception):
    """Another task is running."""


def redact(text: str) -> str:
    return _SECRETS.sub("[redacted]", text)


def home_token(env=os.environ, files=ENV_FILES) -> str:
    """SKIPPY_HOME_TOKEN, the reporter's bearer token, which also guards this endpoint.
    From the environment, else the vault .env. Empty means the endpoint stays shut."""
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


def tool_detail(name: str, args: dict) -> str:
    """What a tool call is about, for the chat's activity line: the command or the path, never file contents."""
    args = args if isinstance(args, dict) else {}
    if name == "Bash":
        lines = str(args.get("command") or "").strip().splitlines()
        return redact(lines[0])[:160] if lines else ""
    for key in ("file_path", "path", "pattern", "url", "description"):
        if args.get(key):
            return redact(str(args[key]))[:160]
    return ""


def friendly_error(text: str) -> str:
    if text.lower().startswith("failed to authenticate"):
        return ("Claude Code on skippy-pc isn't logged in (its sign-in expired). "
                "Run `claude` in a terminal on skippy-pc and use /login, then try again.")
    return text[:500] or "Claude Code failed."


class Run:
    """One chat message being worked on: an append-only list of events, readable while it grows."""

    def __init__(self, task: str, conv: str, message: str):
        self.task, self.conv, self.message = task, conv, message
        self.events: list[dict] = []
        self.done, self.ok, self.error = False, None, None
        self.cancelled = False
        self.proc: subprocess.Popen | None = None
        self.finished_at: float | None = None
        self._cond = threading.Condition()

    def emit(self, event: dict) -> None:
        with self._cond:
            if len(self.events) < MAX_EVENTS:
                self.events.append(event)
            self._cond.notify_all()

    def finish(self, ok: bool, error: str | None = None) -> None:
        with self._cond:
            self.done, self.ok, self.error, self.finished_at = True, ok, error, time.time()
            self._cond.notify_all()

    def since(self, after: int, wait: float = 0.0) -> dict:
        """Events from index `after`; with `wait`, hold the request until there's news or it's over."""
        after = max(0, after)
        with self._cond:
            if wait > 0:
                self._cond.wait_for(lambda: len(self.events) > after or self.done, timeout=wait)
            return {"events": self.events[after:], "next": len(self.events), "done": self.done,
                    "ok": self.ok, "error": self.error}


def _kill_tree(proc: subprocess.Popen) -> None:
    """claude starts shells and tools of its own; stop the lot."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15)
        else:
            proc.kill()
    except Exception:  # noqa: BLE001 — already gone
        pass


class Workshop:
    def __init__(self, claude_cmd: list[str], repo: str = REPO, primary: str = PRIMARY,
                 sessions: str = SESSIONS, remote: str = REMOTE, prompt_file: Path = PROMPT_FILE):
        self.claude_cmd, self.repo, self.primary = list(claude_cmd), repo, primary
        self.sessions_path, self.remote, self.prompt_file = sessions, remote, prompt_file
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    # ── runs ─────────────────────────────────────────────────────────────
    def start(self, task: str, conv: str, message: str) -> Run:
        """Begin a task, or re-attach if it's already known. One task at a time."""
        if not _TASK.fullmatch(task or "") or not _CONV.fullmatch(conv or ""):
            raise ValueError("bad task or conversation id")
        message = (message or "").strip()[:MAX_MESSAGE]
        if not message:
            raise ValueError("empty message")
        with self._lock:
            self._prune()
            if task in self._runs:
                return self._runs[task]
            if any(not r.done for r in self._runs.values()):
                raise Busy("Skippy is still working on the last thing")
            run = self._runs[task] = Run(task, conv, message)
        threading.Thread(target=self._work, args=(run,), daemon=True).start()
        return run

    def get(self, task: str) -> Run | None:
        return self._runs.get(task)

    def cancel(self, task: str) -> bool:
        run = self._runs.get(task)
        if not run or run.done:
            return False
        run.cancelled = True
        if run.proc:
            _kill_tree(run.proc)
        return True

    def busy(self) -> bool:
        return any(not r.done for r in self._runs.values())

    def _prune(self) -> None:
        now = time.time()
        for task in [t for t, r in self._runs.items() if r.done and now - (r.finished_at or now) > KEEP_FINISHED_S]:
            del self._runs[task]

    # ── sessions: one Claude Code session per chat conversation ──────────
    def _sessions(self) -> dict:
        try:
            data = json.loads(Path(self.sessions_path).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _remember(self, conv: str, session: str) -> None:
        data = self._sessions()
        data[conv] = session
        path = Path(self.sessions_path)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass                                   # the next message starts a fresh session; not fatal

    # ── the work ─────────────────────────────────────────────────────────
    def prompt(self) -> str:
        return self.prompt_file.read_text(encoding="utf-8").replace("{repo}", self.repo).replace("{primary}", self.primary)

    def _git(self, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=GIT_TIMEOUT)

    def _prepare_repo(self, run: Run) -> bool:
        """Your own clone, on the latest main. False (with the run failed) if there's no clone to work in."""
        try:
            if not (Path(self.repo) / ".git").exists():
                run.emit({"t": "tool", "name": "git", "detail": "cloning the repo for the first time"})
                out = self._git("clone", self.remote, self.repo)
                if out.returncode != 0:
                    run.finish(False, "couldn't clone the repo: " + redact(out.stderr.strip())[:300])
                    return False
                return True
            if self._git("status", "--porcelain", cwd=self.repo).stdout.strip():
                run.emit({"t": "text", "text": "(my clone has uncommitted changes from an earlier task, so I'm not pulling)"})
                return True
            out = self._git("pull", "--ff-only", cwd=self.repo)
            if out.returncode != 0:
                run.emit({"t": "text", "text": "(couldn't fast-forward my clone: " + redact(out.stderr.strip())[:200] + ")"})
        except (OSError, subprocess.SubprocessError) as exc:
            run.finish(False, f"git isn't working: {exc}")
            return False
        return True

    def command(self, session: str | None) -> list[str]:
        cmd = [*self.claude_cmd, "-p", "--output-format", "stream-json", "--verbose",
               "--model", MODEL, "--dangerously-skip-permissions",
               "--append-system-prompt", self.prompt(), "--add-dir", self.primary]
        if session:
            cmd += ["--resume", session]
        return cmd

    def _work(self, run: Run) -> None:
        try:
            if not self._prepare_repo(run):
                return
            session = self._sessions().get(run.conv)
            self._attempt(run, session)
        except Exception as exc:  # noqa: BLE001 — a crash must end the run, or the chat waits forever
            run.finish(False, f"the bridge hit a snag: {exc}"[:300])

    def _attempt(self, run: Run, session: str | None) -> None:
        if run.cancelled:
            run.finish(False, "stopped")
            return
        env ={k: v for k, v in os.environ.items() if k != "SKIPPY_HOME_TOKEN"}   # the agent never gets the key to this door
        try:
            proc = subprocess.Popen(self.command(session), cwd=self.repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                    env=env, bufsize=1)
        except FileNotFoundError as exc:
            run.finish(False, f"can't start Claude Code: {exc}")
            return
        run.proc = proc
        if run.cancelled:                     # Stop arrived while it was starting: cancel() found no process to kill
            _kill_tree(proc)
        try:
            proc.stdin.write(run.message)
            proc.stdin.close()
        except OSError:
            pass
        pending: str | None = None            # the latest text block, held back one event so an error can replace it
        seen_session, failure, produced = None, None, False
        for line in proc.stdout:
            try:
                evt = json.loads(line)
            except ValueError:
                continue
            kind = evt.get("type")
            if evt.get("session_id"):
                seen_session = evt["session_id"]
            if kind == "assistant":
                for block in (evt.get("message") or {}).get("content") or []:
                    if block.get("type") == "text" and (block.get("text") or "").strip():
                        produced = True
                        if pending is not None:
                            run.emit({"t": "text", "text": pending})
                        pending = block["text"].strip()[:MAX_TEXT]
                    elif block.get("type") == "tool_use":
                        produced = True
                        if pending is not None:
                            run.emit({"t": "text", "text": pending})
                            pending = None
                        name = str(block.get("name") or "tool")
                        run.emit({"t": "tool", "name": name, "detail": tool_detail(name, block.get("input"))})
            elif kind == "result":
                if evt.get("is_error"):
                    failure = str(evt.get("result") or "")
        proc.wait()
        if run.cancelled:
            if pending is not None:
                run.emit({"t": "text", "text": pending})
            run.finish(False, "stopped")
            return
        if failure is not None:
            if session and re.search(r"no conversation found", failure, re.I) and not produced:
                run.emit({"t": "text", "text": "(couldn't pick the last session back up; starting a fresh one)"})
                self._attempt(run, None)
                return
            if pending is not None and pending != failure:
                run.emit({"t": "text", "text": pending})
            run.finish(False, friendly_error(failure))
            return
        if pending is not None:
            run.emit({"t": "text", "text": pending})
        if seen_session:
            self._remember(run.conv, seen_session)
        if proc.returncode not in (0, None):
            run.finish(False, f"Claude Code exited with code {proc.returncode}")
        else:
            run.finish(True)
