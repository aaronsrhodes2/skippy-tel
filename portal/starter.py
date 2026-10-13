"""The starter: start and stop skippy-pc's whole stack from the portal, even when Docker and the bridge are down.

The portal on the cloud cannot reach into a PC at home, and the home reporter lives inside Docker (so it is down exactly when you need to start Docker). So a tiny
starter runs on the Windows host itself (tools/skippy_starter.py, started at sign-in by its own scheduled task) and does what the reporter does: it calls the
portal, outbound only, with the same home token. Every few seconds it posts how things are (is Docker up, how many containers, is the bridge answering) and gets
back the requests waiting for it. It carries out ONLY the three operations below, with no arguments, and reports each result on its next call.

  start     bring up Docker Desktop if needed, then the containers, then the bridge
  stop      stop the bridge (refused while a run is going) and the containers; Docker Desktop and the starter itself stay up
  restart   stop, then start

The portal side is this file (state, queue) plus the admin-only buttons in recovery_web.py and the machine route POST /home/starter in app.py. A PC that is
off, asleep or at the sign-in screen cannot answer: no setting on the portal can fix that (docs/portal/remote-recovery.md).
"""
from __future__ import annotations

import secrets
import threading
import time

OPS = ("start", "stop", "restart")
RESUMES = ("7am", "4h", "manual")        # a stop can say when the stack comes back by itself: 7 AM (the default), in 4 hours, or only when asked
ONLINE_S = 45                    # the starter polls every ~10 s; this long without a call and it is not there
REDELIVER_S = 30                 # a request not answered by then is handed over again (the starter ignores one it already has)
KEEP_RESULTS = 20
MAX_PENDING = 3
STATUS_KEYS = {"docker": bool, "containers_up": int, "containers_total": int, "bridge": bool, "busy": bool, "supervisor": bool, "working": str, "resume_at": str}


class Starter:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}          # id -> {id, op, by, at, sent}
        self._done: dict[str, dict] = {}             # id -> {ok, summary, at, op, by}
        self._events: dict[str, threading.Event] = {}
        self.status: dict = {}
        self.seen_at: float | None = None

    # ── the admin side ───────────────────────────────────────────────────────────
    def online(self) -> bool:
        return self.seen_at is not None and self.clock() - self.seen_at <= ONLINE_S

    def request(self, op: str, by: str, resume: str = "") -> str:
        """Queue one of OPS and return its id. The same op already waiting is not queued twice. A stop may carry `resume` (one of RESUMES)."""
        if op not in OPS:
            raise ValueError("not an operation")
        if op == "stop":
            resume = resume or "7am"
            if resume not in RESUMES:
                raise ValueError("not a way to resume")
        else:
            resume = ""
        with self._lock:
            for c in self._pending.values():
                if c["op"] == op:
                    return c["id"]
            if len(self._pending) >= MAX_PENDING:
                raise ValueError("too many requests are already waiting")
            cid = secrets.token_hex(4)
            self._pending[cid] = {"id": cid, "op": op, "by": by, "at": self.clock(), "sent": 0.0, "resume": resume}
            self._events[cid] = threading.Event()
            return cid

    def wait(self, cid: str, timeout: float) -> dict | None:
        ev = self._events.get(cid)
        if ev is not None:
            ev.wait(timeout)
        return self._done.get(cid)

    def withdraw(self, cid: str) -> None:
        with self._lock:
            self._pending.pop(cid, None)

    def view(self) -> dict:
        now = self.clock()
        with self._lock:
            last = sorted(self._done.values(), key=lambda d: d["at"], reverse=True)[:5]
            return {"online": self.online(), "age_s": None if self.seen_at is None else int(now - self.seen_at), "status": dict(self.status),
                    "waiting": [{"op": c["op"], "age_s": int(now - c["at"])} for c in self._pending.values()],
                    "recent": [{"op": d["op"], "ok": d["ok"], "summary": d["summary"], "age_s": int(now - d["at"]), "by": d["by"]} for d in last]}

    # ── the starter's side (POST /home/starter) ────────────────────────────────
    def poll(self, payload: dict) -> list[dict]:
        """Take the starter's status and results; return the requests it should carry out now: [{id, op}]."""
        now = self.clock()
        status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
        clean = {}
        for key, kind in STATUS_KEYS.items():
            v = status.get(key)
            if kind is bool and isinstance(v, bool):
                clean[key] = v
            elif kind is int and isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 999:
                clean[key] = v
            elif kind is str and isinstance(v, str):
                clean[key] = v[:60]
        with self._lock:
            self.status, self.seen_at = clean, now
            for r in (payload.get("results") if isinstance(payload.get("results"), list) else [])[:10]:
                cid = r.get("id") if isinstance(r, dict) else None
                c = self._pending.pop(cid, None) if isinstance(cid, str) else None
                if c is None:
                    continue
                self._done[cid] = {"ok": bool(r.get("ok")), "summary": str(r.get("summary") or "")[:300], "at": now, "op": c["op"], "by": c["by"]}
                if len(self._done) > KEEP_RESULTS:
                    for old in sorted(self._done, key=lambda k: self._done[k]["at"])[:-KEEP_RESULTS]:
                        self._done.pop(old, None)
                        self._events.pop(old, None)
                ev = self._events.get(cid)
                if ev is not None:
                    ev.set()
            out = []
            for c in self._pending.values():
                if now - c["sent"] >= REDELIVER_S:
                    c["sent"] = now
                    out.append({"id": c["id"], "op": c["op"], **({"resume": c["resume"]} if c.get("resume") else {})})
            return out
