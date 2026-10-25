"""Files skippy-pc makes for a chat (today: generated music), handed to the portal to play.

The home reporter uploads a finished job's audio to PUT /home/media/<job_id>; the chat
page plays it from GET /chat/media/<job_id>. It's a hand-off, not storage: held in this
process's memory (the portal runs one instance) for a while, only for a job the portal is
waiting on, only for audio types, and capped in size so a runaway upload can't eat the
portal's memory.
"""
from __future__ import annotations

import threading
import time

import popups

# Every type the patient queue keeps (patient.TYPES: songs, pictures and what pop-ups hand back), or a result kept in the bucket
# was refused here (415) and the reporter counted the whole job as failed.
TYPES = set(popups.OUTPUT_TYPES)
MAX_ONE = 30_000_000        # Cloud Run caps a request at 32 MB
MAX_ALL = 120_000_000       # of the portal's 1 GiB
TTL = 1800                  # seconds a clip stays playable


class MediaError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Media:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}

    def _expire(self) -> None:
        now = self.clock()
        self._items = {k: v for k, v in self._items.items() if now - v["at"] < TTL}

    def put(self, key: str, data: bytes, ctype: str, owner: str, title: str = "") -> dict:
        if ctype not in TYPES:
            raise MediaError(f"{ctype or 'that'} isn't a type the portal will hold", 415)
        if not data:
            raise MediaError("empty upload", 400)
        if len(data) > MAX_ONE:
            raise MediaError("that clip is too big", 413)
        with self._lock:
            self._expire()
            others = sum(len(v["data"]) for k, v in self._items.items() if k != key)
            if others + len(data) > MAX_ALL:
                raise MediaError("the portal's media hand-off is full; try again in a few minutes", 507)
            self._items[key] = {"data": data, "type": ctype, "owner": owner, "title": title[:100],
                                "at": self.clock()}
        return {"bytes": len(data)}

    def get(self, key: str) -> dict | None:
        with self._lock:
            self._expire()
            return self._items.get(key)
