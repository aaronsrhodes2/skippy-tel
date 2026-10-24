"""The patient queue (the Captain's design, 2026-10-23): pictures and songs are made when a machine is QUIET, not the moment someone asks.

Making a picture (on skippy-core, for quality) or a song (on skippy-pc's card) takes most of a GPU for minutes, and today's crashes came from
exactly that while someone was talking to Skippy. So these jobs wait: the portal holds each one and hands it to skippy-pc's reporter only
when its machine has been quiet for QUIET_S (no chat, voice or agent run on it) and, where the portal can see the card, at least
FREE_PCT of the GPU's memory is free and it is nearly idle (skippy-pc's card, from the starter's nvidia-smi). One at a time per machine. A
job may wait up to JOB_TTL; the chat shows a card that says where it is in line, then that it's being made, and fills in when it's done
(even after a reload, another day, or a portal redeploy). The Captain gets a Discord DM when his is ready.

Everything a job needs to outlive the request and the portal is written down in the Tabletop's private bucket as it starts
(pending/<job>.json: whose, what kind, the job itself, for which machine), and the finished picture or song is kept there too
(<keep|guest>/<job>.<ext> with its .json). Whose: a one-way id of the address (chat_history.owner_id), never the address. The Captain's
are kept forever; a guest's under guest/, which the bucket's lifecycle rule clears after 60 days. Without a bucket it all lives in memory.
"""
from __future__ import annotations

import json
import re
import time

import chat_history
import tabletop

PREFIX = "chat-pictures/"           # (the name it had when it held pictures only; songs live here too)
JOB_TTL = 24 * 3600                 # how long a job may wait and work before the portal gives up on it
QUIET_S = 300                       # a machine is quiet after this long without a chat, voice or agent run on it
FREE_PCT = 60                       # ...and its GPU (where the portal can see it) has at least this much memory free: skippy-pc's card
FREE_GIB = {"core": 60}             # skippy-core's memory is shared with the LLMs (~37 GB resident), so there it's GB, not a share:
                                    # enough for the picture model to load (Qwen-Image ~58 GB, from the pictures session, 2026-10-23)
BUSY_PCT = 20                       # ...and is busier than this at most
KINDS = {"make_image": "image", "make_music": "audio"}
TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
         "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/ogg": "ogg", "audio/flac": "flac"}
_JOB = re.compile(r"[A-Za-z0-9_-]{6,64}")


class Patient:
    def __init__(self, store=None, forever=None, clock=time.time):
        self.store = store if store is not None else tabletop.MemoryStore()
        self.forever = forever or (lambda email: False)
        self.clock = clock
        self.last_active: dict[str, float] = {}       # machine ("pc", "core") -> when someone last used it (this portal's memory)
        self.started = clock()                        # until someone uses a machine, its quiet counts from the portal's start

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

    # ── activity: what "quiet" is measured from ──
    def touch(self, *boxes: str) -> None:
        """Someone is using these machines now (a chat, a voice turn, an agent run): patient jobs wait for them to go quiet."""
        now = self.clock()
        for box in boxes:
            self.last_active[box] = now

    def quiet_for(self, box: str) -> float:
        """Seconds since someone last used `box`. A portal that just started knows nothing yet: it counts from its own start."""
        return self.clock() - self.last_active.get(box, self.started)

    def ready(self, box: str, gpu: dict | None = None) -> tuple[bool, str]:
        """(may a patient job start on `box` now?, why not). `gpu`: {"free_pct", "util_pct", "free_gib"} where the portal can see it."""
        quiet = self.quiet_for(box)
        if quiet < QUIET_S:
            return False, f"waiting for {('skippy-core' if box == 'core' else 'skippy-pc')} to be quiet for {QUIET_S // 60} minutes"
        if gpu:
            if box in FREE_GIB:                                    # a shared-memory box: enough GB free for the job to load
                if gpu.get("free_gib") is not None and gpu["free_gib"] < FREE_GIB[box]:
                    return False, f"waiting for memory to free up ({gpu['free_gib']:.0f} GB free, {FREE_GIB[box]} needed)"
            elif gpu.get("free_pct") is not None and gpu["free_pct"] < FREE_PCT:
                return False, f"waiting for the GPU to free up ({gpu['free_pct']}% free)"
            if gpu.get("util_pct") is not None and gpu["util_pct"] > BUSY_PCT:
                return False, f"waiting for the GPU to settle ({gpu['util_pct']}% busy)"
        return True, ""

    # ── a job's life ──
    def expect(self, job: str, email: str, kind: str, box: str, payload: dict) -> None:
        """A patient job has been queued for this person: write it down (with the job itself, so a restarted portal can queue it again)."""
        if not self._ok(job) or kind not in KINDS:
            return
        meta = {"owner": chat_history.owner_id(email), "keep": bool(self.forever(email)), "kind": kind, "box": box,
                "prompt": str(payload.get("prompt") or "")[:300],
                "job": {k: v for k, v in payload.items() if isinstance(v, (str, int, float, bool)) and not k.startswith("_")},
                "at": int(self.clock())}
        self.store.put(PREFIX + "pending/" + job + ".json", json.dumps(meta).encode(), "application/json")

    def pending(self, job: str) -> dict | None:
        meta = self._meta(PREFIX + "pending/" + job + ".json") if self._ok(job) else None
        if meta and self.clock() - meta.get("at", 0) > JOB_TTL:
            return None                                            # gave up on it
        return meta

    def waiting(self) -> dict[str, dict]:
        """Every job still owed, by id (what a restarted portal puts back in line)."""
        out = {}
        for path in self.store.list(PREFIX + "pending/"):
            job = path.rsplit("/", 1)[-1].removesuffix(".json")
            meta = self.pending(job)
            if meta:
                out[job] = meta
        return out

    def _done_meta(self, job: str) -> tuple[str, dict] | None:
        for shelf in ("keep", "guest"):
            meta = self._meta(f"{PREFIX}{shelf}/{job}.json")
            if meta:
                return f"{PREFIX}{shelf}/{job}", meta
        return None

    def keep(self, job: str, data: bytes, ctype: str, title: str = "") -> dict | None:
        """skippy-pc handed over a finished picture or song for a job written down here: keep it. Its record, or None if none is owed."""
        if not self._ok(job) or ctype not in TYPES:
            return None
        meta = self.pending(job)
        if meta is None:
            return None
        base = f"{PREFIX}{'keep' if meta.get('keep') else 'guest'}/{job}"
        self.store.put(base + "." + TYPES[ctype], data, ctype)
        done = {**{k: v for k, v in meta.items() if k != "job"}, "type": ctype, "title": str(title or meta.get("prompt") or "")[:80],
                "done": int(self.clock())}
        self.store.put(base + ".json", json.dumps(done).encode(), "application/json")
        self.store.delete(PREFIX + "pending/" + job + ".json")
        return done

    def owner(self, job: str) -> str | None:
        """The one-way id of whoever the picture or song is for (pending or done), or None."""
        if not self._ok(job):
            return None
        found = self._done_meta(job)
        meta = found[1] if found else self.pending(job)
        return meta.get("owner") if meta else None

    def state(self, job: str, email: str, is_admin: bool = False) -> str:
        """"done", "waiting" (owed: in line or being made) or "unknown" (no such job, given up on, or not this person's)."""
        if not self._ok(job):
            return "unknown"
        mine = lambda meta: is_admin or meta.get("owner") == chat_history.owner_id(email)   # noqa: E731
        found = self._done_meta(job)
        if found:
            return "done" if mine(found[1]) else "unknown"
        meta = self.pending(job)
        return "waiting" if meta and mine(meta) else "unknown"

    def get(self, job: str, email: str, is_admin: bool = False) -> tuple[bytes, str] | None:
        """The picture's or song's bytes and type, for its owner (or an admin); None otherwise."""
        found = self._done_meta(job) if self._ok(job) else None
        if not found:
            return None
        base, meta = found
        if not (is_admin or meta.get("owner") == chat_history.owner_id(email)):
            return None
        data = self.store.get(base + "." + TYPES.get(meta.get("type"), "png"))
        return (data, meta["type"]) if data is not None else None


def from_env(forever=None) -> Patient:
    return Patient(tabletop.store_from_env(), forever=forever)
