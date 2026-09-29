"""Sliding-window rate limits (in process: the portal runs one instance).

Used where a caller can cost money or compute: the public Mentat playground
(per visitor IP) and Skippy's chat (per signed-in person; the Captain is
exempt). Numbers live in app.py next to their routes.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class Limiter:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: float) -> bool:
        """Record a hit for `key` and say whether it's within `limit` per `window_s`."""
        now = self.clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


def client_ip(request) -> str:
    """Cloud Run's front end appends the real client address LAST to
    X-Forwarded-For; anything before it is whatever the caller sent."""
    hops = [h.strip() for h in (request.headers.get("X-Forwarded-For") or "").split(",") if h.strip()]
    return hops[-1] if hops else (request.remote_addr or "?")
