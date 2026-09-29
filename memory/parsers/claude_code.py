"""Claude Code session transcripts → Conversation.

Claude Code writes one JSONL file per session under
`~/.claude/projects/<project-path-slug>/<session-id>.jsonl`. The archive job
(scripts/claude-history-sync.*) copies that tree from every machine into
`<archive>/claude-code/<machine>/`, so the layout this parser reads is

    claude-code/<machine>/<project-slug>/<session-id>.jsonl

(or a bare `projects/` folder, where the machine is unknown).

Each line is an event: {type, message: {role, content}, timestamp, cwd,
isSidechain, isMeta, ...}. Only the human-visible conversation is kept —
user prompts and assistant text. Tool calls/results, thinking, subagent
(sidechain) traffic, harness reminders and bookkeeping events are dropped.
A session still being written may end in a half-line; bad lines are skipped.
"""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePath
from typing import Iterator

from memory.parsers.common import parse_ts
from memory.schema import Conversation, Message

SOURCE = "claude-code"

# Harness-injected wrappers that aren't part of what the human said.
_NOISE = re.compile(
    r"<(system-reminder|command-name|command-message|command-args|local-command-stdout|"
    r"local-command-stderr|local-command-caveat|task-notification)>.*?</\1>",
    re.S,
)
_TITLE_LEN = 80
_MENTION = re.compile(r'@"[^"]*"|@\S+')   # "@file" references carry no topic


def looks_like_path(path: Path) -> bool:
    return path.is_dir() and next(path.rglob("*.jsonl"), None) is not None


def _text(content) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n\n".join(b.get("text", "") for b in content
                           if isinstance(b, dict) and b.get("type") == "text")
    else:
        return ""
    return _NOISE.sub("", text).strip()


def _title(messages: list[Message]) -> str:
    """First line of the first user prompt that says something besides @file mentions."""
    for m in messages:
        if m.role != "user":
            continue
        for line in m.text.splitlines():
            line = _MENTION.sub("", line).strip()
            if line:
                return line[:_TITLE_LEN]
    return "Untitled"


def parse_session(path: Path, device: str = "unknown") -> Conversation | None:
    messages: list[Message] = []
    title = ""
    cwd = ""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("type") == "summary" and ev.get("summary") and not title:
                title = str(ev["summary"]).strip()
                continue
            if ev.get("type") not in ("user", "assistant"):
                continue
            if ev.get("isSidechain") or ev.get("isMeta") or ev.get("isCompactSummary"):
                continue
            cwd = cwd or ev.get("cwd") or ""
            msg = ev.get("message") or {}
            text = _text(msg.get("content"))
            if not text:
                continue
            messages.append(Message(
                role="user" if ev["type"] == "user" else "assistant",
                text=text,
                created=parse_ts(ev.get("timestamp")),
            ))
    if not messages:
        return None

    title = title or _title(messages)
    project = PurePath(cwd.replace("\\", "/")).name if cwd else path.parent.name
    started = messages[0].created or parse_ts(path.stat().st_mtime)
    updated = messages[-1].created or started
    return Conversation(
        source=SOURCE,
        id=path.stem,
        title=f"{project}: {title}" if project else title,
        started=started,
        updated=updated,
        messages=messages,
        device=device,
    )


def parse_path(root: Path) -> Iterator[Conversation]:
    """Every session under `root`. With the archive layout
    (<machine>/<project>/<session>.jsonl) the machine becomes the device."""
    for path in sorted(root.rglob("*.jsonl")):
        rel = path.relative_to(root).parts
        device = rel[0] if len(rel) == 3 else "unknown"
        conv = parse_session(path, device=device)
        if conv is not None:
            yield conv
