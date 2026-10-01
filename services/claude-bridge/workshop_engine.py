"""The private Skippy's engine: Claude Code (Opus, full permissions) working on
the SkippyTel repo for the Captain, driven from his portal dashboard.

The portal can't reach this PC, so the home reporter carries each chat message
here (POST /workshop), then reads the run's events back (GET /workshop/run/<task>)
and hands them to the portal. A run lives here, keyed by the portal's task id:
asking again with the same id re-attaches instead of starting a second run, so
a reporter or portal restart (a deploy!) never loses or repeats work. Each run is also
saved to disk (one file per task, next to the sessions file), so a restart of the bridge
itself keeps the finished ones deliverable and ends the interrupted ones with a plain error.

The model is hard-coded (MODEL): no environment variable and no request field
can change it. The agent works in its own clone of the repo (REPO), never the
Captain's checkout, and keeps one Claude Code session per chat conversation so
"do the next thing" has the context of the last one.

Two rules about how Claude Code is started, both learned the hard way on Windows (npm's `claude` is claude.cmd, so
every argument goes through cmd.exe):
  * NO argument may contain a newline. cmd.exe cuts the command line at the first one, so a multi-line prompt arrived
    as its first line only and every argument after it (--resume, --add-dir) was silently dropped: the Captain's
    guardrails were never in force. The prompt now goes in a file (--append-system-prompt-file) and the message goes by
    stdin; command() and _spawn() refuse an argument with a newline outright.
  * A run has a clock (MAX_RUN_S, IDLE_S) and its processes can never wait for a person (GIT_TERMINAL_PROMPT=0,
    GCM_INTERACTIVE=never): nobody is at the PC to answer a credential prompt, and one hung run used to lock the chat
    until the bridge was restarted. Stdlib only.
"""
from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import conversation_log
import projects as projects_mod

MODEL = "claude-opus-5-5"          # Opus, latest, on purpose: nothing else picks it
REPO = os.getenv("WORKSHOP_REPO", r"D:\Aaron\development\SkippyTel-workshop")
PRIMARY = os.getenv("WORKSHOP_PRIMARY", r"D:\Aaron\development\SkippyTel")
REMOTE = os.getenv("WORKSHOP_REMOTE", "https://github.com/aaronsrhodes2/skippy-tel.git")
SESSIONS = os.getenv("WORKSHOP_SESSIONS", os.path.join(os.path.dirname(REPO), ".skippy-workshop-sessions.json"))
PROMPT_FILE = Path(__file__).with_name("workshop_prompt.md")
PROMPT_NAME = ".skippy-workshop-prompt.txt"   # the prompt as claude reads it ({repo}/{primary} filled in), next to the sessions file
ENV_FILES = ("G:/My Drive/vault/.env",)        # where SKIPPY_HOME_TOKEN lives if the environment doesn't have it

MAX_MESSAGE = 8000
MAX_WHO = 200                      # the owner's email, which the bridge only carries (it never decides anything by it)
MAX_TEXT = 4000                    # one text event
MAX_EVENTS = 5000
MAX_LISTED = 50                    # runs GET /workshop/runs names
KEEP_FINISHED_S = 3600
SAVE_EVERY_S = 5.0                 # a running run is written to disk at most this often (and always when it finishes)
SAVE_RETRY_S = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)   # pauses between tries of a forced write that fails (see Workshop._save)
STATE_DIR_NAME = ".skippy-workshop-runs"   # next to the sessions file: one <task>.json per run
RESTART_ERROR = "The bridge restarted while this was running."
LOGIN_TTL_S = 120.0                # how long one answer from `claude auth status` is trusted
LOGIN_TIMEOUT_S = 15.0
GIT_TIMEOUT = 180
MAX_RUN_S = 2 * 3600.0             # a run still going after this long is stopped (the watchdog; see Workshop._attempt)
IDLE_S = 30 * 60.0                 # ... and so is one that printed nothing for this long (a long test run is silent for minutes, not half an hour)
WATCH_TICK_S = 0.5                 # how often the run's thread looks at the clock (and at Stop) while claude is quiet
REAP_S = 30.0                      # how long a process that has closed its output, or been killed, gets to exit before it is killed again
SHIP_GIT_TIMEOUT = 30              # each git call of the ship check (a fetch included): it runs as the run ends
_CONV = re.compile(r"[A-Za-z0-9-]{8,64}")
_TASK = re.compile(r"[a-f0-9]{8,32}")
_SHA = re.compile(r"[0-9a-f]{40}")

# Secrets that might appear in a command line the agent runs; the chat shows commands, never their output.
_SECRETS = re.compile(
    r"(?i)(bearer\s+\S+|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}"
    r"|(?:token|secret|password|passwd|api[_-]?key)\s*[=:]\s*\S+)")


class Busy(Exception):
    """Another task is running."""


class UnknownTask(LookupError):
    """A re-attach named a task this bridge doesn't hold. Re-attaching must never start a run."""


def redact(text: str) -> str:
    return _SECRETS.sub("[redacted]", text)


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass                                       # already gone, or held open for a moment: the next prune gets it


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


_BLOCK = re.compile(r"\{\{#(skippytel|other)\}\}\n?(.*?)\{\{/\1\}\}\n?", re.S)


def render_prompt(template: str, project) -> str:
    """workshop_prompt.md for one project: the {{#skippytel}}...{{/skippytel}} block only for a project with SkippyTel's
    deploy rules, the {{#other}}...{{/other}} block only for the rest (whose own CLAUDE.md says how to test and ship),
    and the placeholders filled in: {repo} (the agent's clone), {primary} (the Captain's checkout), {project} (its name),
    {github} (owner/name) and {branch}. A template without blocks is used whole."""
    kind = "skippytel" if project.deploy == "skippytel" else "other"
    text = _BLOCK.sub(lambda m: m.group(2) if m.group(1) == kind else "", template)
    for key, value in (("{repo}", project.clone), ("{primary}", project.primary), ("{project}", project.name),
                       ("{github}", project.repo), ("{branch}", project.branch)):
        text = text.replace(key, value)
    return text


def friendly_error(text: str) -> str:
    if text.lower().startswith("failed to authenticate"):
        return ("Claude Code on skippy-pc isn't logged in (its sign-in expired). "
                "Run `claude` in a terminal on skippy-pc and use /login, then try again.")
    return text[:500] or "Claude Code failed."


class Run:
    """One chat message being worked on: an append-only list of events, readable while it grows."""

    def __init__(self, task: str, conv: str, message: str, who: str = "", created: float | None = None,
                 project: str = projects_mod.DEFAULT):
        self.task, self.conv, self.message, self.who = task, conv, message, who
        self.project = project                 # which repo (projects.json): picks the clone, the checkout and the session
        self.clone = ""                        # that project's clone when the run started (the conversation log reads it)
        self.before_finish = None              # (run) -> None, called once just before the run ends (the ship check)
        self.created = time.time() if created is None else created
        self.events: list[dict] = []
        self.done, self.ok, self.error = False, None, None
        self.cancelled = False
        self.proc: subprocess.Popen | None = None
        self.finished_at: float | None = None
        self._cond = threading.Condition()
        self.on_change = None                  # (run, force) -> None; the Workshop's saver. A Run itself never touches the disk.
        self.save_lock = threading.Lock()      # one writer of this run's file at a time
        self.saved_at = float("-inf")          # time.monotonic() of the last attempt to save it
        self.settled = threading.Event()       # set once finish() has done all it will do, its final save (or giving up) included

    def emit(self, event: dict) -> None:
        with self._cond:
            if len(self.events) < MAX_EVENTS:
                self.events.append(event)
            self._cond.notify_all()
        self._changed(False)

    def finish(self, ok: bool, error: str | None = None) -> None:
        # The ship check (Workshop._ship_check) runs here, BEFORE the run reads as done, so its event is part of the run
        # the relay delivers as final: an event added after "done" could arrive after the chat stopped polling.
        hook, self.before_finish = self.before_finish, None
        if hook is not None:
            try:
                hook(self)
            except Exception:  # noqa: BLE001 — proof of a push is a bonus; it must never stop a run from ending
                pass
        with self._cond:
            self.done, self.ok, self.error, self.finished_at = True, ok, error, time.time()
            self._cond.notify_all()
        self._changed(True)
        self.settled.set()

    def _changed(self, force: bool) -> None:
        if self.on_change is not None:
            self.on_change(self, force)

    def since(self, after: int, wait: float = 0.0) -> dict:
        """Events from index `after`; with `wait`, hold the request until there's news or it's over."""
        after = max(0, after)
        with self._cond:
            if wait > 0:
                self._cond.wait_for(lambda: len(self.events) > after or self.done, timeout=wait)
            return {"events": self.events[after:], "next": len(self.events), "done": self.done,
                    "ok": self.ok, "error": self.error}

    def snapshot(self) -> dict:
        """Everything worth keeping across a bridge restart. The message text is not in it: nothing needs it again."""
        with self._cond:
            return {"task": self.task, "conv": self.conv, "who": self.who, "project": self.project, "created": self.created,
                    "finished_at": self.finished_at, "done": self.done, "ok": self.ok, "error": self.error,
                    "events": list(self.events)}

    def summary(self, now: float) -> dict:
        """What GET /workshop/runs says about a run: counts and state, never the message or any event's content."""
        with self._cond:
            finished = self.finished_at
            return {"task": self.task, "conv": self.conv, "who": self.who, "project": self.project, "events": len(self.events),
                    "done": self.done, "ok": self.ok, "error": self.error,
                    "age_s": round(max(0.0, now - self.created), 1),
                    "finished_age_s": None if finished is None else round(max(0.0, now - finished), 1)}


def child_env(base=None) -> dict:
    """The environment of every process this module starts (claude and git): the door's key is never in it, and nothing
    it runs may wait for a person. Nobody is at the PC to answer a prompt, and Git Credential Manager's sign-in window
    (credential.helper=manager) would sit there forever with the run hanging on it; with these two a missing
    credential fails in seconds with a readable error instead."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if k != "SKIPPY_HOME_TOKEN"}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    return env


def _span(seconds: float) -> str:
    """"2 hours", "30 minutes", "1 second": a duration in the words the chat shows."""
    s = max(1, int(round(seconds)))
    if s >= 3600 and s % 3600 == 0:
        n, unit = s // 3600, "hour"
    elif s >= 60:
        n, unit = max(1, round(s / 60)), "minute"
    else:
        n, unit = s, "second"
    return f"{n} {unit}{'' if n == 1 else 's'}"


def _kill_tree(proc: subprocess.Popen) -> None:
    """claude starts shells and tools of its own; stop the lot."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15)
            return
        try:
            if os.getpgid(proc.pid) == proc.pid:               # it leads its own group (see Workshop._spawn): take the group
                os.killpg(proc.pid, signal.SIGKILL)
                return
        except OSError:
            pass
        proc.kill()
    except Exception:  # noqa: BLE001 — already gone
        pass


# ── is Claude Code signed in on this PC? ─────────────────────────────────
def run_auth_status(claude_cmd: list[str], timeout: float) -> str | None:
    """`claude auth status`'s stdout, or None if it didn't run or didn't answer in time. The command goes through
    Popen as a list exactly like every other claude call here, which is what lets a Windows .cmd shim (npm's
    claude.cmd) run; on a timeout the whole tree dies (taskkill /T), because killing only the shim's cmd.exe would
    leave node running."""
    env = {k: v for k, v in os.environ.items() if k != "SKIPPY_HOME_TOKEN"}     # it needs nothing from this door's key
    try:
        proc = subprocess.Popen([*claude_cmd, "auth", "status"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", env=env)
    except OSError:
        return None
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001 — reaping is best effort; a straggler must not hold this thread
            pass
        return None
    return out


def parse_login(text: str | None) -> str | None:
    """"ok" / "expired" from `auth status`'s JSON ({"loggedIn": true|false, ...}); None when the text isn't that.
    Tolerates a banner line before the JSON. The email and org it also prints are never kept."""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("loggedIn"), bool):
        return "ok" if data["loggedIn"] else "expired"
    return None


class LoginProbe:
    """Learns, in the background, whether `claude` is signed in, so /health can say "Claude's login expired" before a
    chat message fails on it (the Captain's chat otherwise learns it one failed run too late).

    status() NEVER waits: it returns the last answer at once ("unknown" until the first one) and, when that answer
    is older than `ttl`, starts one background check. A check that times out, errors or prints something unreadable
    changes nothing (an "unknown" stays unknown, an "ok" stays ok): a hiccup must not flip the chat to "not ready"."""

    def __init__(self, claude_cmd: list[str], ttl: float = LOGIN_TTL_S, timeout: float = LOGIN_TIMEOUT_S,
                 runner=None, clock=time.monotonic):             # monotonic: a wall-clock step back must not freeze the cache
        self.claude_cmd, self.ttl, self.timeout, self._clock = list(claude_cmd), ttl, timeout, clock
        self._runner = runner                  # (claude_cmd, timeout) -> stdout | None; None means run_auth_status
        self._state = "unknown"
        self._checked_at: float | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def status(self) -> str:
        with self._lock:
            stale = self._checked_at is None or self._clock() - self._checked_at >= self.ttl
            if stale and self._thread is None:
                thread = threading.Thread(target=self._refresh, daemon=True)
                try:
                    thread.start()
                except RuntimeError:           # no thread to spare: keep what we know, try again next time
                    return self._state
                self._thread = thread
            return self._state

    def _refresh(self) -> None:
        try:
            runner = self._runner or run_auth_status           # looked up now, so a test can swap the module's function
            state = parse_login(runner(self.claude_cmd, self.timeout))
        except Exception:  # noqa: BLE001 — whatever went wrong, it's a hiccup, not an answer
            state = None
        with self._lock:
            if state is not None:
                self._state = state
            self._checked_at = self._clock()
            self._thread = None


class Workshop:
    def __init__(self, claude_cmd: list[str], repo: str = REPO, primary: str = PRIMARY,
                 sessions: str = SESSIONS, remote: str = REMOTE, prompt_file: Path = PROMPT_FILE,
                 state_dir: str | None = None, max_run_s: float | None = None, idle_s: float | None = None,
                 conversations: str | None = None, projects: dict | None = None):
        # The projects (projects.py). Without a registry (the tests, and anything built the old way) there is one:
        # SkippyTel, made from repo/primary/remote as they are at each run, so a test that changes shop.primary is heard.
        # With one, SkippyTel's entry decides those three.
        self._projects = dict(projects) if projects else None
        if self._projects:
            own = self._projects[projects_mod.DEFAULT]
            repo, primary, remote = own.clone, own.primary, own.url
        self.claude_cmd, self.repo, self.primary = list(claude_cmd), repo, primary
        self.sessions_path, self.remote, self.prompt_file = sessions, remote, prompt_file
        self.max_run_s = MAX_RUN_S if max_run_s is None else float(max_run_s)      # the watchdog's two limits; tests shorten them
        self.idle_s = IDLE_S if idle_s is None else float(idle_s)
        # Runs are saved next to the sessions file, so anything that points sessions somewhere (a test's tmp dir) moves
        # the runs with it; nothing but the real bridge ever writes to the real directory.
        self.state_dir = str(state_dir or os.path.join(os.path.dirname(sessions), STATE_DIR_NAME))
        self.login_probe = LoginProbe(self.claude_cmd)
        # Every conversation in full, in files the Captain owns (conversation_log.py). The real folder only for the real
        # bridge: a Workshop pointed at other sessions (a test's tmp dir) keeps its conversations next to them.
        if conversations is None:
            conversations = (conversation_log.ROOT if os.path.abspath(sessions) == os.path.abspath(SESSIONS)
                             else os.path.join(os.path.dirname(sessions), "conversations"))
        self.convlog = conversation_log.ConversationLog(conversations, repo=self.repo)
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self._save_error: str | None = None

    def login(self) -> str:
        """"ok" | "expired" | "unknown": Claude Code's sign-in on this PC. Never blocks (see LoginProbe)."""
        return self.login_probe.status()

    # ── projects ─────────────────────────────────────────────────────────
    def project(self, pid: str | None = None) -> projects_mod.Project:
        """A project by id (KeyError if this bridge has no such project). The default one is SkippyTel."""
        pid = pid or projects_mod.DEFAULT
        if self._projects is None:
            if pid != projects_mod.DEFAULT:
                raise KeyError(pid)
            return projects_mod.Project(projects_mod.DEFAULT, "SkippyTel", "aaronsrhodes2/skippy-tel", "main",
                                        self.primary, self.repo, "skippytel", self.remote)
        return self._projects[pid]

    def projects(self) -> list[dict]:
        """Every project, as the portal may see them (no paths): SkippyTel first, then by name."""
        ids = [projects_mod.DEFAULT] if self._projects is None else list(self._projects)
        found = [self.project(i) for i in ids]
        return [p.public() for p in sorted(found, key=lambda p: (p.id != projects_mod.DEFAULT, p.name.lower()))]

    @staticmethod
    def session_key(project: str, conv: str) -> str:
        """The sessions file's key: the bare conversation id for SkippyTel (so every conversation from before projects
        carries on), project-qualified for the rest. A Claude session belongs to the directory it ran in, so the same
        conversation id under another project must never resume it."""
        return conv if project == projects_mod.DEFAULT else f"{project}:{conv}"

    # ── runs ─────────────────────────────────────────────────────────────
    def start(self, task: str, conv: str, message: str, who: str = "", attach: bool = False,
              project: str = "") -> Run:
        """Begin a task, or re-attach if it's already known. One task at a time across ALL projects: the chat follows one
        run, the Captain reviews one outcome at a time, and two runs would race on the shared sessions file.

        `attach` says "I only want to follow this task": an unknown one raises UnknownTask and nothing starts, so a
        relay that is re-attaching after a restart can never begin a Claude run (full permissions) by mistake, and
        needs no message. `who` is the owner's email; the bridge keeps it so the reporter can tell the portal whose
        chat a run belongs to after the portal forgot (a deploy wipes it)."""
        if not _TASK.fullmatch(task or "") or not _CONV.fullmatch(conv or ""):
            raise ValueError("bad task or conversation id")
        who = (who or "").strip()
        if len(who) > MAX_WHO:
            raise ValueError("who is too long")        # refused, not cut: a cut email would name somebody else
        message = (message or "").strip()[:MAX_MESSAGE]
        if not message and not attach:
            raise ValueError("empty message")
        project = project or projects_mod.DEFAULT
        try:
            chosen = self.project(project)
        except KeyError:
            raise ValueError("unknown project") from None    # only projects.json names a repo: never a request field
        adopted = None
        with self._lock:
            self._prune()
            known = self._runs.get(task)
            if known is not None:
                if who and not known.who:              # started before the reporter sent who: take it now
                    known.who, adopted = who, known
            elif attach:
                raise UnknownTask(task)
            elif any(not r.done for r in self._runs.values()):
                raise Busy("Skippy is still working on the last thing")
            else:
                run = self._runs[task] = Run(task, conv, message, who, project=project)
                run.clone = chosen.clone
                run.on_change = self._changed
        if known is not None:
            if adopted is not None:
                self._save(adopted, True)
            return known
        self._save(run, True)                          # on disk before any work: a restart then finds it and can say so
        self.convlog.started(run)                      # the Captain's message is on record before anything runs
        threading.Thread(target=self._work, args=(run,), daemon=True).start()
        return run

    def _changed(self, run: Run, force: bool) -> None:
        """A run's events changed: save it for a bridge restart, and add the new events to the conversation's file."""
        self._save(run, force)
        self.convlog.progress(run)

    def adopt(self, run: Run) -> None:
        """Hold a ready-made run (the loader's, and tests'): known to start(), listed, saved from here on."""
        run.on_change = self._save
        with self._lock:
            self._runs[run.task] = run
        self._save(run, True)

    def get(self, task: str) -> Run | None:
        return self._runs.get(task)

    def listing(self) -> list[dict]:
        """The newest runs (at most MAX_LISTED), for GET /workshop/runs: what the reporter reconciles the portal with."""
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda r: r.created, reverse=True)[:MAX_LISTED]
        now = time.time()
        return [r.summary(now) for r in runs]

    def _live(self, task: str) -> dict | None:
        """What this bridge holds of a run, for the conversation listing (conversation_log.py): None if it holds none."""
        run = self._runs.get(task)
        if run is None:
            return None
        got = run.since(0)
        return {"state": "running" if not got["done"] else ("done" if got["ok"] else "error"),
                "events": got["events"], "error": got["error"]}

    def conversations(self) -> list[dict]:
        """The newest conversations, for GET /workshop/conversations (conversation_log.py keeps them)."""
        return self.convlog.conversations(live=self._live)

    def conversation(self, conv: str) -> dict | None:
        """One conversation's turns for GET /workshop/conversation/<conv>; None if there is no such conversation."""
        if not _CONV.fullmatch(conv or ""):
            return None
        return self.convlog.conversation(conv, live=self._live)

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
            _unlink(self._file(task))

    # ── runs on disk: a bridge restart (a crash, a reboot, the supervisor) must not erase the chat's answer ──
    def _file(self, task: str) -> Path:
        return Path(self.state_dir) / f"{task}.json"

    def _save(self, run: Run, force: bool = False) -> None:
        """Write the run's file: always when `force` (created, finished), else at most every SAVE_EVERY_S. This is
        called from the run's own thread, in line with reading Claude's output, so it must neither raise nor stall:
        a disk that fails is logged once and the run carries on exactly as before (the file is a convenience; the
        run in memory is the truth). Only the forced writes retry, since those are the ones a restart can't live without.

        The retries matter on Windows: os.replace fails with "Access is denied" while anything has the file open
        without delete sharing (antivirus, the search indexer, a sync client; measured here: a reader polling every
        20 ms made 1 replace in 4 fail, and on a loaded PC a few retries over 150 ms all failed, which left the
        FINAL write of a run unsaved and the file saying "still running"). So the end of a run, which nothing
        rewrites afterwards, waits it out for a few seconds; it is the work thread after the run already reads as
        done, so no one waits on it. The creation write, made on the request thread, gives up sooner."""
        try:
            with run.save_lock:
                now = time.monotonic()
                if not force and now - run.saved_at < SAVE_EVERY_S:
                    return
                run.saved_at = now                     # also on failure: a broken disk is retried every 5 s, not every event
                snapshot = run.snapshot()
                data = json.dumps(snapshot)
                path = self._file(run.task)
                tmp = path.with_name(path.name + ".tmp")
                pauses = (SAVE_RETRY_S if snapshot["done"] else SAVE_RETRY_S[:2]) if force else ()
                for attempt in range(len(pauses) + 1):
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        tmp.write_text(data, encoding="utf-8")
                        os.replace(tmp, path)          # atomic: a reader (or a crash) never sees half a file
                        self._save_error = None
                        return
                    except OSError:
                        if attempt == len(pauses):
                            raise
                        time.sleep(pauses[attempt])
        except Exception as exc:  # noqa: BLE001 — see the docstring: nothing here may take down a run
            message = f"{type(exc).__name__}: {exc}"
            if message != self._save_error:
                self._save_error = message
                print(f"[bridge] couldn't save run {run.task}: {message}", file=sys.stderr, flush=True)

    def load(self) -> int:
        """At bridge start: take back the runs on disk that are younger than KEEP_FINISHED_S (a finished run counts
        from when it finished, a run caught mid-flight from when it was last written), prune the rest. A run that
        was not done had its process die with the bridge: it is closed as failed and says so, so the chat shows an
        ending instead of waiting. Returns how many runs came back. Called by main(), not at import, so merely
        importing this module (a test) never touches the real directory."""
        root = Path(self.state_dir)
        try:
            entries = sorted(root.iterdir())
        except OSError:
            return 0                                   # no directory yet: a first start, or nothing was ever saved
        now, loaded = time.time(), 0
        for path in entries:
            if path.name.endswith(".tmp"):             # a write the last process died in the middle of
                _unlink(path)
                continue
            if path.suffix != ".json":
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            run = self._read(path, mtime)
            reference = (run.finished_at or mtime) if run is not None and run.done else mtime
            if now - reference > KEEP_FINISHED_S:
                _unlink(path)                          # too old to matter, or too broken to read and long past mattering
                continue
            if run is None:
                continue                               # unreadable and recent: leave it alone, never crash the start
            with self._lock:
                if run.task in self._runs:
                    continue
                self._runs[run.task] = run
            run.on_change = self._save
            if not run.done:
                run.finish(False, RESTART_ERROR)       # re-saves it, now closed
            loaded += 1
        return loaded

    @staticmethod
    def _read(path: Path, mtime: float) -> Run | None:
        """A Run from its file, or None if the file isn't a run this bridge wrote."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        task, conv, events = data.get("task"), data.get("conv"), data.get("events")
        if (not isinstance(task, str) or task != path.stem or not _TASK.fullmatch(task)
                or not isinstance(conv, str) or not _CONV.fullmatch(conv) or not isinstance(events, list)):
            return None
        who = data.get("who")
        created = data.get("created")
        project = data.get("project")
        run = Run(task, conv, "", who.strip()[:MAX_WHO] if isinstance(who, str) else "",
                  float(created) if _is_number(created) else mtime,
                  project if isinstance(project, str) and projects_mod._ID.fullmatch(project) else projects_mod.DEFAULT)
        run.events = [e for e in events if isinstance(e, dict)][:MAX_EVENTS]
        run.done = data.get("done") is True
        run.ok = data["ok"] if isinstance(data.get("ok"), bool) else None
        run.error = data["error"] if isinstance(data.get("error"), str) else None
        run.finished_at = float(data["finished_at"]) if _is_number(data.get("finished_at")) else None
        return run

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
    def prompt(self, project: str | None = None) -> str:
        return render_prompt(self.prompt_file.read_text(encoding="utf-8"), self.project(project))

    def prompt_path(self, project: str | None = None) -> Path:
        """Where the rendered prompt lives: next to the sessions file, so anything that points sessions somewhere (a
        test's tmp dir) moves it too and nothing but the real bridge writes the real one. One file per project (each
        names its own clone and rules); SkippyTel keeps the original name."""
        pid = self.project(project).id
        name = PROMPT_NAME if pid == projects_mod.DEFAULT else PROMPT_NAME.replace(".txt", f"-{pid}.txt")
        return Path(self.sessions_path).with_name(name)

    def write_prompt(self, project: str | None = None) -> Path:
        """Render the prompt ({repo}/{primary} filled in) into prompt_path() for `--append-system-prompt-file`, on every
        task, so an edited workshop_prompt.md takes effect on the next message without restarting anything. It is a
        file because a multi-line argument cannot go through claude.cmd (see the module docstring). Raises OSError if
        the file can't be made current: a run must never start without its rules (Platinum 19), and an old copy of
        them is not 'current'. A file that already says the right thing is left alone (nothing to write, so nothing
        for an antivirus or indexer holding it open to break). It is written in place, not through a temp file and a
        rename: one task runs at a time, so claude is never reading it during this, and a copy torn by a crash
        differs from the rendering and is simply written again."""
        path = self.prompt_path(project)
        data = self.prompt(project).encode("utf-8")
        try:
            if path.read_bytes() == data:
                return path
        except OSError:
            pass                                       # not there yet
        for attempt in range(4):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                return path
            except OSError:
                if attempt == 3:
                    raise
                time.sleep(0.05 * (attempt + 1))       # Windows: "Access is denied" while something holds the file for a moment
        return path

    def _git(self, *args: str, cwd: str | None = None, timeout: float = GIT_TIMEOUT) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, env=child_env())

    def _prepare_repo(self, run: Run) -> bool:
        """The project's own clone, on the latest of its branch. False (with the run failed) if there's no clone to work
        in. A first use clones it (`git clone`, into the registry's `clone` path: never the Captain's checkout)."""
        project = self.project(run.project)
        clone = project.clone
        try:
            if not (Path(clone) / ".git").exists():
                if Path(clone).exists() and any(Path(clone).iterdir()):
                    # Something else lives there: refuse rather than clone into (or over) it.
                    run.finish(False, f"{clone} exists and isn't a git clone, so I won't work in it")
                    return False
                run.emit({"t": "tool", "name": "git", "detail": f"cloning {project.repo} for the first time"})
                out = self._git("clone", project.url, clone)
                if out.returncode != 0:
                    run.finish(False, "couldn't clone the repo: " + redact(out.stderr.strip())[:300])
                    return False
                return True
            if self._git("status", "--porcelain", cwd=clone).stdout.strip():
                run.emit({"t": "text", "text": "(my clone has uncommitted changes from an earlier task, so I'm not pulling)"})
                return True
            out = self._git("pull", "--ff-only", cwd=clone)
            if out.returncode != 0:
                run.emit({"t": "text", "text": "(couldn't fast-forward my clone: " + redact(out.stderr.strip())[:200] + ")"})
        except (OSError, subprocess.SubprocessError) as exc:
            run.finish(False, f"git isn't working: {exc}")
            return False
        return True

    def command(self, session: str | None, project: str | None = None) -> list[str]:
        """The claude command line. Every element is a single line (see the module docstring): the prompt is a FILE
        (write_prompt() makes it; _work does that before every run) and the message goes by stdin.

        --system-prompt-snapshot off: by default Claude Code records the system prompt on a conversation's first
        request and sends that record on every resume, whatever a later launch passes (measured here on 2.1.285: a
        resumed session ignored a changed prompt file). Left on, a conversation that began before this fix would keep
        the old one-line prompt, and an edit to workshop_prompt.md would not reach a chat already under way. Off, the
        file is read fresh on every request, so the Captain's rules are always the current ones.

        --add-dir is the project's primary checkout (the Captain's): readable, and for SkippyTel the place a skippy-pc
        service is deployed from. The working directory (the clone) is set by _spawn."""
        cmd = [*self.claude_cmd, "-p", "--output-format", "stream-json", "--verbose",
               "--model", MODEL, "--dangerously-skip-permissions",
               "--append-system-prompt-file", str(self.prompt_path(project)), "--system-prompt-snapshot", "off",
               "--add-dir", self.project(project).primary]
        if session:
            cmd += ["--resume", session]
        return self._one_line_each(cmd)

    @staticmethod
    def _one_line_each(argv: list[str]) -> list[str]:
        """argv, or ValueError naming the culprit: a newline in an argument is cut off, along with every argument after
        it, by cmd.exe (the R01 incident), so it must never reach a launch. Nothing today can put one there (the
        paths come from this PC's configuration, the session id from claude's own output); this is the tripwire."""
        for arg in argv:
            if "\n" in arg or "\r" in arg:
                raise ValueError(f"an argument contains a newline ({arg[:40]!r}), which cmd.exe would cut the command line at")
        return argv

    def _spawn(self, argv: list[str], cwd: str | None = None) -> subprocess.Popen:
        """Start claude (or whatever `argv` is) the one way this module does it: no newline in any argument, the
        clone as the working directory, a pipe each way, the child_env() environment, and on POSIX its own process
        group so the whole tree can be killed. The Windows test runs the real claude.cmd through exactly this."""
        extra = {"start_new_session": True} if sys.platform != "win32" else {}
        return subprocess.Popen(self._one_line_each(argv), cwd=cwd or self.repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                env=child_env(), bufsize=1, **extra)

    def _head(self, clone: str) -> str | None:
        out = self._git("rev-parse", "HEAD", cwd=clone, timeout=SHIP_GIT_TIMEOUT)
        sha = out.stdout.strip() if out.returncode == 0 else ""
        return sha if _SHA.fullmatch(sha) else None

    def _ship_check(self, run: Run, before: str | None) -> None:
        """Did this run push? Its clone's HEAD moved and origin's branch now contains the new HEAD: then the run says so
        with one `ship` event the chat turns into a ship card (the deploy's progress, then proof it is live). A commit
        that was not pushed says nothing: the agent's own words cover that. Everything here is read-only git, short
        timeouts, and any failure just means no card."""
        project = self.project(run.project)
        clone = project.clone
        after = self._head(clone)
        if after is None or after == before:
            return
        self._git("fetch", "--quiet", "origin", project.branch, cwd=clone, timeout=SHIP_GIT_TIMEOUT)    # best effort
        if self._git("merge-base", "--is-ancestor", after, f"refs/remotes/origin/{project.branch}",
                     cwd=clone, timeout=SHIP_GIT_TIMEOUT).returncode != 0:
            return
        subject = self._git("log", "-1", "--format=%s", after, cwd=clone, timeout=SHIP_GIT_TIMEOUT).stdout
        subject = " ".join(redact(subject).split())[:200]
        workflow = next((name for name in ("deploy.yml", "deploy.yaml")
                         if (Path(clone) / ".github" / "workflows" / name).is_file()), "")
        run.emit({"t": "ship", "project": project.id, "repo": project.repo, "branch": project.branch, "sha": after,
                  "subject": subject, "workflow": workflow})

    def _work(self, run: Run) -> None:
        try:
            if not self._prepare_repo(run):
                return
            try:
                before = self._head(self.project(run.project).clone)
            except (OSError, subprocess.SubprocessError):
                before = None
            run.before_finish = lambda r: self._ship_check(r, before)
            try:
                self.write_prompt(run.project)
            except OSError as exc:                     # no rules, no run: never start the agent without them
                run.finish(False, f"couldn't write my instructions file ({self.prompt_path(run.project)}): {exc}"[:300])
                return
            session = self._sessions().get(self.session_key(run.project, run.conv))
            self._attempt(run, session)
        except Exception as exc:  # noqa: BLE001 — a crash must end the run, or the chat waits forever
            run.finish(False, f"the bridge hit a snag: {exc}"[:300])
        finally:
            # How it ended, and a fresh copy of Claude Code's whole transcript (the session id is known by now).
            self.convlog.finished(run, self._sessions().get(self.session_key(run.project, run.conv)))

    @staticmethod
    def _feed(proc: subprocess.Popen, message: str) -> None:
        """The message goes in by stdin (never the command line: no length limit, no cmd.exe quoting). In a thread of its
        own, because a claude that never reads would block this write, and nothing may block the run's clock."""
        try:
            proc.stdin.write(message)
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    @staticmethod
    def _pump(proc: subprocess.Popen, lines: "queue.Queue[str | None]") -> None:
        """Claude's output, line by line, into `lines`; None at the end. In a thread of its own so that the run's
        thread waits on a queue with a timeout and not on a pipe that may never speak again."""
        try:
            for line in proc.stdout:
                lines.put(line)
        except (OSError, ValueError):                  # the pipe was closed under us
            pass
        finally:
            lines.put(None)

    def _over_limit(self, now: float, started: float, last_line: float) -> str | None:
        """Why this run has to be stopped (in the words the chat shows), or None while it is within its limits."""
        if now - started > self.max_run_s:
            return (f"Claude Code was still going after {_span(self.max_run_s)}, so I stopped it. "
                    "Send the message again, or a smaller one, to carry on.")
        if now - last_line > self.idle_s:
            return (f"Claude Code went quiet for {_span(self.idle_s)} (a command or a sign-in prompt may be stuck), "
                    "so I stopped it. Send the message again to retry.")
        return None

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        """Wait for the process to be gone; one that isn't within REAP_S is killed (tree and all), then waited for again."""
        try:
            proc.wait(timeout=REAP_S)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.wait(timeout=REAP_S)
            except subprocess.TimeoutExpired:
                pass                                   # unkillable: the run still ends, the straggler is the OS's problem

    def _attempt(self, run: Run, session: str | None) -> None:
        if run.cancelled:
            run.finish(False, "stopped")
            return
        try:
            proc = self._spawn(self.command(session, run.project), cwd=self.project(run.project).clone)
        except (OSError, ValueError) as exc:           # FileNotFoundError, a refused argument ...
            run.finish(False, f"can't start Claude Code: {exc}")
            return
        run.proc = proc
        if run.cancelled:                     # Stop arrived while it was starting: cancel() found no process to kill
            _kill_tree(proc)
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._feed, args=(proc, run.message), daemon=True).start()
        threading.Thread(target=self._pump, args=(proc, lines), daemon=True).start()
        pending: str | None = None            # the latest text block, held back one event so an error can replace it
        seen_session, failure, produced = None, None, False
        # The watchdog (R06): this thread waits on the queue with a short timeout, so it can look at the clock whatever
        # the process is doing, and at Stop even if a stray grandchild keeps the output pipe open after the kill.
        tick = max(0.02, min(WATCH_TICK_S, self.idle_s / 4, self.max_run_s / 4))
        started = last_line = time.monotonic()
        timed_out: str | None = None
        while True:
            now = time.monotonic()
            if run.cancelled:
                _kill_tree(proc)
                break
            timed_out = self._over_limit(now, started, last_line)
            if timed_out:
                _kill_tree(proc)
                break
            try:
                line = lines.get(timeout=tick)
            except queue.Empty:
                continue
            if line is None:
                break
            last_line = time.monotonic()
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
                    # The words are in `result`, or (claude 2.1.285 for a session it cannot find) only in `errors`.
                    # --resume reaches claude now (R01), so that second shape is a real, reachable one.
                    failure = str(evt.get("result") or "")
                    if not failure and isinstance(evt.get("errors"), list):
                        failure = "; ".join(str(e) for e in evt["errors"] if e)
        self._reap(proc)
        if run.cancelled:
            if pending is not None:
                run.emit({"t": "text", "text": pending})
            run.finish(False, "stopped")
            return
        if timed_out:                         # the watchdog ended it: nothing it was doing is trusted, so no session is kept
            if pending is not None:
                run.emit({"t": "text", "text": pending})
            run.finish(False, timed_out)
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
            self._remember(self.session_key(run.project, run.conv), seen_session)
        if proc.returncode not in (0, None):
            run.finish(False, f"Claude Code exited with code {proc.returncode}")
        else:
            run.finish(True)
