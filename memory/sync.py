"""Keeping Skippy Memory current without anyone running a command, and reading it back for "Continue with Skippy".

Run on skippy-pc by the Claude bridge (services/claude-bridge/bridge.py) every SYNC_EVERY_S:
  1. this PC's Claude Code sessions (~/.claude/projects: the Captain's own sessions and the private Skippy's) that changed
     since the last pass, straight to Markdown. Only the changed .jsonl files are read; the raw history is never copied
     to Drive (it is hundreds of MB).
  2. every export dropped in <archive>/inbox/ (Claude, Grok, ...). An export that imported cleanly is MOVED to
     inbox/imported/ (never deleted), so the next pass doesn't parse it again; one that failed stays, with the reason in
     .skippy/sync.json.
  3. index.md and topics/ rebuilt when anything changed.

`listing()` and `read()` are what the bridge serves to the portal (through the home reporter): titles, sources and
dates for the list; one conversation's text, newest turns kept when it's long, for the chat to continue from.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from memory import index, parsers
from memory.parsers import claude_code
from memory.store import Archive

SYNC_EVERY_S = 30 * 60
LIST_MAX = 300
READ_MAX_CHARS = 60_000          # what the chat sends the model as "the earlier conversation": about 15k tokens


def default_root() -> Path:
    return Path(os.environ.get("SKIPPY_MEMORY_DIR") or "G:/My Drive/Skippy Memory")


def _state_path(archive: Archive) -> Path:
    return archive.root / ".skippy" / "sync.json"


def load_state(archive: Archive) -> dict:
    try:
        return json.loads(_state_path(archive).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(archive: Archive, state: dict) -> None:
    path = _state_path(archive)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def sync(archive: Archive, claude_home: Path | None, device: str, clock=time.time) -> dict:
    """One pass. Returns what it did: {"sessions": n, "exports": {name: "x new, y updated" | "error: ..."}, "at": t}."""
    archive.init()
    state = load_state(archive)
    report: dict = {"sessions": 0, "exports": {}, "at": int(clock())}
    changed = False

    if claude_home and claude_home.is_dir():
        mark = float(state.get("claude_mtime") or 0)
        newest = mark
        convs = []
        for path in claude_home.rglob("*.jsonl"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime <= mark:
                continue
            newest = max(newest, mtime)
            conv = claude_code.parse_session(path, device=device)
            if conv is not None:
                convs.append(conv)
        if convs:
            result = archive.ingest(convs)
            report["sessions"] = result.new + result.updated
            changed = changed or bool(result.new or result.updated)
        state["claude_mtime"] = newest

    inbox = archive.root / "inbox"
    done = inbox / "imported"
    errors = {}
    for path in sorted(p for p in inbox.iterdir() if p.name != "imported"
                       and (p.suffix.lower() in (".zip", ".json") or p.is_dir())):
        try:
            result = archive.ingest(parsers.load(path))
        except (ValueError, OSError) as exc:
            errors[path.name] = str(exc)[:300]
            report["exports"][path.name] = f"error: {exc}"[:300]
            continue
        report["exports"][path.name] = f"{result.new} new, {result.updated} updated"
        changed = changed or bool(result.new or result.updated)
        done.mkdir(exist_ok=True)
        target = done / path.name
        if target.exists():
            target = done / f"{path.stem}-{int(clock())}{path.suffix}"
        shutil.move(str(path), str(target))
    state["errors"] = errors

    if changed or not (archive.root / "index.md").exists():
        index.rebuild(archive)
    state["last"] = report
    _save_state(archive, state)
    return report


def listing(archive: Archive, limit: int = LIST_MAX) -> dict:
    """The newest conversations, every source: what the chat's "Past conversations" list shows. No text."""
    rows = []
    for rel, meta in archive.entries():
        rows.append({"key": f"{meta.get('source', '')}:{meta.get('id', '')}", "source": meta.get("source", ""),
                     "title": str(meta.get("title") or "Untitled")[:120], "updated": str(meta.get("updated") or "")[:19],
                     "messages": meta.get("messages") or 0, "device": meta.get("device") or "",
                     "summary": str(meta.get("summary") or "")[:300]})
    rows.sort(key=lambda r: r["updated"], reverse=True)
    state = load_state(archive)
    return {"conversations": rows[:limit], "total": len(rows), "last_sync": (state.get("last") or {}).get("at"),
            "errors": state.get("errors") or {}}


def read(archive: Archive, key: str, max_chars: int = READ_MAX_CHARS) -> dict | None:
    """One conversation's Markdown body (front matter removed), cut from the START when long so the newest turns, the
    ones a continuation needs, are kept. None if the key isn't in the archive."""
    entry = archive.ledger.get(key)
    if not entry:
        return None
    path = archive.root / entry["path"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    from memory.store import read_front_matter
    meta = read_front_matter(path)
    body = text.split("---\n", 2)[2] if text.startswith("---\n") and text.count("---\n") >= 2 else text
    cut = len(body) > max_chars
    if cut:
        body = "…(earlier turns left out)…\n" + body[-max_chars:]
    return {"key": key, "source": meta.get("source", ""), "title": meta.get("title", ""),
            "updated": str(meta.get("updated") or "")[:19], "text": body.strip(), "cut": cut}
