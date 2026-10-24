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

import chat_names

ROOT = os.getenv("WORKSHOP_CONVERSATIONS", r"D:\Aaron\conversations\private-skippy")
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
_SAFE = re.compile(r"[^A-Za-z0-9-]")
INDEX_SUFFIX = ".turns.json"          # <YYYY-MM-DD>_<conv>.turns.json: the turns index (see ConversationLog._index)
DEFAULT_PROJECT = "skippytel"
MAX_TURNS = 200                       # turns one conversation's index keeps (the Markdown keeps everything)
MAX_TURN_EVENTS = 400                 # events one turn keeps there (the last ones: the answer is at the end)
MAX_MESSAGE = 8000
MAX_EVENT_TEXT = 4000
MAX_LISTED = 50
MAX_CONVERSATION_BYTES = 600_000      # one conversation as the bridge sends it (the portal takes 1 MB per request)
_CONV_ID = re.compile(r"[A-Za-z0-9-]{8,64}")


def claude_project_dir(repo: str, projects: Path = CLAUDE_PROJECTS) -> Path:
    """Claude Code files a session under ~/.claude/projects/<the working directory with every non-alphanumeric
    character turned into '-'>, e.g. D:\\Aaron\\development\\SkippyTel-workshop -> D--Aaron-development-SkippyTel-workshop."""
    return projects / re.sub(r"[^A-Za-z0-9]", "-", repo)


def _stamp(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def _event_md(event: dict) -> str:
    if not isinstance(event, dict):
        return ""
    if event.get("t") == "text":
        return str(event.get("text") or "").strip() + "\n"
    if event.get("t") == "tool":
        detail = str(event.get("detail") or "").replace("\n", " \u00a6 ")       # (a checklist is one line in the archive)
        return f"- `{event.get('name')}`" + (f": `{detail}`" if detail else "") + "\n"
    if event.get("t") == "image":
        return f"- showed the screenshot `{str(event.get('name') or '')[:80]}`\n"
    if event.get("t") == "ship":
        return f"- shipped `{event.get('sha')}` to {event.get('repo')} {event.get('branch')}: {event.get('subject')}\n"
    return ""


class ConversationLog:
    def __init__(self, root: str = ROOT, repo: str = "", projects: Path = CLAUDE_PROJECTS, clock=time.time, namer=None):
        self.root, self.repo, self.projects, self.clock = Path(root), repo, projects, clock
        # namer(first message) -> "noun-verb-adjective-noun" (chat_names.py via Skippy Core); asked once per conversation, on a
        # thread, so a slow or absent Core never holds up a run. Until it answers the name is chat_names.fallback().
        self.namer = namer
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
            md = self.path(run.conv, run.created)
            self._append(md, f"\n---\n\n## The Captain, {_stamp(run.created)}\n\n{str(run.message).strip()}\n\n"
                             f"### Skippy\n\n")
            new = self._turn_started(md, run)
        if new and self.namer:
            threading.Thread(target=self._name, args=(md, str(run.message)), daemon=True).start()

    def _name(self, md: Path, message: str) -> None:
        """Name a new conversation (chat_names.py): the name replaces the fallback the index started with."""
        try:
            name = self.namer(message)
        except Exception as exc:  # noqa: BLE001 — the fallback name stays
            self._warn(f"couldn't name a conversation: {exc}")
            return
        if not chat_names.valid(name):
            return
        with self._lock:
            path = self._index(md)
            data = self._load_index(path)
            if data is not None:
                data["title"] = name
                self._save_index(path, data)

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
            self._turn_finished(path, run)
        if session:
            # Claude Code files the transcript under the directory it ran in: this run's project clone.
            self.copy_session(path, session, getattr(run, "clone", "") or self.repo)

    def copy_session(self, md_path: Path, session: str, repo: str = "") -> Path | None:
        session = _SAFE.sub("", session)
        src = claude_project_dir(repo or self.repo, self.projects) / f"{session}.jsonl"
        dst = md_path.with_name(f"{md_path.stem}.claude-{session}.jsonl")
        try:
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                return dst
        except OSError as exc:
            self._warn(f"couldn't copy {src}: {exc}")
        return None

    # ── the turns index: what the chat's Conversations list and a re-opened conversation show ──────────
    # Why a JSON file beside the Markdown, and not the Markdown itself: the Markdown is for the Captain to read, and
    # parsing it back would break on the first message that contains a "## " line. The bridge's run records can't serve
    # either: they are forgotten an hour after a run ends. This file is written twice per message (when it arrives and
    # when it ends), atomically, from the events the run already cleaned (tool lines carry a command or a path, never
    # output), so it holds nothing the chat didn't already show.
    @staticmethod
    def _index(md: Path) -> Path:
        return md.with_name(md.stem + INDEX_SUFFIX)

    def _load_index(self, path: Path) -> dict | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("turns"), list) or not isinstance(data.get("conv"), str):
            return None
        return data

    def _save_index(self, path: Path, data: dict) -> None:
        data["turns"] = data["turns"][-MAX_TURNS:]
        tmp = path.with_name(path.name + ".tmp")
        for attempt in range(4):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, path)          # atomic: the listing never reads half a file
                return
            except OSError as exc:
                if attempt == 3:
                    self._warn(f"couldn't write {path}: {exc}")
                    return
                time.sleep(0.05 * (attempt + 1))   # Windows: "Access is denied" while an indexer holds the file

    def _turn_started(self, md: Path, run) -> bool:
        """Record a turn's start in the index; True when this is the conversation's first turn (a new index)."""
        path = self._index(md)
        who = str(getattr(run, "who", "") or "")
        data = self._load_index(path)
        new = data is None
        data = data or {"conv": run.conv, "project": getattr(run, "project", "") or DEFAULT_PROJECT,
                        "who": who, "title": chat_names.fallback(run.message), "started": run.created, "turns": []}
        if not data.get("who") and who:
            data["who"] = who
        data["last"] = run.created
        data["turns"] = [t for t in data["turns"] if isinstance(t, dict) and t.get("task") != run.task]
        data["turns"].append({"task": run.task, "at": run.created, "message": str(run.message).strip()[:MAX_MESSAGE],
                              "events": [], "state": "running", "error": None})
        self._save_index(path, data)
        return new

    def _turn_finished(self, md: Path, run) -> None:
        path = self._index(md)
        data = self._load_index(path)
        if data is None:
            return
        for turn in data["turns"]:
            if isinstance(turn, dict) and turn.get("task") == run.task:
                turn["events"] = clean_events(list(run.events))
                turn["state"] = "done" if run.ok else "error"
                turn["error"] = None if run.ok else str(run.error or "")[:500]
                turn["ended"] = run.finished_at or self.clock()
                data["last"] = turn["ended"]
                break
        else:
            return
        self._save_index(path, data)

    def conversations(self, limit: int = MAX_LISTED, live=lambda task: None) -> list[dict]:
        """The newest `limit` conversations (by last activity), summarised: what GET /workshop/conversations answers.
        `live(task)` is what the bridge itself holds of a run ({"state", "events", "error"}, or None): a turn the index
        still calls running takes the bridge's word (it may have ended a moment ago), and one the bridge no longer holds
        (it restarted mid-run) reads as an error, never as a chat that waits forever."""
        found = []
        for path in self.root.glob(f"*{INDEX_SUFFIX}"):
            data = self._load_index(path)
            if data is None or not _CONV_ID.fullmatch(data["conv"]):
                continue
            turns = [t for t in data["turns"] if isinstance(t, dict)]
            state = _turn_state(turns[-1], live) if turns else "empty"
            found.append({"conv": data["conv"], "project": _text(data.get("project"), 40) or DEFAULT_PROJECT,
                          "who": _text(data.get("who"), 200), "title": _text(data.get("title"), 90),
                          "started": _num(data.get("started")), "last": _num(data.get("last")) or _num(data.get("started")),
                          "turns": len(turns), "state": state})
        found.sort(key=lambda c: c["last"] or 0, reverse=True)
        return found[:limit]

    def conversation(self, conv: str, live=lambda task: None, budget: int = MAX_CONVERSATION_BYTES) -> dict | None:
        """One conversation's turns for display (GET /workshop/conversation/<conv>), newest turns kept when it is bigger
        than `budget` bytes of JSON (a reply must fit the portal's 1 MB request limit with room to spare)."""
        if not _CONV_ID.fullmatch(conv or ""):
            return None
        for path in self.root.glob(f"*_{conv}{INDEX_SUFFIX}"):
            data = self._load_index(path)
            if data is None or data["conv"] != conv:
                continue
            turns = []
            for t in (t for t in data["turns"] if isinstance(t, dict)):
                events, error = t.get("events") if isinstance(t.get("events"), list) else [], t.get("error")
                held = live(str(t.get("task") or "")) if t.get("state") == "running" else None
                if held:                                   # still on the bridge: its events are the current ones
                    events, error = held.get("events") or [], held.get("error")
                turns.append({"task": _text(t.get("task"), 32), "at": _num(t.get("at")),
                              "message": _text(t.get("message"), MAX_MESSAGE), "events": clean_events(events),
                              "state": _turn_state(t, live), "error": _text(error, 500) or None})
            out = {"conv": conv, "project": _text(data.get("project"), 40) or DEFAULT_PROJECT,
                   "who": _text(data.get("who"), 200), "title": _text(data.get("title"), 90), "turns": turns,
                   "trimmed": 0}
            while len(json.dumps(out)) > budget and len(out["turns"]) > 1:
                out["turns"].pop(0)
                out["trimmed"] += 1
            return out
        return None


def _text(value, limit: int) -> str:
    return str(value)[:limit] if isinstance(value, str) else ""


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _turn_state(turn: dict, live) -> str:
    state = turn.get("state")
    if state == "running":
        held = live(str(turn.get("task") or ""))
        # The bridge's word when it still holds the run; none means it restarted mid-run (or forgot it): over, not waiting.
        state = held.get("state") if isinstance(held, dict) else "error"
    return state if state in ("running", "done", "error") else "error"


def clean_events(events: list) -> list[dict]:
    """The events a turn keeps: text and tool lines only, cut to size, the last MAX_TURN_EVENTS."""
    out = []
    for e in events[-MAX_TURN_EVENTS:]:
        if not isinstance(e, dict):
            continue
        if e.get("t") == "text" and isinstance(e.get("text"), str):
            out.append({"t": "text", "text": e["text"][:MAX_EVENT_TEXT]})
        elif e.get("t") == "tool" and isinstance(e.get("name"), str):
            out.append({"t": "tool", "name": e["name"][:40], "detail": str(e.get("detail") or "")[:(1200 if e["name"] == "TodoWrite" else 200)]})
        elif ship_event(e):
            out.append(ship_event(e))
        elif (e.get("t") == "picture" and isinstance(e.get("id"), str) and re.fullmatch(r"[0-9a-f]{16}", e["id"])
              and isinstance(e.get("prompt"), str)):
            out.append({"t": "picture", "id": e["id"], "prompt": e["prompt"][:900]})    # a picture asked for: its card finds it by id
        elif e.get("t") == "image":            # the picture itself stays out of the index: a line saying it was there
            out.append({"t": "text", "text": f"(a screenshot was shown here: {str(e.get('name') or '')[:80]})"})
    return out


_SHIP_SHA = re.compile(r"[0-9a-f]{40}")
_SHIP_REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
_SHIP_PROJECT = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_SHIP_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,100}")


def ship_event(e) -> dict | None:
    """A `ship` event (the run pushed this commit), exactly in its shape, or None."""
    if not isinstance(e, dict) or e.get("t") != "ship":
        return None
    fields = {k: e.get(k) for k in ("project", "repo", "branch", "sha", "subject", "workflow")}
    if not all(isinstance(v, str) for v in fields.values()):
        return None
    if not (_SHIP_PROJECT.fullmatch(fields["project"]) and _SHIP_REPO.fullmatch(fields["repo"])
            and _SHIP_BRANCH.fullmatch(fields["branch"]) and _SHIP_SHA.fullmatch(fields["sha"])
            and fields["workflow"] in ("", "deploy.yml", "deploy.yaml")):
        return None
    return {"t": "ship", **fields, "subject": " ".join(fields["subject"].split())[:200]}


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
