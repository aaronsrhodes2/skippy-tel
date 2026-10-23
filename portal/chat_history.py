"""Each person's chats, kept on the portal (the Captain's design, 2026-10-23): a guest's for 30 days, the Captain's forever. One
private file per person, chat-history/<owner>.json, in the Tabletop's bucket (TABLETOP_BUCKET, never public; the portal's runtime
identity is the only reader). <owner> is the same one-way id of the address that keys the page's own storage, always worked out
here from the signed-in session, never taken from the page, so nobody can read or write another person's file.

A guest's chat untouched for 30 days is no longer shown or sent, but it is not thrown away unread: sweep() (run about hourly,
nudged by the home reporter's check-ins) has Skippy Core ANALYSE each one first (what went right, what went wrong, bugs,
improvements), records that insight (chat-insights/<YYYY-MM>.json in the same bucket, and home to skippy-pc), and only then
deletes the chat. If Core can't be reached the chat waits for the next sweep. The bucket's lifecycle rule (infra/bootstrap)
deletes a file no one has written for 60 days, a backstop far behind the sweep. Without a bucket (a local run, tests) it all
lives in memory (MemoryStore); the page also keeps its own copy, so a chat survives a portal without storage.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta, timezone

import chat_names
import tabletop

KEEP_DAYS = 30
MAX_CHATS = 40                  # a guest's newest 40, as the page's own list
MAX_CHATS_FOREVER = 1000        # the Captain's: kept forever, so only a sanity cap
MAX_CHAT_BYTES = 256 * 1024     # one chat's history, as JSON
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES_FOREVER = 24 * 1024 * 1024
PREFIX = "chat-history/"
INSIGHTS = "chat-insights/"
_ID = re.compile(r"[a-z0-9]{4,24}")
_OWNER_FILE = re.compile(r"chat-history/([0-9a-f]{16})\.json")

ANALYSE = ("You review a finished conversation between a guest of the Maker Portal and Skippy, its assistant, before it is deleted. "
           "Say what it teaches the people who build Skippy. Answer with JSON only, exactly this shape: "
           '{"right": [], "wrong": [], "bugs": [], "improvements": []}, each a list of short plain sentences (empty when there is '
           "nothing): right = what Skippy did well; wrong = where it misunderstood, refused needlessly, made something up or was "
           "unhelpful; bugs = anything that looks broken (a tool that failed, a picture that never came, an error); improvements = "
           "what would have made it better. Never quote personal details.")


class Refused(ValueError):
    """Something the page sent that can't be kept (a bad id, a chat too big): a 400 with this message."""


def owner_id(email: str) -> str:
    """The one-way id of a person (also the key of the page's localStorage, templates/_chat.html OWNER)."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fresh(chat: dict, now: datetime) -> bool:
    try:
        when = datetime.fromisoformat(str(chat.get("updated", "")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return now - when <= timedelta(days=KEEP_DAYS)


def parse_insight(text: str) -> dict:
    """The analysis's JSON (its thinking and any words around it dropped); ValueError if there is none."""
    text = re.sub(r"<think>.*?</think>", " ", str(text or ""), flags=re.S | re.I)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON in the analysis")
    got = json.loads(text[start:end + 1])
    return {k: [str(x)[:300] for x in got.get(k, []) if str(x).strip()][:8] if isinstance(got.get(k), list) else []
            for k in ("right", "wrong", "bugs", "improvements")}


class History:
    def __init__(self, store=None, namer=None, forever=None):
        self.store = store if store is not None else tabletop.MemoryStore()
        # namer(first prompt) -> "noun-verb-adjective-noun" (chat_names.py, Skippy Core): a chat is named once, on its first save
        self.namer = namer
        # forever(email) -> True for the Captain: his chats never expire (app.py passes auth.is_admin)
        self.forever = forever or (lambda email: False)
        self._lock = threading.Lock()   # the portal runs one instance: a lock is enough to keep two saves from crossing

    # ── files ──
    def _path(self, email: str) -> str:
        return PREFIX + owner_id(email) + ".json"

    def _load(self, path: str) -> list[dict]:
        """Every chat in a file, expired ones included (they wait there for the sweep)."""
        raw = self.store.get(path)
        try:
            chats = json.loads(raw) if raw else []
        except ValueError:
            chats = []
        return [c for c in chats if isinstance(c, dict)] if isinstance(chats, list) else []

    def _store(self, path: str, chats: list[dict], forever: bool) -> None:
        now = _now()
        chats = sorted(chats, key=lambda c: c.get("updated", ""), reverse=True)
        if forever:
            chats, max_bytes = chats[:MAX_CHATS_FOREVER], MAX_FILE_BYTES_FOREVER
        else:                                                    # the newest 40 shown, plus the expired ones the sweep still owes
            chats, max_bytes = [c for c in chats if _fresh(c, now)][:MAX_CHATS] + [c for c in chats if not _fresh(c, now)], MAX_FILE_BYTES
        data = json.dumps(chats, separators=(",", ":")).encode()
        while len(data) > max_bytes and chats:                   # full: the oldest go first
            chats = chats[:-1]
            data = json.dumps(chats, separators=(",", ":")).encode()
        if chats:
            self.store.put(path, data, "application/json")
        else:
            self.store.delete(path)

    # ── the page's calls ──
    def chats(self, email: str) -> list[dict]:
        """This person's chats, newest first: a guest's from the last 30 days, all of the Captain's."""
        forever, now = self.forever(email), _now()
        with self._lock:
            chats = self._load(self._path(email))
        return sorted((c for c in chats if forever or _fresh(c, now)), key=lambda c: c.get("updated", ""), reverse=True)

    def save(self, email: str, chat_id: str, title: str, history) -> dict:
        """Keep (or replace) one chat. Only role/content turns are kept: whatever else the page sends is dropped."""
        if not _ID.fullmatch(str(chat_id or "")):
            raise Refused("That isn't a chat id.")
        if not isinstance(history, list) or not history:
            raise Refused("A chat needs at least one turn.")
        turns = [{"role": t["role"], "content": t["content"]} for t in history
                 if isinstance(t, dict) and t.get("role") in ("user", "assistant") and isinstance(t.get("content"), str)]
        if not turns:
            raise Refused("A chat needs at least one turn.")
        if len(json.dumps(turns)) > MAX_CHAT_BYTES:
            raise Refused("That chat is too long to keep on the portal (it stays in this browser).")
        path, forever = self._path(email), self.forever(email)
        with self._lock:
            had = next((c for c in self._load(path) if c.get("id") == chat_id), None)
        if had and chat_names.valid(had.get("title", "")):
            name = had["title"]                                  # named once: the page's title (its first words) never replaces it
        else:                                                    # outside the lock: Skippy Core takes a second or two
            first = next((t["content"] for t in turns if t["role"] == "user"), str(title or ""))
            name = (self.namer or chat_names.fallback)(first)
        chat = {"id": chat_id, "title": name, "updated": _now().strftime("%Y-%m-%dT%H:%M:%SZ"), "history": turns}
        with self._lock:
            self._store(path, [chat] + [c for c in self._load(path) if c.get("id") != chat_id], forever)
        return {"id": chat_id, "updated": chat["updated"], "title": name}

    def forget(self, email: str, chat_id: str) -> None:
        """The person's own bin: gone at once (a chat they delete themselves is not analysed)."""
        path = self._path(email)
        with self._lock:
            self._store(path, [c for c in self._load(path) if c.get("id") != chat_id], self.forever(email))

    # ── expiry: analyse, record, then delete ──
    def sweep(self, analyse, record, keep_owners=(), limit: int = 10) -> int:
        """Up to `limit` expired guest chats: analyse(chat) -> insight, record(owner, chat, insight), then delete it. Stops at
        the first failed analysis (Core away): those chats wait for the next sweep. `keep_owners` are the Captain's owner
        ids (never expire). Answers how many were done."""
        done, now = 0, _now()
        for path in sorted(self.store.list(PREFIX)):
            m = _OWNER_FILE.fullmatch(path)
            if not m or m.group(1) in keep_owners:
                continue
            with self._lock:
                stale = [c for c in self._load(path) if not _fresh(c, now)]
            for chat in stale:
                if done >= limit:
                    return done
                insight = analyse(chat)                          # raises when it can't: the chat stays for next time
                record(m.group(1), chat, insight)
                with self._lock:
                    self._store(path, [c for c in self._load(path) if c.get("id") != chat.get("id")], False)
                done += 1
        return done

    def record_insight(self, owner: str, chat: dict, insight: dict) -> dict:
        """Add one insight to this month's file (chat-insights/<YYYY-MM>.json): no names or addresses, the chat's own name,
        how long it ran, and what Skippy Core made of it."""
        item = {"at": _now().strftime("%Y-%m-%dT%H:%M:%SZ"), "owner": owner, "chat": str(chat.get("title") or "")[:60],
                "turns": len(chat.get("history") or []), "last": str(chat.get("updated") or "")[:20], **insight}
        path = INSIGHTS + _now().strftime("%Y-%m") + ".json"
        with self._lock:
            raw = self.store.get(path)
            try:
                items = json.loads(raw) if raw else []
            except ValueError:
                items = []
            items = (items if isinstance(items, list) else []) + [item]
            self.store.put(path, json.dumps(items, separators=(",", ":")).encode(), "application/json")
        return item


def analyse_with(ask):
    """analyse(chat) for sweep(): Skippy Core reads the conversation (cut to its last ~12k characters) and answers the insight."""
    def analyse(chat: dict) -> dict:
        lines = [f"{t['role'].upper()}: {t['content']}" for t in chat.get("history") or [] if isinstance(t, dict)]
        text = "\n\n".join(lines)[-12000:]
        return parse_insight(ask(ANALYSE, text + "\n\n/no_think"))
    return analyse


def from_env() -> History:
    return History(tabletop.store_from_env())
