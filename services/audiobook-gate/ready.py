"""The readiness contract: what a service's GET /health says (PLATINUM section 24, "Ready means ready").

An HTTP 200 only proves a web server answers. A service says, in JSON: whether it is READY to take work right now, whether it is
ACCEPTING, what JOBS it has (running, queued, how old the oldest is), when it last made PROGRESS (`since`; a job running with no progress for a
long time is HUNG, which the Status page tells apart from busy), and plain words (`detail`) when it is not ready.

    {"spec": 1, "service": "mentat", "ready": true, "accepting": true,
     "jobs": {"running": 1, "queued": 0, "oldest_s": 2.4}, "since": "2026-10-27T21:04:05Z", "stuck_s": 0, "uptime_s": 913,
     "detail": "", "deps": {"llm": {"ready": true, "detail": ""}}}

This file is the canonical copy. Each service that serves /health keeps an identical `ready.py` beside its code (services build as separate
images), and tools/tests/test_ready_copies.py fails when a copy differs. Standard library only.
"""
from __future__ import annotations

import contextlib
import threading
import time
from datetime import datetime, timezone

SPEC = 1


class Readiness:
    def __init__(self, service: str, clock=time.time):
        self.service, self.clock = service, clock
        self.started = self.progress_at = clock()
        self._lock = threading.Lock()
        self._jobs: dict[int, tuple[str, float]] = {}
        self._seq = 0
        self._queued = 0

    # ── what the service tells us as it works ───────────────────────────────────
    def begin(self, label: str = "") -> int:
        with self._lock:
            self._seq += 1
            self._jobs[self._seq] = (label, self.clock())
            return self._seq

    def end(self, token: int) -> None:
        with self._lock:
            self._jobs.pop(token, None)
            self.progress_at = self.clock()

    def progress(self) -> None:
        """A running job got somewhere (a word written, a step finished): `since` moves, so it is not hung."""
        with self._lock:
            self.progress_at = self.clock()

    def queued(self, n: int) -> None:
        with self._lock:
            self._queued = max(0, int(n))

    @contextlib.contextmanager
    def job(self, label: str = ""):
        token = self.begin(label)
        try:
            yield token
        finally:
            self.end(token)

    # ── what /health answers ───────────────────────────────────────────────────
    def report(self, ready: bool = True, detail: str = "", deps: dict | None = None, accepting: bool | None = None, **extra) -> dict:
        now = self.clock()
        with self._lock:
            running = len(self._jobs)
            oldest = max((now - at for _, at in self._jobs.values()), default=None)
            queued = self._queued
            progress_at = self.progress_at
        deps = deps or {}
        everything = bool(ready) and all(d.get("ready") for d in deps.values())
        if bool(ready) and not everything and not detail:
            detail = "waiting on " + ", ".join(n + (f" ({d['detail']})" if d.get("detail") else "") for n, d in deps.items() if not d.get("ready"))
        out = {"spec": SPEC, "service": self.service, "ready": everything, "accepting": everything if accepting is None else bool(accepting and everything),
               "jobs": {"running": running, "queued": queued, "oldest_s": None if oldest is None else round(oldest, 1)},
               "since": datetime.fromtimestamp(progress_at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "stuck_s": int(now - progress_at) if running else 0, "uptime_s": int(now - self.started),
               "detail": "" if everything else (detail or "not ready"), "deps": deps}
        out.update(extra)
        return out


def status_code(report: dict) -> int:
    """200 only when it is ready: a plain 200 check can no longer say "fine" about a service that is not."""
    return 200 if report.get("ready") else 503
