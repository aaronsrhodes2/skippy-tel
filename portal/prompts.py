"""Every chat prompt is kept until it has a real answer (the Captain, 2026-11-01: "chats should have their prompt queued so it won't be
'answered' and will retry if it never got an answer ... so it won't matter much if we take down running models").

The page gives each message a stable id (`prompt_id`). /chat/send claims it here as `pending`, answers it, and on a dropped reply
(Skippy Core went away mid-answer, a 5xx, an empty reply: chat.Dropped) waits and tries again with backoff instead of showing the
failure as the answer. While it waits, GET /chat/pending/<id> says "waiting for Skippy, retrying" with the attempt and why. The same
id sent again (a reload, a dropped connection, a redeploy) joins the turn in progress, or gets the answer already recorded, so a
prompt is answered once. In memory: the portal runs one instance; a redeploy forgets, and the page then sends the same id again.
"""
from __future__ import annotations

import re
import threading
import time

ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
KEEP_S = 3600.0                    # an answered (or abandoned) prompt is remembered this long, for a late resend
RETRY_FOR_S = 300.0                # one /chat/send keeps trying this long; then it says "still pending" and the page sends again
BACKOFF_S = (2.0, 4.0, 8.0, 15.0, 30.0)   # between tries; the last repeats


def backoff(attempt: int) -> float:
    """The pause before try number `attempt + 1` (attempt counts the failures so far, from 1)."""
    return BACKOFF_S[min(max(attempt, 1), len(BACKOFF_S)) - 1]


class Prompt:
    def __init__(self, pid: str, owner: str):
        self.id, self.owner = pid, owner
        self.state = "pending"             # pending -> answered (a reply, or a final refusal the person must see)
        self.attempts = 0                  # failed tries so far
        self.why = ""                      # the last failure, in words
        self.retry_at: float | None = None # when the next try starts (time.time()), while waiting
        self.result: tuple[dict, int] | None = None
        self.working = False               # a request is answering it right now
        self.created = self.touched = time.time()
        self.done = threading.Event()

    def view(self) -> dict:
        now = time.time()
        return {"id": self.id, "state": self.state, "attempts": self.attempts, "why": self.why, "working": self.working,
                "retry_in": None if self.retry_at is None else round(max(0.0, self.retry_at - now), 1)}


class Prompts:
    def __init__(self, clock=time.time):
        self._lock = threading.Lock()
        self._all: dict[str, Prompt] = {}
        self.clock = clock

    def _sweep(self) -> None:
        cut = self.clock() - KEEP_S
        for pid in [p for p, e in self._all.items() if e.touched < cut and not e.working]:
            del self._all[pid]

    def claim(self, pid: str, owner: str) -> tuple[Prompt, bool]:
        """(the prompt, True if this request must answer it). False: it is answered already, or another request is on it (wait())."""
        with self._lock:
            self._sweep()
            p = self._all.get(pid)
            if p is None or p.owner != owner:
                p = self._all[pid] = Prompt(pid, owner)   # someone else's id is never theirs to read: a fresh prompt of their own
            p.touched = self.clock()
            if p.state == "answered" or p.working:
                return p, False
            p.working = True
            return p, True

    def get(self, pid: str, owner: str) -> Prompt | None:
        with self._lock:
            p = self._all.get(pid)
            return p if p is not None and p.owner == owner else None

    def failed(self, p: Prompt, why: str) -> float:
        """A try failed in a way worth retrying: note it; returns the pause before the next one."""
        with self._lock:
            p.attempts += 1
            p.why = why[:300]
            pause = backoff(p.attempts)
            p.retry_at = self.clock() + pause
            p.touched = self.clock()
            return pause

    def answered(self, p: Prompt, payload: dict, status: int) -> None:
        with self._lock:
            p.state, p.result, p.working, p.retry_at, p.touched = "answered", (payload, status), False, None, self.clock()
        p.done.set()

    def released(self, p: Prompt) -> None:
        """This request gave up for now (its window ran out, or the person left): still pending, the next send takes it up."""
        with self._lock:
            p.working, p.touched = False, self.clock()
