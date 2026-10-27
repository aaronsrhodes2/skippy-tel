"""What a long run knows about itself, kept as data (the Captain, 2026-10-26: no shims, no prompt injection, no timers, no
text-stripping passes; fix it right).

A Core run is long: the coder's window is 131k tokens and a real task fills it several times over. Claude Code's answer is to
ask the model to summarize its own conversation; the Core coder does that badly (4 of 8 summaries in the Shifting Prophesy
trials were one sentence and a refused tool call), and an agent that gets one has lost its task. So the work does not depend on
a summary. It runs in bounded SEGMENTS: Claude Code ends a segment itself at its turn limit (a result message, not a timer),
and the next segment is a fresh session whose opening prompt is built here from facts:

    * the Captain's request, word for word;
    * the repository as it is now (git status, git log): the work itself is the memory;
    * the previous segment's last few messages and tool calls, as it said and did them (selected, never summarized);
    * a MAP of the files: each one's length and where its parts are (functions, classes, headings, by line), so a fresh session
      can read the part it needs with an offset instead of reading every file whole to find its bearings (Claude Code makes a new
      session Read a file before editing it; T07 and purple:1:8 spent about 60% of their tool calls re-reading).

Everything here is plain data in, plain data out: no SDK, no subprocess but git, no regex over anything a model wrote.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field

LAST_WORDS = 4                  # the previous segment's last text blocks, verbatim
LAST_TOOLS = 20                 # ... and its last tool calls, one line each
MAX_WORDS_CHARS = 2500          # each of those text blocks is cut at this
MAX_STATUS_LINES = 60
MAX_LOG_LINES = 15
GATE_LIMIT = 2                  # how many times the stop gate may send an agent back to its work, per run


MAP_CHARS = 9000                # the file map's size (about 2,500 tokens)
MAP_FILE_CHARS = 420            # ... and one file's line of it
MAP_FILES = 60
MAX_MAP_BYTES = 400_000         # a file bigger than this is listed without its parts
EXTERNAL_FILES = 8              # files the run read from outside the repository (references) that get a map line too
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


@dataclass
class Journal:
    """What the run said and did, in order of arrival, from the engine's typed events (never from parsing text)."""
    words: deque = field(default_factory=lambda: deque(maxlen=LAST_WORDS))
    tools: deque = field(default_factory=lambda: deque(maxlen=LAST_TOOLS))
    turns: int = 0
    tool_calls: int = 0
    edited: dict = field(default_factory=dict)         # the files it wrote or edited, in order (a dict is an ordered set)
    read: dict = field(default_factory=dict)           # the files it read

    def touched(self, name: str, args: dict) -> None:
        path = args.get("file_path") or args.get("notebook_path") if isinstance(args, dict) else None
        if isinstance(path, str) and path:
            (self.edited if name in EDIT_TOOLS else self.read if name == "Read" else {}).setdefault(path, True)

    def said(self, text: str) -> None:
        if text and text.strip():
            self.words.append(text.strip()[:MAX_WORDS_CHARS])

    def did(self, name: str, detail: str) -> None:
        self.tool_calls += 1
        self.tools.append(f"{name}: {detail}" if detail else name)

    def turn(self) -> None:
        self.turns += 1


def git_text(clone: str, *args: str, timeout: float = 20.0) -> str:
    """Stdout of a read-only git command in `clone`, or '' (a missing git or a timeout must not stop a handoff)."""
    try:
        out = subprocess.run(["git", *args], cwd=clone, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.rstrip() if out.returncode == 0 else ""      # not strip(): porcelain's first line starts with a space


def dirty_paths(porcelain: str) -> list[str]:
    """The paths in `git status --porcelain` (v1: two status letters, a space, then the path; renames 'old -> new')."""
    paths = []
    for line in porcelain.splitlines():
        if len(line) > 3:
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            if path:
                paths.append(path)
    return paths


def uncommitted_since(clone: str, baseline: set[str]) -> list[str]:
    """Paths that are dirty now and were not dirty when the run began: this run's own loose ends. Work an earlier
    task left behind is not the run's to answer for."""
    return [p for p in dirty_paths(git_text(clone, "status", "--porcelain", "--untracked-files=all")) if p not in baseline]


def gate_reason(paths: list[str]) -> str:
    """The stop gate's feedback: facts only. What the agent does with them is its call."""
    shown = ", ".join(paths[:12]) + (f" (and {len(paths) - 12} more)" if len(paths) > 12 else "")
    return f"These paths changed during this run and are not committed: {shown}."


_JS = [re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)"),
       re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)"), re.compile(r"^export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)")]
_PY = [re.compile(r"^(?:async\s+)?def\s+(\w+)"), re.compile(r"^class\s+(\w+)")]
_LUA = [re.compile(r"^(?:local\s+)?function\s+([\w.:]+)")]
_JAVA = [re.compile(r"^\s*(?:(?:public|protected|private|static|final|abstract)\s+)*(?:class|interface|enum)\s+(\w+)"),
         re.compile(r"^\s{1,8}(?:(?:public|protected|private)\s+)(?:static\s+)?(?:final\s+)?[\w<>\[\],.?\s]+?\s(\w+)\s*\(")]
_MD = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
_PATTERNS = {".js": _JS, ".mjs": _JS, ".cjs": _JS, ".ts": _JS, ".tsx": _JS, ".jsx": _JS, ".py": _PY, ".lua": _LUA, ".java": _JAVA}
_LISTED = set(_PATTERNS) | {".md", ".json", ".txt", ".html", ".css", ".sh", ".toml", ".yml", ".yaml", ".go", ".rs", ".c", ".h", ".cpp", ".cs", ".kt", ".rb"}


def _parts(path: str, text: str, limit: int = 14) -> str:
    """Where a file's parts are, 'name:line' each: functions and classes by language, headings in a markdown file."""
    ext = os.path.splitext(path)[1].lower()
    found: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        if ext == ".md":
            m = _MD.match(line)
            if m:
                found.append(f"{m.group(2)[:40]}:{n}")
        else:
            for pat in _PATTERNS.get(ext, ()):
                m = pat.match(line)
                if m:
                    found.append(f"{m.group(1)}:{n}")
                    break
        if len(found) >= limit:
            break
    return ", ".join(found)


def _map_line(shown: str, path: str) -> str | None:
    try:
        if os.path.getsize(path) > MAX_MAP_BYTES:
            return f"{shown} (large)"
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    n = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    parts = _parts(path, text)
    line = f"{shown} ({n} lines){': ' + parts if parts else ''}"
    return line if len(line) <= MAP_FILE_CHARS else line[:MAP_FILE_CHARS - 3] + "..."


def file_map(clone: str, journal: Journal) -> list[str]:
    """One line per file: its length and where its parts are. The files the run edited come first, then the rest by how recently
    they changed; then the few files it read from outside the repository (references), by their absolute path."""
    names = git_text(clone, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    names = [n for n in names if os.path.splitext(n)[1].lower() in _LISTED and not n.startswith(".skippy-goal/")]
    full = lambda n: os.path.normpath(os.path.join(clone, n))                     # noqa: E731
    edited = {os.path.normcase(os.path.normpath(p)) for p in journal.edited}
    def age(n):
        try:
            return -os.path.getmtime(full(n))
        except OSError:
            return 0.0
    names.sort(key=lambda n: (os.path.normcase(full(n)) not in edited, age(n)))
    out: list[str] = []
    for n in names[:MAP_FILES]:
        line = _map_line(n.replace("\\", "/"), full(n))
        if line:
            out.append(line)
    inside = {os.path.normcase(full(n)) for n in names}
    root = os.path.normcase(os.path.normpath(clone)) + os.sep                    # (with the separator: "repo-ref" is not inside "repo")
    outside = [p for p in journal.read if os.path.normcase(os.path.normpath(p)) not in inside
               and os.path.splitext(p)[1].lower() in _LISTED and not (os.path.normcase(os.path.normpath(p)) + os.sep).startswith(root)]
    for p in outside[:EXTERNAL_FILES]:
        line = _map_line(p.replace("\\", "/"), p)
        if line:
            out.append(line)
    if len(names) > MAP_FILES:
        out.append(f"... and {len(names) - MAP_FILES} more files")
    return out


def handoff(request: str, segment: int, clone: str, journal: Journal) -> str:
    """The opening prompt of segment `segment` (2 or later): the request, then what is true now."""
    status = git_text(clone, "status", "--short", "--untracked-files=all").splitlines()
    log = git_text(clone, "log", "--oneline", f"-{MAX_LOG_LINES}").splitlines()
    parts = [request.strip(), "", "---",
             f"This is segment {segment} of the job above. The segment before it used its turn budget and its session is over;"
             " nothing from it carries over except what follows."]
    parts += ["", "Repository, as it is now:"]
    parts += ["  git status:"] + [f"    {s}" for s in status[:MAX_STATUS_LINES]] if status else ["  git status: clean"]
    if len(status) > MAX_STATUS_LINES:
        parts.append(f"    ... and {len(status) - MAX_STATUS_LINES} more")
    parts += ["  git log:"] + [f"    {s}" for s in log] if log else ["  git log: no commits"]
    if journal.words:
        parts += ["", "The previous segment's last messages, verbatim, oldest first:"]
        for w in journal.words:
            parts += ["  >>>", *[f"  {line}" for line in w.splitlines()], "  <<<"]
    if journal.tools:
        parts += ["", "Its last tool calls, oldest first:"] + [f"  {t}" for t in journal.tools]
    files = file_map(clone, journal)
    if files:
        budget, shown = MAP_CHARS, []
        for line in files:
            if budget - len(line) - 3 < 0:
                shown.append("  ... (the map is cut here)")
                break
            shown.append(f"  {line}")
            budget -= len(line) + 3
        parts += ["", "The files, with their length and where their parts are (name:line), so a part can be read with an offset and a limit:"] + shown
    return "\n".join(parts) + "\n"


def session_file(config_dir: str, cwd: str, session_id: str) -> str:
    """Where Claude Code keeps a session's transcript: <config>/projects/<cwd with every non-alphanumeric as '-'>/<id>.jsonl."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return os.path.join(config_dir, "projects", slug, f"{session_id}.jsonl")


def can_resume(config_dir: str, cwd: str, session_id: str | None) -> bool:
    """A session can be picked up when its transcript is there. Checked before launching, so a missing one is a plain
    decision to start fresh, not a failed launch to retry."""
    return bool(session_id) and os.path.isfile(session_file(config_dir, cwd, session_id))
