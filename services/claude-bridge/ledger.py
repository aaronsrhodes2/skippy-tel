"""A recency-pruned memory of a long run (the Captain, 2026-11-13: "removing the phrases that haven't been referenced in a while instead of
compressing, so each turn is getting the best of the last turn, plus a reverification of the goal").

A fresh segment (sdk_engine.drive, run_state.handoff) must not start from a summary the model wrote of itself: summaries drift, and a
mistaken line in one is repeated by every later one. Instead the engine keeps PHRASES, the run's own words and tool calls as they were, each with the
event at which the work last REFERRED to it (it names the same file, identifier or number again). When a segment ends, the phrases still being
referred to are carried over word for word, newest-referenced first, until the budget is full; the ones nothing has touched for a while are
dropped, not compressed. They stay on disk (`.purple-out/ledger.jsonl`) so the model can grep for one it turns out to need.

No model is involved: keys and references are plain text matching, so the ledger can't hallucinate."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MIN_PHRASE = 24                 # shorter lines carry nothing worth keeping
MAX_PHRASE = 260
STALE_EVENTS = 80               # not referred to for this many events (messages and tool calls): dropped from the next segment
FRESH_EVENTS = 25               # ...but anything younger than this is always kept: it has had no chance to be referred to yet
LEDGER_CHARS = 6000             # what a handoff carries (about 1,700 tokens)
ARCHIVE = Path(".purple-out") / "ledger.jsonl"

_PATH = re.compile(r"[\w./\\-]*[\w-]\.[A-Za-z]{1,5}\b|[\w-]+[/\\][\w./\\-]+")
_IDENT = re.compile(r"\b(?:[a-z]+_[a-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]+|[0-9a-f]{7,40}|\d{3,})\b")
_WORD = re.compile(r"\b[A-Za-z][A-Za-z-]{6,}\b")
_COMMON = frozenset("however because through another already without should please something everything anything between against important following".split())


def keys_of(text: str) -> set[str]:
    """The distinctive things a phrase names: paths, identifiers, hashes, numbers, and the longer words (lowercased)."""
    found = {m.group(0).lower().replace("\\", "/") for m in _PATH.finditer(text)}
    found |= {m.group(0).lower() for m in _IDENT.finditer(text)}
    found |= {m.group(0).lower() for m in _WORD.finditer(text) if m.group(0).lower() not in _COMMON}
    return found


def phrases_of(text: str) -> list[str]:
    """A message cut into its phrases: one per sentence or line, too-short ones left out."""
    out = []
    for line in re.split(r"(?<=[.!?])\s+|\n+", text):
        line = line.strip(" \t-*>#`")
        if len(line) >= MIN_PHRASE:
            out.append(line[:MAX_PHRASE])
    return out


@dataclass
class Entry:
    kind: str                   # said | tool | fail
    text: str
    keys: set
    born: int
    last_ref: int
    refs: int = 0

    def line(self) -> str:
        return f"[{self.kind}] {self.text}"


@dataclass
class Ledger:
    clock: int = 0              # one tick per message or tool call
    entries: list = field(default_factory=list)
    seen: set = field(default_factory=set)

    def observe(self, text: str) -> None:
        """The work just said or did `text`: every phrase it names again counts as referred to now."""
        self.clock += 1
        now = keys_of(text)
        if not now:
            return
        for e in self.entries:
            hit = e.keys & now
            if len(hit) >= 2 or any(("/" in k or "." in k or "_" in k or k.isdigit()) for k in hit):
                e.last_ref, e.refs = self.clock, e.refs + 1

    def add(self, kind: str, text: str) -> None:
        text = text.strip()
        if len(text) < MIN_PHRASE and kind != "fail" or text in self.seen:
            return
        self.seen.add(text)
        self.entries.append(Entry(kind, text[:MAX_PHRASE], keys_of(text), self.clock, self.clock))

    def say(self, text: str) -> None:
        self.observe(text)
        for p in phrases_of(text):
            self.add("said", p)

    def do(self, name: str, detail: str, failed: bool = False) -> None:
        line = f"{name}: {detail}" if detail else name
        self.observe(line)
        self.add("fail" if failed else "tool", line)

    def alive(self) -> list[Entry]:
        """What is still being referred to (or too new to say), newest-referenced first."""
        keep = [e for e in self.entries if self.clock - e.last_ref <= STALE_EVENTS or self.clock - e.born <= FRESH_EVENTS]
        return sorted(keep, key=lambda e: (e.last_ref, e.refs, e.born), reverse=True)

    def carry(self, budget: int = LEDGER_CHARS) -> list[str]:
        """The phrases to open the next segment with, word for word, within `budget` characters; chronological order reads best."""
        chosen, used = [], 0
        for e in self.alive():
            if used + len(e.text) + 8 > budget:
                continue
            chosen.append(e)
            used += len(e.text) + 8
        return [e.line() for e in sorted(chosen, key=lambda e: e.born)]

    def dropped(self) -> list[Entry]:
        live = {id(e) for e in self.alive()}
        return [e for e in self.entries if id(e) not in live]

    def archive(self, clone: str) -> str:
        """Every phrase so far, kept or dropped, to the clone's scratch file (best effort); the path, or '' when it can't be written."""
        path = Path(clone) / ARCHIVE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                for e in self.entries:
                    f.write(json.dumps({"kind": e.kind, "text": e.text, "born": e.born, "last_ref": e.last_ref, "refs": e.refs}) + "\n")
            return str(ARCHIVE).replace("\\", "/")
        except OSError:
            return ""
