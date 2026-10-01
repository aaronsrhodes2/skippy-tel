"""Every private-Skippy conversation, kept in full in files the Captain owns (his rule, 2026-09-30).

Where: D:\\Aaron\\conversations\\private-skippy (env WORKSHOP_CONVERSATIONS), one pair of files per chat conversation:

    <YYYY-MM-DD>_<conv>.md                         readable: each message he sent, then everything Skippy said and every
                                                   tool step, written as it happens (a crash mid-run keeps what came before)
    <YYYY-MM-DD>_<conv>.claude-<session>.jsonl     Claude Code's own transcript of that conversation, copied after every
                                                   message: the complete record, tool outputs included

Why the copy: Claude Code keeps its transcripts under ~/.claude/projects and deletes them after 30 days by default, the
bridge forgets a finished run after an hour, and the browser only keeps a display copy per device. These files are none
of those.

Writing here must never hurt a run: every failure is swallowed (and printed once to the bridge's log).

    python conversation_log.py --backfill          copy + render every transcript Claude Code still holds for the workshop
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = os.getenv("WORKSHOP_CONVERSATIONS", r"D:\Aaron\conversations\private-skippy")
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
_SAFE = re.compile(r"[^A-Za-z0-9-]")


def claude_project_dir(repo: str, projects: Path = CLAUDE_PROJECTS) -> Path:
    """Claude Code files a session under ~/.claude/projects/<the working directory with every non-alphanumeric
    character turned into '-'>, e.g. D:\\Aaron\\development\\SkippyTel-workshop -> D--Aaron-development-SkippyTel-workshop."""
    return projects / re.sub(r"[^A-Za-z0-9]", "-", repo)


def _stamp(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def _event_md(event: dict) -> str:
    if event.get("t") == "text":
        return str(event.get("text") or "").strip() + "\n"
    if event.get("t") == "tool":
        detail = str(event.get("detail") or "")
        return f"- `{event.get('name')}`" + (f": `{detail}`" if detail else "") + "\n"
    return ""


class ConversationLog:
    def __init__(self, root: str = ROOT, repo: str = "", projects: Path = CLAUDE_PROJECTS, clock=time.time):
        self.root, self.repo, self.projects, self.clock = Path(root), repo, projects, clock
        self._lock = threading.Lock()
        self._written: dict[str, int] = {}       # task -> events already written for it
        self._warned = False

    # ── paths ─────────────────────────────────────────────────────────────
    def path(self, conv: str, when: float | None = None) -> Path:
        """This conversation's file: the one that already exists for it, or a new one dated today."""
        conv = _SAFE.sub("", conv)[:64] or "unknown"
        found = sorted(self.root.glob(f"*_{conv}.md"))
        if found:
            return found[0]
        day = time.strftime("%Y-%m-%d", time.localtime(self.clock() if when is None else when))
        return self.root / f"{day}_{conv}.md"

    def _append(self, path: Path, text: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            new = not path.exists()
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                if new:
                    f.write(f"# Private Skippy conversation\n\nconversation `{path.stem.split('_', 1)[-1]}`, "
                            f"started {_stamp(self.clock())}\n")
                f.write(text)
        except OSError as exc:
            self._warn(f"couldn't write {path}: {exc}")

    def _warn(self, message: str) -> None:
        if not self._warned:                     # once: a full disk must not flood the bridge's log
            self._warned = True
            print(f"[bridge] conversation log: {message}", file=sys.stderr, flush=True)

    # ── the hooks the workshop calls ──────────────────────────────────────
    def started(self, run) -> None:
        """A message arrived: write it before anything runs, so even a run that never starts is on record."""
        with self._lock:
            self._written[run.task] = 0
            self._append(self.path(run.conv, run.created),
                         f"\n---\n\n## The Captain, {_stamp(run.created)}\n\n{str(run.message).strip()}\n\n"
                         f"### Skippy\n\n")

    def progress(self, run) -> None:
        """New events since the last call (called on every event, so a crash keeps everything said so far)."""
        with self._lock:
            done = self._written.get(run.task)
            if done is None:                     # a run this log never saw start (re-attached after a bridge restart)
                return
            events = list(run.events)[done:]
            if events:
                self._append(self.path(run.conv, run.created), "".join(_event_md(e) for e in events))
                self._written[run.task] = done + len(events)

    def finished(self, run, session: str | None) -> None:
        """The run ended: the rest of its events, how it ended, and a fresh copy of Claude Code's full transcript."""
        self.progress(run)
        with self._lock:
            if self._written.pop(run.task, None) is None:
                return
            path = self.path(run.conv, run.created)
            ended = _stamp(run.finished_at or self.clock())
            how = "done" if run.ok else f"**ended with an error:** {run.error}"
            self._append(path, f"\n_{ended}, {how}_\n")
        if session:
            self.copy_session(path, session)

    def copy_session(self, md_path: Path, session: str) -> Path | None:
        session = _SAFE.sub("", session)
        src = claude_project_dir(self.repo, self.projects) / f"{session}.jsonl"
        dst = md_path.with_name(f"{md_path.stem}.claude-{session}.jsonl")
        try:
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                return dst
        except OSError as exc:
            self._warn(f"couldn't copy {src}: {exc}")
        return None


# ── backfill: what Claude Code still holds from before this existed ──────────
def render_transcript(jsonl: Path) -> str:
    """A Claude Code session transcript as readable Markdown: every user message, every assistant text, every tool
    call with its input, and every tool result (results cut at 4000 characters here; the .jsonl beside it has them whole)."""
    out = []
    for line in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        kind, msg = evt.get("type"), evt.get("message") or {}
        when = evt.get("timestamp", "")
        content = msg.get("content")
        if kind == "user":
            if isinstance(content, str):
                out.append(f"\n---\n\n## The Captain, {when}\n\n{content.strip()}\n")
                continue
            for block in content or []:
                if block.get("type") == "text":
                    out.append(f"\n---\n\n## The Captain, {when}\n\n{block.get('text', '').strip()}\n")
                elif block.get("type") == "tool_result":
                    body = block.get("content")
                    if isinstance(body, list):
                        body = "\n".join(str(b.get("text", "")) for b in body if isinstance(b, dict))
                    out.append(f"\n<details><summary>tool result</summary>\n\n```\n{str(body)[:4000]}\n```\n</details>\n")
        elif kind == "assistant":
            for block in content or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    out.append(f"\n**Skippy:** {block['text'].strip()}\n")
                elif block.get("type") == "tool_use":
                    out.append(f"\n- `{block.get('name')}` `{json.dumps(block.get('input'), ensure_ascii=False)[:2000]}`\n")
    return "# Private Skippy conversation (rendered from Claude Code's transcript)\n" + "".join(out)


def backfill(root: str = ROOT, repo: str = "", projects: Path = CLAUDE_PROJECTS, sessions_file: str = "") -> int:
    """Copy and render every workshop transcript Claude Code still holds. Files are named like live ones when the
    sessions file says which conversation a session belongs to; otherwise by the session id."""
    conv_of: dict[str, str] = {}
    try:
        conv_of = {s: c for c, s in json.loads(Path(sessions_file).read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        pass
    target, n = Path(root), 0
    target.mkdir(parents=True, exist_ok=True)
    for src in sorted(claude_project_dir(repo, projects).glob("*.jsonl")):
        session = src.stem
        day = time.strftime("%Y-%m-%d", time.localtime(src.stat().st_mtime))
        stem = f"{day}_{_SAFE.sub('', conv_of.get(session, session))[:64]}"
        shutil.copy2(src, target / f"{stem}.claude-{session}.jsonl")
        md = target / f"{stem}.claude-{session}.md"
        md.write_text(render_transcript(src), encoding="utf-8", newline="\n")
        n += 1
    return n


if __name__ == "__main__":
    if sys.argv[1:] == ["--backfill"]:
        import workshop_engine as engine
        count = backfill(ROOT, engine.REPO, CLAUDE_PROJECTS, engine.SESSIONS)
        print(f"{count} transcript(s) copied and rendered into {ROOT}")
    else:
        print(__doc__)
