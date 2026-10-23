"""Pictures Skippy draws (the chat's make_image), kept until the chat that asked for them collects them (the Captain's design,
2026-10-23: pictures are drawn on skippy-core for quality, not speed; "pick it up when it is done and display it in the chat at
some point down the line").

A picture job may run for minutes or hours, longer than the chat request that asked for it and longer than the portal itself (a
deploy restarts it, and home.py's job list lives in memory). So each picture job is written down here, in the Tabletop's private
bucket, as soon as it starts (pending/<job>.json: whose it is and what was asked), and the finished picture is kept here too
(<keep|guest>/<job>.<ext> with its .json), so /chat/media/<job> can serve it after any restart, and the chat's card fills in
whenever it is done. Whose: a one-way id of the address (chat_history.owner_id), never the address. The Captain's pictures are
kept with his chats (forever); a guest's under guest/, which the bucket's lifecycle rule clears after 60 days.
Without a bucket (a local run, tests) it all lives in memory.
"""
from __future__ import annotations

import json
import re
import time

import chat_history
import tabletop

PREFIX = "chat-pictures/"
JOB_TTL = 6 * 3600                  # how long a picture job may take before the portal gives up on it
TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_JOB = re.compile(r"[A-Za-z0-9_-]{6,64}")


class Pictures:
    def __init__(self, store=None, forever=None, clock=time.time):
        self.store = store if store is not None else tabletop.MemoryStore()
        self.forever = forever or (lambda email: False)
        self.clock = clock

    @staticmethod
    def _ok(job: str) -> bool:
        return bool(_JOB.fullmatch(job or ""))

    def _meta(self, path: str) -> dict | None:
        raw = self.store.get(path)
        try:
            meta = json.loads(raw) if raw else None
        except ValueError:
            return None
        return meta if isinstance(meta, dict) else None

    def expect(self, job: str, email: str, prompt: str) -> None:
        """A picture job has started for this person: write it down so a restarted portal still knows whose it is."""
        if not self._ok(job):
            return
        meta = {"owner": chat_history.owner_id(email), "keep": bool(self.forever(email)), "prompt": str(prompt)[:300],
                "at": int(self.clock())}
        self.store.put(PREFIX + "pending/" + job + ".json", json.dumps(meta).encode(), "application/json")

    def pending(self, job: str) -> dict | None:
        meta = self._meta(PREFIX + "pending/" + job + ".json") if self._ok(job) else None
        if meta and self.clock() - meta.get("at", 0) > JOB_TTL:
            return None                                            # gave up on it
        return meta

    def _done_meta(self, job: str) -> tuple[str, dict] | None:
        for shelf in ("keep", "guest"):
            meta = self._meta(f"{PREFIX}{shelf}/{job}.json")
            if meta:
                return f"{PREFIX}{shelf}/{job}", meta
        return None

    def keep(self, job: str, data: bytes, ctype: str, title: str = "") -> bool:
        """skippy-pc handed over a finished picture for a job written down here: keep it. False if no such job is owed."""
        if not self._ok(job) or ctype not in TYPES:
            return False
        meta = self.pending(job)
        if meta is None:
            return False
        base = f"{PREFIX}{'keep' if meta.get('keep') else 'guest'}/{job}"
        self.store.put(base + "." + TYPES[ctype], data, ctype)
        done = {**meta, "type": ctype, "title": str(title or meta.get("prompt") or "")[:80], "done": int(self.clock())}
        self.store.put(base + ".json", json.dumps(done).encode(), "application/json")
        self.store.delete(PREFIX + "pending/" + job + ".json")
        return True

    def owner(self, job: str) -> str | None:
        """The one-way id of whoever the picture is for (pending or done), or None."""
        if not self._ok(job):
            return None
        found = self._done_meta(job)
        meta = found[1] if found else self.pending(job)
        return meta.get("owner") if meta else None

    def state(self, job: str, email: str, is_admin: bool = False) -> str:
        """"done", "drawing" or "unknown" (no such picture, given up on, or not this person's)."""
        if not self._ok(job):
            return "unknown"
        mine = lambda meta: is_admin or meta.get("owner") == chat_history.owner_id(email)   # noqa: E731
        found = self._done_meta(job)
        if found:
            return "done" if mine(found[1]) else "unknown"
        meta = self.pending(job)
        return "drawing" if meta and mine(meta) else "unknown"

    def get(self, job: str, email: str, is_admin: bool = False) -> tuple[bytes, str] | None:
        """The picture's bytes and type, for its owner (or an admin); None otherwise."""
        found = self._done_meta(job) if self._ok(job) else None
        if not found:
            return None
        base, meta = found
        if not (is_admin or meta.get("owner") == chat_history.owner_id(email)):
            return None
        data = self.store.get(base + "." + TYPES.get(meta.get("type"), "png"))
        return (data, meta["type"]) if data is not None else None


def from_env(forever=None) -> Pictures:
    return Pictures(tabletop.store_from_env(), forever=forever)
