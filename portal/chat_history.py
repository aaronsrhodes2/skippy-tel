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

Archive (the Captain's bedtime routine, 2026-10-31): once a night archive() zips every chat untouched for 14 days into its own
file, chat-archive/<owner>/<id>.zip (outside the chat-history/ lifecycle rule), and leaves a light stub in the person's file
({id, title, updated, turns, archived: true}, no history), so the list and the file stay small. The list shows the stub with its
name and date; opening it (restore()) puts the chat back in the file and deletes its zip. A guest's archived chat still expires
at 30 days: the sweep reads its zip for the analysis and deletes both.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import zipfile
from datetime import datetime, timedelta, timezone

import chat_names
import tabletop

KEEP_DAYS = 30
ARCHIVE_DAYS = 14               # a chat untouched this long is zipped, leaving a stub in the list
MAX_CHATS = 40                  # a guest's newest 40, as the page's own list
MAX_CHATS_FOREVER = 1000        # the Captain's: kept forever, so only a sanity cap
MAX_CHAT_BYTES = 256 * 1024     # one chat's history, as JSON (a guest's)
MAX_CHAT_BYTES_FOREVER = 2 * 1024 * 1024    # the Captain's: his long working chats must keep saving
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES_FOREVER = 24 * 1024 * 1024
PREFIX = "chat-history/"
INSIGHTS = "chat-insights/"
ARCHIVE = "chat-archive/"
_ID = re.compile(r"[a-z0-9]{4,24}")
_OWNER_FILE = re.compile(r"chat-history/([0-9a-f]{16})\.json")
_ARCHIVE_FILE = re.compile(r"chat-archive/([0-9a-f]{16})/([a-z0-9]{4,24})\.zip")

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


def _fresh(chat: dict, now: datetime, days: int = KEEP_DAYS) -> bool:
    try:
        when = datetime.fromisoformat(str(chat.get("updated", "")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return now - when <= timedelta(days=days)


def _zip(chat: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("chat.json", json.dumps(chat, separators=(",", ":")))
    return buf.getvalue()


def _unzip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        chat = json.loads(z.read("chat.json"))
    if not isinstance(chat, dict):
        raise ValueError("not a chat")
    return chat


def _stub(chat: dict) -> dict:
    """What stays in the list for an archived chat: its name, date and size, no history."""
    return {"id": chat["id"], "title": chat.get("title") or "chat", "updated": chat.get("updated", ""), "named": True,
            "turns": sum(1 for t in chat.get("history") or [] if isinstance(t, dict) and t.get("role") == "user"),
            "archived": True, **({"star": True} if chat.get("star") else {})}


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

    def _zip_path(self, owner: str, chat_id: str) -> str:
        return f"{ARCHIVE}{owner}/{chat_id}.zip"

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
        """Keep (or replace) one chat. Only role/content turns are kept (and a heard turn's language): whatever else the page sends is dropped."""
        if not _ID.fullmatch(str(chat_id or "")):
            raise Refused("That isn't a chat id.")
        if not isinstance(history, list) or not history:
            raise Refused("A chat needs at least one turn.")
        turns = [{"role": t["role"], "content": t["content"],
                  **({"lang": str(t.get("lang") or "")[:8]} if t["role"] == "heard" else {})} for t in history
                 if isinstance(t, dict) and t.get("role") in ("user", "assistant", "heard") and isinstance(t.get("content"), str)]
        if not any(t["role"] != "heard" for t in turns):
            raise Refused("A chat needs at least one turn.")
        path, forever = self._path(email), self.forever(email)
        if len(json.dumps(turns)) > (MAX_CHAT_BYTES_FOREVER if forever else MAX_CHAT_BYTES):
            raise Refused("That chat is too long to keep on the portal (it stays in this browser).")
        with self._lock:
            had = next((c for c in self._load(path) if c.get("id") == chat_id), None)
        if had and (had.get("named") or chat_names.valid(had.get("title", ""))):
            name = had["title"]                                  # named once (or renamed): the page's title never replaces it
        else:                                                    # outside the lock: Skippy Core takes a second or two
            first = next((t["content"] for t in turns if t["role"] == "user"), str(title or ""))
            name = (self.namer or chat_names.fallback)(first)
        chat = {"id": chat_id, "title": name, "updated": _now().strftime("%Y-%m-%dT%H:%M:%SZ"), "history": turns,
                "named": True, **({"star": True} if had and had.get("star") else {})}
        with self._lock:
            self._store(path, [chat] + [c for c in self._load(path) if c.get("id") != chat_id], forever)
            if had and had.get("archived"):                      # used again: the whole chat is back in the file, its zip goes
                self.store.delete(self._zip_path(owner_id(email), chat_id))
        return {"id": chat_id, "updated": chat["updated"], "title": name}

    def update(self, email: str, chat_id: str, title=None, star=None) -> dict:
        """Rename a chat (any words, 1 to 70 characters) or star it (starred chats sit at the top of the list). Neither counts
        as using the chat: its `updated` (and so its 30 days) is left alone."""
        path = self._path(email)
        with self._lock:
            chats = self._load(path)
            chat = next((c for c in chats if c.get("id") == chat_id), None)
            if chat is None:
                raise Refused("There's no such chat.")
            if title is not None:
                title = re.sub(r"\s+", " ", str(title)).strip()[:70]
                if not title:
                    raise Refused("A chat's name can't be empty.")
                chat["title"], chat["named"] = title, True
            if star is not None:
                if not isinstance(star, bool):
                    raise Refused("Starred is true or false.")
                chat.pop("star", None)
                if star:
                    chat["star"] = True
            self._store(path, chats, self.forever(email))
        return {"id": chat_id, "title": chat["title"], "star": bool(chat.get("star"))}

    def forget(self, email: str, chat_id: str) -> None:
        """The person's own bin: gone at once (a chat they delete themselves is not analysed)."""
        path = self._path(email)
        with self._lock:
            chats = self._load(path)
            if any(c.get("id") == chat_id and c.get("archived") for c in chats):
                self.store.delete(self._zip_path(owner_id(email), chat_id))
            self._store(path, [c for c in chats if c.get("id") != chat_id], self.forever(email))

    # ── archive: zipped after 14 days, a stub left in the list, restored when opened ──
    def restore(self, email: str, chat_id: str) -> dict:
        """One chat, whole: an archived one is unzipped back into the file (its `updated` left alone) and its zip deleted."""
        path, owner = self._path(email), owner_id(email)
        with self._lock:
            chats = self._load(path)
            chat = next((c for c in chats if c.get("id") == chat_id), None)
            if chat is None or not (self.forever(email) or _fresh(chat, _now())):
                raise Refused("There's no such chat.")
            if not chat.get("archived"):
                return chat
            data = self.store.get(self._zip_path(owner, chat_id))
            if data is None:
                raise Refused("That chat's archive is missing.")
            full = {k: v for k, v in _unzip(data).items() if k not in ("archived", "star")}
            full.update(title=chat.get("title") or full.get("title"), named=True)   # renamed or starred while archived: the stub wins
            if chat.get("star"):
                full["star"] = True
            self._store(path, [full if c.get("id") == chat_id else c for c in chats], self.forever(email))
            self.store.delete(self._zip_path(owner, chat_id))
        return full

    def archive(self, keep_owners=()) -> int:
        """The nightly pass: every chat untouched for 14 days goes to its own zip and leaves a stub. A zip whose stub is gone
        (its file lost to the bucket's lifecycle rule) gets its stub back while the chat is still kept, else is deleted.
        `keep_owners` are the Captain's owner ids (kept forever). Answers how many chats were archived."""
        done, now = 0, _now()
        zips: dict[str, set] = {}
        for p in self.store.list(ARCHIVE):
            m = _ARCHIVE_FILE.fullmatch(p)
            if m:
                zips.setdefault(m.group(1), set()).add(m.group(2))
        owners = {m.group(1) for m in map(_OWNER_FILE.fullmatch, self.store.list(PREFIX)) if m} | set(zips)
        for owner in sorted(owners):
            path, forever = PREFIX + owner + ".json", owner in keep_owners
            with self._lock:
                chats, changed = self._load(path), False
                for i, chat in enumerate(chats):
                    if chat.get("archived") or not chat.get("history") or _fresh(chat, now, ARCHIVE_DAYS) \
                            or not _ID.fullmatch(str(chat.get("id") or "")):
                        continue
                    self.store.put(self._zip_path(owner, chat["id"]), _zip(chat), "application/zip")
                    chats[i], changed = _stub(chat), True
                    done += 1
                listed = {c.get("id") for c in chats}
                for chat_id in sorted(zips.get(owner, set()) - listed):    # an orphaned zip
                    zp = self._zip_path(owner, chat_id)
                    try:
                        chat = _unzip(self.store.get(zp) or b"")
                    except (ValueError, KeyError, zipfile.BadZipFile):
                        chat = None
                    if chat and chat.get("id") == chat_id and (forever or _fresh(chat, now)):
                        chats.append(_stub(chat))
                        changed = True
                    else:
                        self.store.delete(zp)
                if changed:
                    self._store(path, chats, forever)
        return done

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
                zp = self._zip_path(m.group(1), str(chat.get("id")))
                if chat.get("archived"):                         # its words are in its zip
                    data = self.store.get(zp)
                    chat = _unzip(data) if data else {**chat, "history": []}
                if chat.get("history"):
                    insight = analyse(chat)                      # raises when it can't: the chat stays for next time
                    record(m.group(1), chat, insight)
                with self._lock:
                    self._store(path, [c for c in self._load(path) if c.get("id") != chat.get("id")], False)
                    self.store.delete(zp)                        # its zip, if it was archived (none is no harm)
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
