"""The Captain's own text, cut into passages a search can find and cite.

Three sources, each read where it already lives on skippy-pc (nothing is copied anywhere):

  * ``agent``:  the private Skippy's saved conversations, ``D:/Aaron/conversations/private-skippy``. A ``<conv>.turns.json``
    (one entry per turn: the Captain's message and what Skippy said and ran) is preferred; a ``.md`` whose conversation has
    no turns file is read as Markdown (``## The Captain, <when>`` / ``### Skippy``).
  * ``memory``: the Skippy Memory archive (``G:/My Drive/Skippy Memory/conversations/**.md``, written by memory/sync.py):
    every AI conversation the Captain saved, front matter then ``**Captain** · <when>`` / ``**Claude** · <when>`` blocks.
  * ``mentat``: Mentat's research log (``SkippyTel/state/mentat-research/<YYYY-MM>.jsonl``, mentat/research.py): one
    passage per request, the question (or tool and arguments) and what Mentat answered.

A passage is one turn (the Captain's words and the reply), cut into windows of about WINDOW characters when it is long,
each window carrying its document's title so it reads on its own. Its ``key`` is a hash of its source, place and text, so
an unchanged passage keeps its key (and its vector) when the file around it grows.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

WINDOW = 1500          # characters a passage holds at most (about 350 tokens: well inside both models' windows)
OVERLAP = 200          # characters two windows of one long turn share, so a sentence cut in two is whole in one of them
MIN_CHARS = 40         # shorter turns ("ok", "proceed") carry no meaning worth an index row
TOOL_DETAIL = 160      # a tool call shows its name and this much of its argument: what was run, not its output


@dataclass
class Passage:
    source: str                    # "agent" | "memory" | "mentat"
    doc: str                       # the file it came from (path relative to its source root)
    title: str
    at: str                        # when (ISO, UTC, to the minute) or ""
    anchor: str                    # where in the document: "turn 3", "line 120"
    text: str
    key: str = field(default="")

    def __post_init__(self):
        if not self.key:
            h = hashlib.sha256(f"{self.source}\0{self.doc}\0{self.anchor}\0{self.text}".encode("utf-8", "replace"))
            self.key = h.hexdigest()[:24]

    def embed_text(self) -> str:
        """What the models read: the title first, so a window from the middle of a long turn still says what it is about."""
        return f"{self.title}\n{self.text}" if self.title else self.text


def _iso(ts) -> str:
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M")
    if isinstance(ts, str):
        return ts.replace(" ", "T")[:16]
    return ""


def windows(text: str, size: int = WINDOW, overlap: int = OVERLAP) -> list[str]:
    """Cut text into windows of at most `size` characters, at a paragraph, line or sentence end where there is one."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind("\n\n", start + size // 2, end), text.rfind("\n", start + size // 2, end),
                      text.rfind(". ", start + size // 2, end))
            if cut > start:
                end = cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [w for w in out if w]


def _passages(source: str, doc: str, title: str, at: str, anchor: str, text: str) -> list[Passage]:
    if len(text.strip()) < MIN_CHARS:
        return []
    parts = windows(text)
    return [Passage(source, doc, title, at, anchor if len(parts) == 1 else f"{anchor}.{i + 1}", part)
            for i, part in enumerate(parts)]


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", str(title or "")).strip()[:160]


# ── the agent's conversations ──────────────────────────────────────────────────

def _turn_text(turn: dict) -> str:
    lines = [f"Captain: {turn.get('message') or ''}".rstrip()]
    said, tools = [], []
    for ev in turn.get("events") or []:
        if ev.get("t") == "text" and ev.get("text"):
            said.append(str(ev["text"]))
        elif ev.get("t") == "tool" and ev.get("name"):
            tools.append(f"{ev['name']}: {str(ev.get('detail') or '')[:TOOL_DETAIL]}")
    if tools:
        lines.append("Skippy ran: " + "; ".join(tools[:12]) + (" ..." if len(tools) > 12 else ""))
    if said:
        lines.append("Skippy: " + "\n".join(said))
    return "\n".join(lines)


def agent_turns(path: Path, rel: str) -> list[Passage]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    title = _clean_title(d.get("title"))
    out = []
    for i, turn in enumerate(d.get("turns") or []):
        if isinstance(turn, dict):
            out += _passages("agent", rel, title, _iso(turn.get("at")), f"turn {i + 1}", _turn_text(turn))
    return out


_AGENT_MD_TURN = re.compile(r"^## The Captain, (\S+ \S+)\s*$", re.M)


def agent_markdown(path: Path, rel: str) -> list[Passage]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    title = ""
    parts = _AGENT_MD_TURN.split(text)        # [head, when1, body1, when2, body2, ...]
    out = []
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1].replace("### Skippy", "Skippy:").strip()
        body = re.sub(r"^_\d{4}-\d\d-\d\d [\d:]+, \w+_\s*$", "", body, flags=re.M)
        if not title:
            title = _clean_title(body.split("\n", 1)[0])
        out += _passages("agent", rel, title, _iso(parts[i]), f"turn {i // 2 + 1}", "Captain: " + body)
    return out


def _conv_id(name: str) -> str:
    """`2026-10-10_<conv>.turns.json` and `2026-10-10_<conv>.claude-<session>.md` share the `<conv>`."""
    return name.split("_", 1)[-1].split(".", 1)[0]


def agent_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    turns = sorted(root.glob("*.turns.json"))
    have = {_conv_id(p.name) for p in turns}
    md = [p for p in sorted(root.glob("*.md")) if _conv_id(p.name) not in have]
    return turns + md


def read_agent(path: Path, rel: str) -> list[Passage]:
    return agent_turns(path, rel) if path.name.endswith(".turns.json") else agent_markdown(path, rel)


# ── the Skippy Memory archive ──────────────────────────────────────────────────

_MEM_TURN = re.compile(r"^\*\*(.+?)\*\* · (\d{4}-\d\d-\d\d \d\d:\d\d)(?: UTC)?\s*$", re.M)


def read_memory(path: Path, rel: str) -> list[Passage]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return memory_text(text, rel)


def memory_text(text: str, rel: str, title: str = "") -> list[Passage]:
    """An archive conversation's Markdown (memory/store.py render(), front matter optional) as passages."""
    if text.startswith("---\n") and text.count("---\n") >= 2:
        _, head, text = text.split("---\n", 2)
        for line in head.splitlines():
            if line.startswith("title: "):
                try:
                    title = _clean_title(json.loads(line[7:]))
                except ValueError:
                    title = _clean_title(line[7:])
    parts = _MEM_TURN.split(text)             # [head, who1, when1, body1, ...]
    out, turn, n = [], [], 0
    when = ""

    def flush():
        nonlocal turn, n
        if turn:
            n += 1
            out.extend(_passages("memory", rel, title, when, f"turn {n}", "\n".join(turn)))
            turn = []

    for i in range(1, len(parts) - 2, 3):
        who, at, body = parts[i], parts[i + 1], parts[i + 2].strip()
        if who.lower().startswith("captain"):
            flush()
            when = _iso(at)
        turn.append(f"{who}: {body}")
    flush()
    return out


def memory_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.md")) if root.is_dir() else []


def session_files(root: Path) -> list[Path]:
    """Claude Code's transcripts (~/.claude/projects/<project>/<session>.jsonl), the archive's claude-code source. Subagent
    transcripts are all side-chain, which the archive leaves out too."""
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.jsonl") if "subagents" not in p.parts)


def read_session(path: Path, rel: str) -> list[Passage]:
    """One transcript exactly as the Skippy Memory archive renders it (memory/parsers/claude_code.py, memory/store.py), so the
    index reads the same words the archive keeps without needing the Drive folder (Docker can't see G:)."""
    from memory.parsers import claude_code
    from memory.store import render
    try:
        conv = claude_code.parse_session(path)
    except (OSError, ValueError):
        return []
    if conv is None:
        return []
    return memory_text(render(conv), rel, _clean_title(conv.title))


# ── Mentat's research log ──────────────────────────────────────────────────────

def _short(value, limit: int) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + " ..."


def _mentat_answer(resp) -> str:
    if not isinstance(resp, dict):
        return _short(resp, 600)
    for k in ("answer", "text", "summary", "message"):
        if isinstance(resp.get(k), str) and resp[k].strip():
            return resp[k][:900]
    res = resp.get("result")
    if isinstance(res, dict):
        bits = [f"{k}={_short(v, 80)}" for k, v in res.items() if k not in ("steps", "provenance", "sources")][:12]
        return "; ".join(bits)
    if res is not None:
        return _short(res, 600)
    if resp.get("error"):
        return "error: " + _short(resp["error"], 300)
    return _short(resp, 600)


def read_mentat(path: Path, rel: str) -> list[Passage]:
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for n, line in enumerate(lines, 1):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        req = rec.get("request") or {}
        if isinstance(req, dict) and req.get("question"):
            asked = str(req["question"])
            if req.get("given"):
                asked += " (given " + _short(req["given"], 200) + ")"
        else:
            asked = f"{rec.get('tool') or rec.get('endpoint') or 'request'} " + _short(req, 300)
        title = f"Mentat {rec.get('tool') or rec.get('endpoint') or ''}".strip()
        body = f"Asked Mentat: {asked}\nMentat answered: {_mentat_answer(rec.get('response'))}"
        out += _passages("mentat", rel, title, _iso(rec.get("ts")), f"line {n}", body)
    return out


def mentat_files(root: Path) -> list[Path]:
    return sorted(root.glob("*.jsonl")) if root.is_dir() else []


# ── all three ──────────────────────────────────────────────────────────────────

SESSIONS = ("memory", session_files, read_session)  # the container's way to the same archive text: its source transcripts

SOURCES = {
    "agent": (agent_files, read_agent),
    "memory": (memory_files, read_memory),          # the archive folder itself (on the host: G:/My Drive/Skippy Memory)
    "mentat": (mentat_files, read_mentat),
}
