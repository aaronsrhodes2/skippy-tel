"""The private Skippy's engine: Claude Code (Sonnet 5.5, medium effort, full permissions) working on
the SkippyTel repo for the Captain, driven from his portal dashboard.

The portal can't reach this PC, so the home reporter carries each chat message
here (POST /workshop), then reads the run's events back (GET /workshop/run/<task>)
and hands them to the portal. A run lives here, keyed by the portal's task id:
asking again with the same id re-attaches instead of starting a second run, so
a reporter or portal restart (a deploy!) never loses or repeats work. Each run is also
saved to disk (one file per task, next to the sessions file), so a restart of the bridge
itself keeps the finished ones deliverable and ends the interrupted ones with a plain error.

The model and its effort level are hard-coded (MODEL, EFFORT): no environment variable and no request field
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

import base64
import json
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import sys
import threading
import time
from pathlib import Path

import chat_names
import eyes
import conversation_log
import new_folder
import projects as projects_mod
import sdk_engine

MODEL = "claude-sonnet-5-5"        # Sonnet 5.5, on purpose: nothing else picks it (was Opus until 2026-10-10)
EFFORT = "medium"                  # and its effort level: also fixed here, never from the environment or a request
# The other brain (2026-10-20): Claude Code itself, pointed at Skippy Core (skippy-core's llama-swap, "coder" slot) through its
# Claude-compatible API. A run picks "claude" or "core"; nothing else. Core runs get their OWN Claude Code config folder:
# with this PC's subscription login in the shared one, Claude Code sends that login and never the key (measured: a
# 108-character Bearer token, 401 from llama-swap). Claude sessions live in a config folder, so a conversation keeps one brain.
BRAINS = ("claude", "core")
CORE_MODEL = "coder"               # core/models.json's coding slot
# Claude Code's background calls (titles, quick checks) go to the always-loaded chat slot: on the coder (--parallel 1) they took
# its only slot and threw away its cached prompt, so the next turn re-read all ~30K tokens (the community's Strix Halo notes).
CORE_FAST_MODEL = "skippy-chat"
# That slot's --ctx-size (Qwen3.6-35B-A3B since 2026-10-23: 131072), so Claude Code compacts in time (it doesn't know "coder").
# Told too little, it thrashes: its own fixed prompt is ~30k tokens, and at 65536 (minus its reply reserve) it compacted after every
# turn until it gave up ("Autocompact is thrashing", the Captain's iPad, 2026-10-23). WORKSHOP_CORE_CONTEXT overrides it.
CORE_CONTEXT = os.getenv("WORKSHOP_CORE_CONTEXT", "131072")
CORE_MAX_OUTPUT = "16000"          # its reply reserve on Core: a local model's replies are short, and the reserve comes out of the window
VAULT_ENV = os.getenv("SKIPPY_VAULT_ENV", "G:/My Drive/vault/.env")
REPO = os.getenv("WORKSHOP_REPO", r"D:\Aaron\development\SkippyTel-workshop")
PRIMARY = os.getenv("WORKSHOP_PRIMARY", r"D:\Aaron\development\SkippyTel")
REMOTE = os.getenv("WORKSHOP_REMOTE", "D:/trunk/git/skippy-tel.git")      # SkippyTel's home (docs/no-github.md): never GitHub
SESSIONS = os.getenv("WORKSHOP_SESSIONS", os.path.join(os.path.dirname(REPO), ".skippy-workshop-sessions.json"))
PROMPT_FILE = Path(__file__).with_name("workshop_prompt.md")
PROMPT_NAME = ".skippy-workshop-prompt.txt"   # the prompt as claude reads it ({repo}/{primary} filled in), next to the sessions file
ENV_FILES = ("G:/My Drive/vault/.env",)        # where SKIPPY_HOME_TOKEN lives if the environment doesn't have it

MAX_MESSAGE = 120_000          # ~30K tokens: a pasted log or file fits; the same in the portal, the bridge and the log
# Pictures the Captain pastes into the chat (UI bug screenshots, mostly): saved here, one folder per task, and the message tells
# Claude Code the paths so it opens them with its Read tool. Checked again here: the bridge trusts nothing it is handed.
ATTACH_DIR = os.getenv("WORKSHOP_ATTACH", os.path.join(os.path.dirname(os.getenv("WORKSHOP_REPO", r"D:\Aaron\development\SkippyTel-workshop")), ".skippy-workshop-attachments"))
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 3 * 1024 * 1024
ATTACH_KEEP_S = 14 * 24 * 3600


def _image_ext(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def save_images(task: str, images, root: str | None = None) -> list[str]:
    """Write the pictures of one message to <root>/<task>/image-N.ext and return the paths. ValueError for anything that is not a
    picture or is too big. Old folders (ATTACH_KEEP_S) are swept as a courtesy: this is a scratch space, not an archive."""
    root = root or ATTACH_DIR
    if not images:
        return []
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise ValueError("too many pictures")
    if not _TASK.fullmatch(task or ""):
        raise ValueError("bad task id")
    decoded = []
    for item in images:
        b64 = item.get("b64") if isinstance(item, dict) else None
        if not isinstance(b64, str):
            raise ValueError("bad picture")
        try:
            data = base64.b64decode(b64, validate=True)
        except ValueError:
            raise ValueError("bad picture") from None
        ext = _image_ext(data)
        if not ext or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("bad picture")
        decoded.append((ext, data))
    folder = os.path.join(root, task)
    os.makedirs(folder, exist_ok=True)
    paths = []
    for n, (ext, data) in enumerate(decoded, 1):
        path = os.path.join(folder, f"image-{n}{ext}")
        with open(path, "wb") as f:
            f.write(data)
        paths.append(path)
    cutoff = time.time() - ATTACH_KEEP_S
    try:
        for name in os.listdir(root):
            old = os.path.join(root, name)
            if os.path.isdir(old) and os.path.getmtime(old) < cutoff:
                for leaf in os.listdir(old):
                    os.unlink(os.path.join(old, leaf))
                os.rmdir(old)
    except OSError:
        pass
    return paths
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



def _is_junction(path: str) -> bool:
    """A Windows directory junction (os.path.islink says no to those before Python 3.12's isjunction)."""
    check = getattr(os.path, "isjunction", None)
    return bool(check and check(path))

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


MAX_TODO_DETAIL = 1200                       # a checklist is long where every other tool line is short (portal/workshop.py and conversation_log.py agree)


def tool_detail(name: str, args: dict) -> str:
    """What a tool call is about, for the chat's activity line: the command or the path, never file contents."""
    args = args if isinstance(args, dict) else {}
    if name == "TodoWrite":                        # his task list, shown in the chat as a live checklist: "<mark> <item>" per line
        marks = {"completed": "\u2713", "in_progress": "\u25b6"}
        rows = [marks.get(t.get("status"), "\u25cb") + " " + redact(str(t["content"]))[:80]
                for t in (args.get("todos") or [])[:20] if isinstance(t, dict) and t.get("content")]
        return "\n".join(rows)[:MAX_TODO_DETAIL]
    if name == "Bash":
        lines = str(args.get("command") or "").strip().splitlines()
        return redact(lines[0])[:160] if lines else ""
    for key in ("file_path", "path", "pattern", "url", "description"):
        if args.get(key):
            return redact(str(args[key]))[:160]
    return ""


MAX_SHOTS = 3                      # pictures one run may attach (SKIPPY_IMAGE lines)
# A picture the agent shows: up to 12 MB (a 4K screenshot, a full-size picture). It was 400 KB, which the event pipe needed, not the
# Captain; and it was named MAX_IMAGE_BYTES too, which silently capped the pictures HE attaches (above, 3 MB) at 400 KB as well.
MAX_SHOT_BYTES = 12 * 1024 * 1024
# `SKIPPY_MODEL: <file.glb>` is the same, for a 3D model (a voxel build from tools/voxel/render.py --glb): the chat shows it inline
# as a model to rotate (2026-10-25: the Captain wants 3D in the chat itself, not a file he can't reach from a browser).
# SKIPPY_AUDIO / SKIPPY_VIDEO / SKIPPY_FILE the same for sound, video and a file to save (MIDI, a zip of stems): one rule for every
# result, nothing ever left as a path on skippy-pc.
_IMAGE_LINE = re.compile(r"^[ \t]*`?SKIPPY_(?:IMAGE|MODEL|AUDIO|VIDEO|FILE):[ \t]*(\S[^\n`]*?)[ \t]*`?[ \t]*$", re.M)


# A picture the agent wants MADE (not a file it has): `SKIPPY_PICTURE: <a detailed description>` in its reply. The bridge gives it
# an id and the portal makes it a patient job (portal/patient.py) for the Captain, drawn when its machine is quiet; the chat shows a
# card that fills in. The agent never runs the image studio by hand (2026-10-24: it did, and the run died trying to show the file).
# `SKIPPY_PICTURE_HQ:` asks for the quality tier instead (skippy-core's big model, up to an hour), only when he asks for quality.
MAX_PICTURES = 2
_PICTURE_LINE = re.compile(r"^[ \t]*`?SKIPPY_PICTURE(_HQ)?:[ \t]*(\S[^\n`]*?)[ \t]*`?[ \t]*$", re.M)


def split_pictures(text: str) -> tuple[str, list[tuple[str, str]]]:
    """An assistant text block without its `SKIPPY_PICTURE[_HQ]: <description>` lines, and those as (description, tier):
    tier "hq" for a quality picture, "" for a quick one."""
    prompts = [(m.group(2).strip()[:900], "hq" if m.group(1) else "") for m in _PICTURE_LINE.finditer(text)]
    return _PICTURE_LINE.sub("", text).strip(), [p for p in prompts if p[0]]


# A pop-up job the agent wants run (docs/popups.md): `SKIPPY_POPUP: <name> <json args>`, any model in the registry. Like a picture: an id
# from the bridge, a patient job on the portal, a card in his chat. `"_file": "<path>"` in the arguments hands it a picture or a sound
# from this run's clone or the Captain's attachments (the only places a file may leave this PC from), at most MAX_POPUP_INPUT.
MAX_POPUPS = 3
MAX_POPUP_ARGS = 4000              # characters of JSON arguments (the portal's workshop.MAX_POPUP_ARGS)
MAX_POPUP_INPUT = 1_000_000        # bytes of file (as base64 it fits a chat attachment: attachments.MAX_B64_EACH)
_POPUP_LINE = re.compile(r"^[ \t]*`?SKIPPY_POPUP:[ \t]*([a-z][a-z0-9_]{2,39})(?:[ \t]+([^\n`]*?))?[ \t]*`?[ \t]*$", re.M)
_SOUND_MAGIC = ((b"RIFF", "audio/wav"), (b"fLaC", "audio/flac"), (b"OggS", "audio/ogg"), (b"ID3", "audio/mpeg"))


def split_popups(text: str) -> tuple[str, list[tuple[str, dict | None]]]:
    """An assistant text block without its `SKIPPY_POPUP: <name> <json>` lines, and those as (name, args): args None when the JSON
    was not an object of at most MAX_POPUP_ARGS characters (the run then says so)."""
    found = []
    for m in _POPUP_LINE.finditer(text):
        try:
            args = json.loads(m.group(2) or "{}")
        except ValueError:
            args = None
        found.append((m.group(1), args if isinstance(args, dict) and len(json.dumps(args)) <= MAX_POPUP_ARGS else None))
    return _POPUP_LINE.sub("", text).strip(), found


def file_mime(data: bytes) -> str | None:
    """A picture's or a sound's type by its first bytes, or None."""
    if image_mime(data):
        return image_mime(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return next((mime for magic, mime in _SOUND_MAGIC if data.startswith(magic) and (magic != b"RIFF" or data[8:12] == b"WAVE")), None)


def split_images(text: str) -> tuple[str, list[str]]:
    """An assistant text block without its `SKIPPY_IMAGE: <path>` lines, and those paths (see _attach_images)."""
    paths = [m.group(1).strip().strip("\"'") for m in _IMAGE_LINE.finditer(text)]
    return _IMAGE_LINE.sub("", text).strip(), paths


def image_mime(data: bytes) -> str | None:
    """What the agent may show in the chat, by the file's own first bytes (never by its name), else None: a picture, a 3D model
    (binary glTF 2), sound (mp3, wav, ogg, flac), video (mp4), or a file to save (MIDI, zip). The same rules as the portal's
    workshop.sniff (a test keeps them equal): the browser is only a window, everything is made here and shown there."""
    if data[:4] == b"glTF" and data[4:8] == b"\x02\x00\x00\x00":
        return "model/gltf-binary"                     # a binary glTF, version 2
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:4] == b"fLaC":
        return "audio/flac"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    if data[:4] == b"MThd":
        return "audio/midi"
    if data[:4] == b"PK\x03\x04":
        return "application/zip"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return None


_BLOCK = re.compile(r"\{\{#(skippytel|other)\}\}\n?(.*?)\{\{/\1\}\}\n?", re.S)


# A folder opened in place (projects.folders): the Captain chose to work right in his own folder, so the clone rules below
# don't apply to this conversation. This goes FIRST so it wins over them.
INPLACE_NOTE = (
    "## This conversation works IN PLACE (the Captain's choice)\n"
    "The Captain picked his own folder, {repo}, and wants you to work right in it: there is no separate clone this time. "
    "Wherever the rules below say \"your clone\" or \"never edit the Captain's own checkout\", for this conversation both mean "
    "this folder, and editing it is what he asked for. Because it is HIS working copy: run `git status` before you change "
    "anything and mention any uncommitted work you find; never discard or overwrite his work in progress (no "
    "`git reset --hard`, `git checkout -- <file>`, `git clean`, or stashing it without asking); commit only what you changed. "
    "If the folder isn't a git repo, say so before changing files, since there is then no undo.\n\n")


def render_prompt(template: str, project) -> str:
    """workshop_prompt.md for one project: the {{#skippytel}}...{{/skippytel}} block only for a project with SkippyTel's
    deploy rules, the {{#other}}...{{/other}} block only for the rest (whose own CLAUDE.md says how to test and ship),
    and the placeholders filled in: {repo} (the agent's clone), {primary} (the Captain's checkout), {project} (its name),
    {github} (owner/name) and {branch}. A template without blocks is used whole."""
    kind = "skippytel" if project.deploy == "skippytel" else "other"
    text = _BLOCK.sub(lambda m: m.group(2) if m.group(1) == kind else "", template)
    if getattr(project, "inplace", False):
        text = INPLACE_NOTE + text
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
                 project: str = projects_mod.DEFAULT, brain: str = "core", client_id: str = ""):
        self.task, self.conv, self.message, self.who = task, conv, message, who
        self.client_id = client_id                 # an opaque id the page chose for this send; echoed for tracing, never used to dedupe here
        self.brain = brain if brain in BRAINS else "claude"   # who does the thinking: Skippy Core's coder (purple) unless another is named
        self.project = project                 # which repo (projects.json): picks the clone, the checkout and the session
        self.clone = ""                        # that project's clone when the run started (the conversation log reads it)
        self.before_finish = None              # (run) -> None, called once just before the run ends (the ship check)
        self.image_paths: list[str] = []       # SKIPPY_IMAGE lines the agent printed: screenshots to attach at the end
        self.picture_prompts: list[tuple[str, str]] = []   # SKIPPY_PICTURE[_HQ] lines: (description, tier) to have made (patient jobs)
        self.popups: list[tuple[str, dict | None]] = []    # SKIPPY_POPUP lines: (name, args) to have run (patient jobs, docs/popups.md)
        self.pictures: list[str] = []          # a Core-brain run's attached pictures, described by the vision slot before it starts
        self.created = time.time() if created is None else created
        self.events: list[dict] = []
        self.done, self.ok, self.error = False, None, None
        self.cancelled = False
        self.proc: subprocess.Popen | None = None
        self.abort = None                      # () -> None, set by an engine that has no process to kill (sdk_engine): cancels its task
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
            # finished_age_s: how long ago it ended, so a portal hearing of an old run (after a deploy) can tell it from
            # one that just finished (it notifies the Captain only for the second kind: portal/notify.py)
            return {"events": self.events[after:], "next": len(self.events), "done": self.done,
                    "ok": self.ok, "error": self.error,
                    "finished_age_s": None if self.finished_at is None else round(max(0.0, time.time() - self.finished_at), 1)}

    def snapshot(self) -> dict:
        """Everything worth keeping across a bridge restart. The message text is not in it: nothing needs it again."""
        with self._cond:
            return {"task": self.task, "conv": self.conv, "who": self.who, "project": self.project, "brain": self.brain,
                    "client_id": self.client_id, "created": self.created,
                    "finished_at": self.finished_at, "done": self.done, "ok": self.ok, "error": self.error,
                    "events": list(self.events)}

    def summary(self, now: float) -> dict:
        """What GET /workshop/runs says about a run: counts and state, never the message or any event's content."""
        with self._cond:
            finished = self.finished_at
            return {"task": self.task, "conv": self.conv, "who": self.who, "project": self.project, "brain": self.brain,
                    "client_id": self.client_id, "events": len(self.events),
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
                 conversations: str | None = None, projects: dict | None = None, dev_root: str | None = None):
        # The projects (projects.py). Without a registry (the tests, and anything built the old way) there is one:
        # SkippyTel, made from repo/primary/remote as they are at each run, so a test that changes shop.primary is heard.
        # With one, SkippyTel's entry decides those three.
        self._projects = dict(projects) if projects else None
        if self._projects:
            own = self._projects[projects_mod.DEFAULT]
            repo, primary, remote = own.clone, own.primary, own.url
        self.claude_cmd, self.repo, self.primary = list(claude_cmd), repo, primary
        # In-place folders (projects.folders): the direct children of the development root. Only when one is given (the
        # real bridge passes SkippyTel's parent): a Workshop built without it (the tests, anything else) offers no folders.
        self.dev_root = dev_root
        self.sessions_path, self.remote, self.prompt_file = sessions, remote, prompt_file
        self.max_run_s = MAX_RUN_S if max_run_s is None else float(max_run_s)      # the watchdog's two limits; tests shorten them
        self.idle_s = IDLE_S if idle_s is None else float(idle_s)
        # Runs are saved next to the sessions file, so anything that points sessions somewhere (a test's tmp dir) moves
        # the runs with it; nothing but the real bridge ever writes to the real directory.
        self.state_dir = str(state_dir or os.path.join(os.path.dirname(sessions), STATE_DIR_NAME))
        self.attach_dir = os.path.join(os.path.dirname(sessions), ".skippy-workshop-attachments")   # pasted pictures (save_images)
        self.login_probe = LoginProbe(self.claude_cmd)
        # Every conversation in full, in files the Captain owns (conversation_log.py). The real folder only for the real
        # bridge: a Workshop pointed at other sessions (a test's tmp dir) keeps its conversations next to them.
        if conversations is None:
            conversations = (conversation_log.ROOT if os.path.abspath(sessions) == os.path.abspath(SESSIONS)
                             else os.path.join(os.path.dirname(sessions), "conversations"))
        self.convlog = conversation_log.ConversationLog(conversations, repo=self.repo, namer=self.name_chat)
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self._save_error: str | None = None
        self._maintenance: str | None = None      # set by host_ops while it resets a clone or restarts the bridge

    def hold(self, why: str) -> bool:
        """Refuse new runs (start() raises Busy with `why`) until release(). False, and nothing held, when a run is going:
        the check and the hold are one step under the lock, so a run can't slip in between them."""
        with self._lock:
            if self._maintenance or any(not r.done for r in self._runs.values()):
                return False
            self._maintenance = why
            return True

    def release(self) -> None:
        with self._lock:
            self._maintenance = None

    def login(self) -> str:
        """"ok" | "expired" | "unknown": Claude Code's sign-in on this PC. Never blocks (see LoginProbe)."""
        return self.login_probe.status()

    # ── projects ─────────────────────────────────────────────────────────
    def project(self, pid: str | None = None) -> projects_mod.Project:
        """A project by id (KeyError if this bridge has no such project). The default one is SkippyTel."""
        pid = pid or projects_mod.DEFAULT
        if pid.startswith(projects_mod.FOLDER_PREFIX):
            return self._folder(pid)
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
        listed = [p.public() for p in sorted(found, key=lambda p: (p.id != projects_mod.DEFAULT, p.name.lower()))]
        room = projects_mod.MAX_PROJECTS - len(listed)
        found_folders = projects_mod.folders(self.dev_root).values() if self.dev_root else []
        folders = [{**f.public(), "name": f.name + " \u00b7 folder"} for f in found_folders]
        return listed + folders[:max(0, room)]

    def new_folder(self, name: str) -> dict:
        """New folder (new_folder.py): D:/Aaron/development/<name>, its git repo and the private GitHub repo. Raises
        ValueError for a bad or taken name; answers the folder's id for the picker and how the GitHub side went."""
        if not self.dev_root:
            raise ValueError("This bridge has no development root to make folders in.")
        made = new_folder.create(self.dev_root, name, env=child_env())
        target = projects_mod._norm(made["path"])
        fid = next((f.id for f in projects_mod.folders(self.dev_root).values() if projects_mod._norm(f.primary) == target), "")
        return {"id": fid, "name": made["name"], "repo": made["repo"], "remote": made["remote"], "why": made["why"]}

    def _settle_remote(self, project: projects_mod.Project) -> None:
        """A folder whose GitHub repository is still owed (new_folder.MARKER): try again, on a thread, as a conversation starts."""
        if project.inplace and new_folder.pending(project.clone):
            threading.Thread(target=lambda: new_folder.retry(project.clone, env=child_env()), daemon=True).start()

    def _folder(self, pid: str) -> projects_mod.Project:
        """A folder opened in place (KeyError if there's none by that id now). A folder that is a registered project's own
        checkout borrows that project's repo, branch and deploy rules, so in-place SkippyTel still knows how to ship."""
        if not self.dev_root:
            raise KeyError(pid)
        folder = projects_mod.folders(self.dev_root)[pid]
        for known in (self._projects or {}).values():
            if projects_mod._norm(known.primary) == projects_mod._norm(folder.primary):
                return projects_mod.Project(folder.id, folder.name, known.repo, known.branch, folder.primary, folder.primary,
                                            known.deploy, known.remote, True)
        return folder

    @staticmethod
    def session_key(project: str, conv: str, brain: str = "claude") -> str:
        """The sessions file's key: the bare conversation id for SkippyTel (so every conversation from before projects
        carries on), project-qualified for the rest. A Claude session belongs to the directory it ran in, so the same
        conversation id under another project must never resume it."""
        key = conv if project == projects_mod.DEFAULT else f"{project}:{conv}"
        return key if brain == "claude" else f"{key}@{brain}"       # another brain's sessions live in another config folder

    # ── runs ─────────────────────────────────────────────────────────────
    def start(self, task: str, conv: str, message: str, who: str = "", attach: bool = False,
              project: str = "", images=None, brain: str = "core", client_id: str = "") -> Run:
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
        paths = save_images(task, images, self.attach_dir) if images and not attach else []
        if not message and not attach and not paths:
            raise ValueError("empty message")
        if paths and brain != "core":                  # Claude Code reads them with its Read tool, like any file
            message = (message or "(no words, just the picture)") + "\n\n[The Captain attached " + str(len(paths)) + \
                      (" picture" if len(paths) == 1 else " pictures") + ". Open each with the Read tool before answering: " + "; ".join(paths) + "]"
        elif paths:                                    # Skippy Core's coder is text-only: its vision slot looks first (_describe_pictures)
            message = message or "(no words, just the picture)"
        project = project or projects_mod.DEFAULT
        try:
            chosen = self.project(project)
        except KeyError:
            raise ValueError("unknown project") from None    # only projects.json, or a folder under the root, is a target
        if not attach:
            self._settle_remote(chosen)
        brain = brain or "core"                        # no brain named = purple (the Captain, 2026-10-27); old run files and session keys stay Claude's
        if brain not in BRAINS:
            raise ValueError("unknown brain")
        if brain == "core" and not attach and not self.core_ready():
            raise ValueError("Skippy Core isn't reachable from skippy-pc yet (no SKIPPY_CORE_URL and key)")
        adopted = None
        with self._lock:
            self._prune()
            known = self._runs.get(task)
            if known is not None:
                if who and not known.who:              # started before the reporter sent who: take it now
                    known.who, adopted = who, known
            elif attach:
                raise UnknownTask(task)
            elif self._maintenance:
                raise Busy(self._maintenance)
            elif any(not r.done for r in self._runs.values()):
                raise Busy("Skippy is still working on the last thing")
            else:
                run = self._runs[task] = Run(task, conv, message, who, project=project, brain=brain, client_id=(client_id or "")[:64])
                run.clone = chosen.clone
                run.pictures = paths if brain == "core" else []
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
        if run.abort:
            run.abort()
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
                  project if isinstance(project, str) and projects_mod._ID.fullmatch(project) else projects_mod.DEFAULT,
                  data.get("brain") if data.get("brain") in BRAINS else "claude",     # a file with no brain is from before brains: Claude's
                  data["client_id"][:64] if isinstance(data.get("client_id"), str) else "")
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

    # ── Skippy Core as the brain ─────────────────────────────────────────
    def _core_settings(self) -> tuple[str, str]:
        """skippy-core's URL and key: from the environment, else the vault .env (read here, never logged or returned)."""
        url = os.getenv("WORKSHOP_CORE_URL") or os.getenv("SKIPPY_CORE_URL", "")
        key = os.getenv("SKIPPY_CORE_API_KEY", "")
        if not (url and key):
            try:
                for line in Path(VAULT_ENV).read_text(encoding="utf-8").splitlines():
                    name, _, value = line.partition("=")
                    value = value.strip().strip(chr(34) + chr(39))
                    if name.strip() == "SKIPPY_CORE_URL" and not url:
                        url = value
                    elif name.strip() == "SKIPPY_CORE_API_KEY" and not key:
                        key = value
            except OSError:
                pass
        return url.rstrip("/"), key

    CORE_PROBE_TTL_S = 60

    def ask_core(self, system: str, text: str) -> str:
        """One quick question to Skippy Core's chat slot (no tools): a conversation's name. Raises when Core isn't there."""
        import urllib.request
        url, key = self._core_settings()
        if not (url and key):
            raise RuntimeError("Skippy Core isn't configured")
        body = json.dumps({"model": "chat", "max_tokens": 400, "system": system,
                           "messages": [{"role": "user", "content": text}]}).encode()
        req = urllib.request.Request(url + "/v1/messages", data=body, method="POST",
                                     headers={"x-api-key": key, "Authorization": "Bearer " + key,
                                              "anthropic-version": "2023-06-01", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            got = json.loads(r.read().decode("utf-8", "replace"))
        return " ".join(b.get("text", "") for b in got.get("content", []) if isinstance(b, dict) and b.get("type") == "text")

    def describe_picture(self, path: str, post=None) -> str:
        """What Skippy Core's vision slot (Qwen3-VL, with its mmproj) sees in one picture, text copied out word for word (eyes.py).
        The coder slot is text-only: handed a picture it fails the whole run with "image type not supported"."""
        url, key = self._core_settings()
        return eyes.describe(url, key, path, post=post)

    def _describe_pictures(self, message: str, paths: list[str]) -> str:
        """The message for a Core-brain run with pictures: each one described by the vision slot, and the coder told not to open them."""
        parts = []
        for n, path in enumerate(paths, 1):
            try:
                seen = self.describe_picture(path)
            except Exception as exc:                   # noqa: BLE001 — say so in the message; never fail the run over it
                seen = f"(Skippy Core's vision model couldn't look at it: {type(exc).__name__}: {exc})"
            parts.append(f"Picture {n} ({path}):\n{seen}")
        many = len(paths) != 1
        return (message + "\n\n[The Captain attached " + str(len(paths)) + (" pictures" if many else " picture") + ". You can't see "
                "pictures, so Skippy Core's vision model looked for you; what it saw, with the text copied out, is below. Do NOT open "
                "the picture files with the Read tool (that fails this whole run). If what you need isn't in the description, say "
                "what to look for and the Captain will send a closer photo.]\n\n" + "\n\n".join(parts))

    def name_chat(self, message: str) -> str:
        """A conversation's name, noun-verb-adjective-noun (chat_names.py), from Skippy Core; the fallback without it."""
        return chat_names.name(message, self.ask_core)

    def core_ready(self) -> bool:
        """Configured AND answering: GET {url}/v1/models with the key, at most once a minute (the reporter asks every 15 s).
        A URL that's set but unreachable (the Funnel not open yet, skippy-core down) is not ready: the picker says so."""
        url, key = self._core_settings()
        if not (url and key):
            return False
        now = time.time()
        cached = getattr(self, "_core_probe", None)
        if cached and cached[0] == url and now - cached[1] < self.CORE_PROBE_TTL_S:
            return cached[2]
        import urllib.request
        try:
            req = urllib.request.Request(url + "/v1/models", headers={"x-api-key": key, "Authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=4) as r:
                ok = r.status == 200
        except Exception:  # noqa: BLE001 — any failure is "not reachable"
            ok = False
        self._core_probe = (url, now, ok)
        return ok

    # What a Core run's own config folder gets from the Captain's Claude Code (~/.claude): his skills and plugins and the
    # settings that switch them on, so both brains work the same way for him. NEVER his login: .credentials.json, any
    # *.json holding tokens, history and sessions stay out (the folder exists precisely so his login can't win over the key).
    SHARED_DIRS = ("skills", "plugins", "agents", "commands")
    SHARED_SETTINGS = ("enabledPlugins", "extraKnownMarketplaces", "hooks", "effortLevel", "skipWorkflowUsageWarning")
    # The Core brain's window is 128K, ~30K of it Claude Code's own prompt: of the Captain's plugins it loads only the coding ones (the
    # rest, plugin authoring and maths-olympiad, cost ~2.4K tokens a turn for nothing; the Captain, 2026-10-25). A plugin is
    # "name@marketplace"; the name is what is matched.
    CORE_PLUGINS = ("code-review", "code-simplifier", "claude-md-management", "feature-dev", "claude-code-setup")

    @staticmethod
    def memory_key(path: str) -> str:
        """Claude Code's folder for a project's notes, from the project's path (D:\\Aaron\\x -> D--Aaron-x)."""
        return re.sub(r"[^A-Za-z0-9]", "-", os.path.normpath(path))

    def share_memory(self, config_home: str, cwd: str, primary: str) -> None:
        """One memory for the Captain and every brain (2026-10-25): the agent's notes folder for `cwd`, in `config_home`, is a link to his
        own (~/.claude/projects/<primary>/memory, where his Claude Desktop sessions keep what he has taught them: bash first, no confirm
        boxes, kill by PID...). Notes the agent writes land there too. A real folder already there is folded in first, never lost."""
        home = os.getenv("WORKSHOP_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")
        source = os.path.join(home, "projects", self.memory_key(primary), "memory")
        target = os.path.join(config_home, "projects", self.memory_key(cwd), "memory")
        if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(target)):
            return                                       # the Captain's own folder: it already is his memory
        try:
            os.makedirs(source, exist_ok=True)
            if os.path.isdir(target) and os.path.samefile(target, source):
                return
            if os.path.isdir(target) and not os.path.islink(target) and not _is_junction(target):
                for name in os.listdir(target):          # the agent's own notes so far: kept, in the shared folder
                    dst = os.path.join(source, name if not os.path.exists(os.path.join(source, name)) else "agent-" + name)
                    shutil.move(os.path.join(target, name), dst)
                os.rmdir(target)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.name == "nt":                          # a junction: no admin rights needed, unlike a symlink
                subprocess.run(["cmd", "/c", "mklink", "/J", target, source], capture_output=True, timeout=20)
            else:
                os.symlink(source, target, target_is_directory=True)
        except (OSError, subprocess.SubprocessError):
            pass                                         # no shared memory this run: never a reason to fail one

    def _share_claude_setup(self, config: str) -> None:
        home = os.getenv("WORKSHOP_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")
        if not os.path.isdir(home):
            return

        def copy_if_newer(src, dst, *, follow_symlinks=True):
            try:
                if os.path.getmtime(dst) >= os.path.getmtime(src) and os.path.getsize(dst) == os.path.getsize(src):
                    return dst
            except OSError:
                pass
            return shutil.copy2(src, dst, follow_symlinks=follow_symlinks)

        for name in self.SHARED_DIRS:
            src = os.path.join(home, name)
            if os.path.isdir(src):
                try:
                    shutil.copytree(src, os.path.join(config, name), dirs_exist_ok=True, copy_function=copy_if_newer,
                                    ignore=shutil.ignore_patterns(".credentials*", "*.token", "auth*.json"))
                except (OSError, shutil.Error):
                    pass                                   # a locked file now is copied next time; never fail a run over it
        try:
            mine = json.loads(Path(home, "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            mine = {}
        settings = {k: mine[k] for k in self.SHARED_SETTINGS if k in mine}
        if isinstance(settings.get("enabledPlugins"), dict):
            settings["enabledPlugins"] = {k: v for k, v in settings["enabledPlugins"].items() if k.split("@")[0] in self.CORE_PLUGINS}
        # llama.cpp's prompt cache: Claude Code's attribution header changes every request and makes llama-server re-read
        # the whole prompt each turn (reported: 24K tokens, 55 s a turn). Only settings.json's env turns it off.
        settings["env"] = {**(mine.get("env") or {}), "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"}
        try:
            Path(config, "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
        except OSError:
            pass

    def core_env(self) -> dict:
        """child_env() for a run on Skippy Core: its own config folder (no subscription login to win over the key) with the
        Captain's skills and plugins shared in, the key sent both ways (x-api-key and Bearer), the coding slot for its
        work and the chat slot for its background calls, no side traffic."""
        url, key = self._core_settings()
        config = os.path.join(os.path.dirname(self.sessions_path), ".skippy-workshop-core-claude")
        os.makedirs(config, exist_ok=True)
        self._share_claude_setup(config)
        env = child_env()
        env.update(ANTHROPIC_BASE_URL=url, ANTHROPIC_API_KEY=key, ANTHROPIC_AUTH_TOKEN=key, CLAUDE_CONFIG_DIR=config,
                   ANTHROPIC_SMALL_FAST_MODEL=CORE_FAST_MODEL, ANTHROPIC_DEFAULT_HAIKU_MODEL=CORE_FAST_MODEL,
                   ANTHROPIC_DEFAULT_SONNET_MODEL=CORE_MODEL, ANTHROPIC_DEFAULT_OPUS_MODEL=CORE_MODEL,
                   CLAUDE_CODE_MAX_CONTEXT_TOKENS=CORE_CONTEXT, CLAUDE_CODE_MAX_OUTPUT_TOKENS=CORE_MAX_OUTPUT,
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_TELEMETRY="1",
                   SKIPPY_CORE_EYES="1")                     # the coder is text-only: eyes.py looks at pictures for it
        return env

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
        if project.inplace:                            # the Captain's own folder: nothing to clone or pull, just be there
            if not Path(clone).is_dir():
                run.finish(False, f"{project.name} isn't a folder on skippy-pc any more")
                return False
            return True
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

    def command(self, session: str | None, project: str | None = None, brain: str = "claude") -> list[str]:
        """The claude command line. Every element is a single line (see the module docstring): the prompt is a FILE
        (write_prompt() makes it; _work does that before every run) and the message goes by stdin.

        --system-prompt-snapshot off: by default Claude Code records the system prompt on a conversation's first
        request and sends that record on every resume, whatever a later launch passes (measured here on 2.1.285: a
        resumed session ignored a changed prompt file). Left on, a conversation that began before this fix would keep
        the old one-line prompt, and an edit to workshop_prompt.md would not reach a chat already under way. Off, the
        file is read fresh on every request, so the Captain's rules are always the current ones.

        --add-dir is the project's primary checkout (the Captain's): readable, and for SkippyTel the place a skippy-pc
        service is deployed from. The working directory (the clone) is set by _spawn."""
        thinking = ["--model", MODEL, "--effort", EFFORT] if brain == "claude" else ["--model", CORE_MODEL]
        cmd = [*self.claude_cmd, "-p", "--output-format", "stream-json", "--verbose",
               *thinking, "--dangerously-skip-permissions",
               "--append-system-prompt-file", str(self.prompt_path(project)), "--system-prompt-snapshot", "off",
               "--add-dir", self.project(project).primary, "--settings", self.lane_settings()]
        house = self.house_mcp_config()
        if house:
            cmd += ["--mcp-config", house]
        if session:
            cmd += ["--resume", session]
        return self._one_line_each(cmd)

    def house_mcp_config(self) -> str | None:
        """The house's tools for every run, either brain (services/satellite/house_mcp.py on the satellite's venv: lights and switches,
        HomeKit, Matter, Leviton). None, and the run goes without them, until that venv exists. Written next to the runs."""
        satellite = Path(__file__).resolve().parents[1] / "satellite"
        python, server = satellite / ".venv" / "Scripts" / "python.exe", satellite / "house_mcp.py"
        if not (python.is_file() and server.is_file()):
            return None
        text = json.dumps({"mcpServers": {"skippy-house": {"type": "stdio", "command": python.as_posix(), "args": [server.as_posix()]}}},
                          indent=1)
        for folder in (Path(self.state_dir), Path(tempfile.gettempdir()) / "skippy-workshop"):
            path = folder / "house-mcp.json"
            try:
                if not path.is_file() or path.read_text(encoding="utf-8") != text:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
                return str(path)
            except OSError:
                continue
        return None

    def lane_settings(self) -> str:
        """The settings file every run gets with --settings: the lane guard (lane_guard.py) as a PreToolUse hook, so a run
        changes files only in its own conversation's folder (the Captain's rule, 2026-10-23). Written next to the runs."""
        hook = f'"{sys.executable}" "{Path(__file__).resolve().with_name("lane_guard.py")}"'
        see = f'"{sys.executable}" "{Path(__file__).resolve().with_name("eyes.py")}"'   # a Core run's Read of a picture (eyes.py)
        # after a compaction, the Captain's request back in front of the agent, word for word (restate_request.py)
        restate = f'"{sys.executable}" "{Path(__file__).resolve().with_name("restate_request.py")}"'
        want = {"hooks": {"PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
                                          "hooks": [{"type": "command", "command": hook, "timeout": 10}]},
                                         {"matcher": "Read", "hooks": [{"type": "command", "command": see, "timeout": 200}]}],
                          "SessionStart": [{"matcher": "compact", "hooks": [{"type": "command", "command": restate, "timeout": 10}]}]}}
        return self._settings_file("lane-settings.json", want)

    def sdk_settings(self, claude_md_excludes=None) -> str:
        """The SDK engine's settings file: only the hooks that stay scripts (a Core run's Read of a picture, and the compaction
        backstop), and the CLAUDE.md files this project must not load (claudeMdExcludes, from engines.json). The lane rule and the
        stop gate are callbacks in sdk_engine.py, not entries here."""
        see = f'"{sys.executable}" "{Path(__file__).resolve().with_name("eyes.py")}"'
        restate = f'"{sys.executable}" "{Path(__file__).resolve().with_name("restate_request.py")}"'
        want = {"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": see, "timeout": 200}]}],
                          "SessionStart": [{"matcher": "compact", "hooks": [{"type": "command", "command": restate, "timeout": 10}]}]}}
        if claude_md_excludes:
            want["claudeMdExcludes"] = list(claude_md_excludes)
        return self._settings_file("sdk-settings.json", want)

    def _settings_file(self, name: str, want: dict) -> str:
        """Write a settings file next to the runs (the temp folder if that fails) and return its path; it is only written when it changed."""
        text = json.dumps(want, indent=1)
        problem = None
        for folder in (Path(self.state_dir), Path(tempfile.gettempdir()) / "skippy-workshop"):   # the temp folder if the state one fails
            path = folder / name
            try:
                if not path.is_file() or path.read_text(encoding="utf-8") != text:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
                return str(path)
            except OSError as exc:
                problem = exc
        raise ValueError(f"can't write the settings {name} ({problem}): no run starts without them")

    @staticmethod
    def _one_line_each(argv: list[str]) -> list[str]:
        """argv, or ValueError naming the culprit: a newline in an argument is cut off, along with every argument after
        it, by cmd.exe (the R01 incident), so it must never reach a launch. Nothing today can put one there (the
        paths come from this PC's configuration, the session id from claude's own output); this is the tripwire."""
        for arg in argv:
            if "\n" in arg or "\r" in arg:
                raise ValueError(f"an argument contains a newline ({arg[:40]!r}), which cmd.exe would cut the command line at")
        return argv

    def _spawn(self, argv: list[str], cwd: str | None = None, env: dict | None = None) -> subprocess.Popen:
        """Start claude (or whatever `argv` is) the one way this module does it: no newline in any argument, the
        clone as the working directory, a pipe each way, the child_env() environment, and on POSIX its own process
        group so the whole tree can be killed. The Windows test runs the real claude.cmd through exactly this."""
        extra = {"start_new_session": True} if sys.platform != "win32" else {}
        return subprocess.Popen(self._one_line_each(argv), cwd=cwd or self.repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                env=env or child_env(), bufsize=1, **extra)

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
        if not project.repo or not project.branch:     # a folder that isn't a registered repo: no deploy to follow
            return
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

    def _attach_images(self, run: Run) -> None:
        """The screenshots the agent asked to show (`SKIPPY_IMAGE: <path>`), as `image` events under its answer. Only a
        file inside this run's project clone (where tools/workshop_screenshot.py writes), at most MAX_SHOTS, each a real
        PNG or JPEG (by its first bytes) of at most MAX_SHOT_BYTES: anything else is skipped with a short note, so a
        path the agent got wrong can never send some other file off this PC."""
        clone = os.path.realpath(self.project(run.project).clone)
        for raw in run.image_paths[:MAX_SHOTS]:
            path = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(clone, raw))
            name = os.path.basename(path)[:80]
            try:
                inside = os.path.commonpath([clone, path]) == clone
            except ValueError:                         # another drive
                inside = False
            data = b""
            if inside and os.path.isfile(path) and os.path.getsize(path) <= MAX_SHOT_BYTES:
                with open(path, "rb") as f:
                    data = f.read(MAX_SHOT_BYTES + 1)
            mime = image_mime(data)
            if not inside or mime is None or len(data) > MAX_SHOT_BYTES:
                run.emit({"t": "text", "text": f"(couldn't attach the picture {name}: it must be a PNG, JPEG or .glb of at "
                                                f"most {MAX_SHOT_BYTES // (1024 * 1024)} MB inside my clone)"})
                continue
            run.emit({"t": "image", "mime": mime, "name": name, "b64": base64.b64encode(data).decode("ascii")})
        for prompt, tier in run.picture_prompts[:MAX_PICTURES]:      # pictures to make: the portal queues each (its id is the job's)
            run.emit({"t": "picture", "id": secrets.token_hex(8), "prompt": prompt, **({"tier": tier} if tier else {})})
        for name, args in run.popups[:MAX_POPUPS]:                    # pop-up jobs: the same, any model in the registry
            if args is None:
                run.emit({"t": "text", "text": f"(couldn't ask for {name}: its arguments must be one JSON object on the same line)"})
                continue
            event = {"t": "popup", "id": secrets.token_hex(8), "name": name, "args": {k: v for k, v in args.items() if k != "_file"}}
            if "_file" in args:
                given, why = self._popup_file(run, args["_file"], clone)
                if given is None:
                    run.emit({"t": "text", "text": f"(couldn't hand {name} the file: {why})"})
                    continue
                event["input"] = given
            run.emit(event)

    def _popup_file(self, run: Run, raw, clone: str) -> tuple[dict | None, str]:
        """The file a SKIPPY_POPUP line names in `_file`: a picture or a sound inside the run's clone or the attachments folder."""
        if not isinstance(raw, str) or not raw.strip():
            return None, "_file must be a path"
        path = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(clone, raw))
        roots = [clone, os.path.realpath(self.attach_dir)]
        try:
            inside = any(os.path.commonpath([root, path]) == root for root in roots)
        except ValueError:                             # another drive
            inside = False
        if not inside or not os.path.isfile(path):
            return None, "it must be a file inside my clone or the Captain's attachments"
        if os.path.getsize(path) > MAX_POPUP_INPUT:
            return None, f"it is over {MAX_POPUP_INPUT // 1_000_000} MB"
        with open(path, "rb") as f:
            data = f.read(MAX_POPUP_INPUT + 1)
        mime = file_mime(data)
        if mime is None:
            return None, "it isn't a picture (png, jpeg, webp) or a sound (wav, flac, ogg, mp3)"
        return {"type": mime, "name": os.path.basename(path)[:80], "b64": base64.b64encode(data).decode("ascii")}, ""

    def _work(self, run: Run) -> None:
        try:
            if not self._prepare_repo(run):
                return
            try:
                before = self._head(self.project(run.project).clone)
            except (OSError, subprocess.SubprocessError):
                before = None
            def at_end(r: Run) -> None:                # each on its own: a failed ship check must not lose the screenshot
                for step in (lambda: self._ship_check(r, before), lambda: self._attach_images(r)):
                    try:
                        step()
                    except Exception:  # noqa: BLE001
                        pass
            run.before_finish = at_end
            try:
                self.write_prompt(run.project)
            except OSError as exc:                     # no rules, no run: never start the agent without them
                run.finish(False, f"couldn't write my instructions file ({self.prompt_path(run.project)}): {exc}"[:300])
                return
            session = self._sessions().get(self.session_key(run.project, run.conv, run.brain))
            if sdk_engine.engine_for(self.state_dir, run.brain, run.project) == "sdk":
                sdk_engine.run_core(self, run, session)         # typed events, callbacks, bounded segments (sdk_engine.py)
            else:
                self._attempt(run, session)
        except Exception as exc:  # noqa: BLE001 — a crash must end the run, or the chat waits forever
            run.finish(False, f"the bridge hit a snag: {exc}"[:300])
        finally:
            # How it ended, and a fresh copy of Claude Code's whole transcript (the session id is known by now).
            self.convlog.finished(run, self._sessions().get(self.session_key(run.project, run.conv, run.brain)))

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
        if run.pictures:                               # once per run, before the first attempt (a retry reuses the words)
            run.message, run.pictures = self._describe_pictures(run.message, run.pictures), []
        try:
            project = self.project(run.project)
            env = self.core_env() if run.brain == "core" else child_env()
            # the lane guard's one exception: SkippyTel's primary checkout, where a skippy-pc service is pulled and deployed
            env["SKIPPY_LANE_DEPLOY"] = project.primary if project.deploy == "skippytel" and not project.inplace else ""
            # One memory with the Captain's own Claude Code (both brains): the clone's notes folder links to his project's notes.
            self.share_memory(env.get("CLAUDE_CONFIG_DIR") or os.getenv("WORKSHOP_CLAUDE_HOME")
                              or os.path.join(os.path.expanduser("~"), ".claude"), project.clone, project.primary)
            proc = self._spawn(self.command(session, run.project, run.brain), cwd=project.clone, env=env)
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
                        said, shots = split_images(block["text"])
                        run.image_paths.extend(shots)          # attached when the run ends (see _attach_images)
                        said, wanted = split_pictures(said)
                        run.picture_prompts.extend(wanted)     # asked for when the run ends (see _attach_images)
                        said, jobs = split_popups(said)
                        run.popups.extend(jobs)                # ...and so are pop-up jobs
                        if not said:
                            continue
                        if pending is not None:
                            run.emit({"t": "text", "text": pending})
                        pending = said[:MAX_TEXT]
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
            self._remember(self.session_key(run.project, run.conv, run.brain), seen_session)
        if proc.returncode not in (0, None):
            run.finish(False, f"Claude Code exited with code {proc.returncode}")
        else:
            run.finish(True)
