"""What a long run knows about itself, kept as data (the Captain, 2026-10-26: no shims, no prompt injection, no timers, no
text-stripping passes; fix it right).

A Core run is long: the coder's window is 131k tokens and a real task fills it several times over. Claude Code's answer is to
ask the model to summarize its own conversation; the Core coder does that badly (4 of 8 summaries in the Shifting Prophesy
trials were one sentence and a refused tool call), and an agent that gets one has lost its task. So the work does not depend on
a summary. It runs in bounded SEGMENTS: Claude Code ends a segment itself at its turn limit (a result message, not a timer),
and the next segment is a fresh session whose opening prompt is built here from facts:

    * the Captain's request, word for word;
    * the repository as it is now (git status, git log): the work itself is the memory;
    * the previous segment's last few messages and tool calls, as it said and did them (selected, never summarized).

Everything here is plain data in, plain data out: no SDK, no subprocess but git, no regex over anything a model wrote.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections import deque
from dataclasses import dataclass, field

LAST_WORDS = 4                  # the previous segment's last text blocks, verbatim
LAST_TOOLS = 20                 # ... and its last tool calls, one line each
MAX_WORDS_CHARS = 2500          # each of those text blocks is cut at this
MAX_STATUS_LINES = 60
MAX_LOG_LINES = 15
GATE_LIMIT = 2                  # how many times the stop gate may send an agent back to its work, per run


@dataclass
class Journal:
    """What the run said and did, in order of arrival, from the engine's typed events (never from parsing text)."""
    words: deque = field(default_factory=lambda: deque(maxlen=LAST_WORDS))
    tools: deque = field(default_factory=lambda: deque(maxlen=LAST_TOOLS))
    turns: int = 0
    tool_calls: int = 0

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
    return "\n".join(parts) + "\n"


def session_file(config_dir: str, cwd: str, session_id: str) -> str:
    """Where Claude Code keeps a session's transcript: <config>/projects/<cwd with every non-alphanumeric as '-'>/<id>.jsonl."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    return os.path.join(config_dir, "projects", slug, f"{session_id}.jsonl")


def can_resume(config_dir: str, cwd: str, session_id: str | None) -> bool:
    """A session can be picked up when its transcript is there. Checked before launching, so a missing one is a plain
    decision to start fresh, not a failed launch to retry."""
    return bool(session_id) and os.path.isfile(session_file(config_dir, cwd, session_id))
