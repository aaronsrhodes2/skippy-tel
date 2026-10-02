"""The archive on disk (a Google Drive folder, synced by Drive for Desktop).

Layout (see templates/README.md for the human-facing version):
  README.md
  inbox/                                   raw exports dropped by the Captain
  conversations/<source>/<yyyy-mm>/<yyyy-mm-dd>-<slug>-<id8>.md
  topics/<topic>.md                        generated
  index.md                                 generated
  .skippy/ledger.json                      source:id → {path, updated}; makes re-import a no-op

Front matter is a restricted YAML subset: one `key: <json value>` per line,
so it stays valid YAML for any other tool while needing no YAML dependency.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from memory.schema import Conversation

TEMPLATES = Path(__file__).parent / "templates"

_SPEAKERS = {"claude": "Claude", "claude-code": "Claude", "chatgpt": "ChatGPT", "grok": "Grok", "gemini": "Gemini"}
_FIELDS = ("source", "id", "title", "device", "started", "updated",
           "messages", "topics", "summary", "url")


def slugify(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "untitled"


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else ""


# ── front matter ──────────────────────────────────────────────────────────────

def read_front_matter(path: Path) -> dict:
    meta: dict = {}
    with path.open(encoding="utf-8") as fh:
        if fh.readline().rstrip("\n") != "---":
            return meta
        for line in fh:
            line = line.rstrip("\n")
            if line == "---":
                break
            key, sep, raw = line.partition(": ")
            if sep:
                try:
                    meta[key] = json.loads(raw)
                except json.JSONDecodeError:
                    meta[key] = raw
    return meta


def update_front_matter(path: Path, **changes) -> None:
    text = path.read_text(encoding="utf-8")
    _, head, body = text.split("---\n", 2)
    meta = {}
    for line in head.splitlines():
        key, _, raw = line.partition(": ")
        meta[key] = json.loads(raw)
    meta.update(changes)
    path.write_text(_front_matter(meta) + body, encoding="utf-8")


def _front_matter(meta: dict) -> str:
    lines = [f"{k}: {json.dumps(meta[k], ensure_ascii=False)}" for k in _FIELDS if k in meta]
    return "---\n" + "\n".join(lines) + "\n---\n"


# ── rendering ─────────────────────────────────────────────────────────────────

def render(conv: Conversation, summary: str = "") -> str:
    meta = {
        "source": conv.source,
        "id": conv.id,
        "title": conv.title,
        "device": conv.device,
        "started": conv.started.isoformat(),
        "updated": conv.updated.isoformat(),
        "messages": len(conv.messages),
        "topics": conv.topics,
        "summary": summary,
        "url": conv.url,
    }
    assistant = _SPEAKERS.get(conv.source, conv.source.title())
    out = [_front_matter(meta), f"\n# {conv.title}\n"]
    for msg in conv.messages:
        who = {"user": "Captain", "assistant": assistant}.get(msg.role, msg.role.title())
        stamp = f" · {_fmt(msg.created)}" if msg.created else ""
        out.append(f"\n**{who}**{stamp}\n")
        if msg.attachments:
            out.append("\n_Attached: " + ", ".join(msg.attachments) + "_\n")
        if msg.text:
            out.append("\n" + msg.text + "\n")
    return "".join(out)


# ── archive ───────────────────────────────────────────────────────────────────

@dataclass
class IngestResult:
    new: int = 0
    updated: int = 0
    skipped: int = 0


class Archive:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._ledger_path = self.root / ".skippy" / "ledger.json"
        self.ledger: dict[str, dict] = {}
        if self._ledger_path.exists():
            self.ledger = json.loads(self._ledger_path.read_text(encoding="utf-8"))

    def init(self) -> None:
        for sub in ("inbox", "claude-code", "conversations", "topics", ".skippy"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        readme = self.root / "README.md"
        if not readme.exists():
            shutil.copyfile(TEMPLATES / "README.md", readme)

    def save_ledger(self) -> None:
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._ledger_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.ledger, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self._ledger_path)

    def _path_for(self, conv: Conversation) -> Path:
        # The id comes from an export (untrusted text): only letters, digits, - and _ reach the file name.
        name = f"{conv.started:%Y-%m-%d}-{slugify(conv.title)}-{re.sub(r'[^A-Za-z0-9_-]', '', conv.id)[:8] or 'x'}.md"
        return Path("conversations") / conv.source / f"{conv.started:%Y-%m}" / name

    def write(self, conv: Conversation) -> str:
        """Write one conversation. Returns "new", "updated" or "skipped".

        A conversation the ledger has already seen is rewritten only when the
        source says it changed since (it was continued); tags and summary from
        an earlier distill pass are carried over.
        """
        entry = self.ledger.get(conv.key)
        updated = conv.updated.isoformat()
        if entry and datetime.fromisoformat(entry["updated"]) >= conv.updated:
            return "skipped"

        rel = Path(entry["path"]) if entry else self._path_for(conv)
        path = self.root / rel
        summary = ""
        if entry and path.exists():
            old = read_front_matter(path)
            conv.topics = conv.topics or old.get("topics", [])
            summary = old.get("summary", "")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(conv, summary), encoding="utf-8")
        self.ledger[conv.key] = {"path": rel.as_posix(), "updated": updated}
        return "updated" if entry else "new"

    def ingest(self, conversations) -> IngestResult:
        result = IngestResult()
        for conv in conversations:
            outcome = self.write(conv)
            setattr(result, outcome, getattr(result, outcome) + 1)
        self.save_ledger()
        return result

    def entries(self) -> list[tuple[Path, dict]]:
        """(relative path, front matter) for every archived conversation."""
        out = []
        for entry in self.ledger.values():
            path = self.root / entry["path"]
            if path.exists():
                out.append((Path(entry["path"]), read_front_matter(path)))
        return out
