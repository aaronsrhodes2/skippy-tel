"""The private Skippy: the Captain's chat on his dashboard, backed by Claude Code
(Opus, full permissions) running on skippy-pc through the bridge.

Only an admin can reach it (the portal's Google login plus ADMIN_EMAILS). The
portal can't reach skippy-pc, so a message becomes a job in the home queue; the
home reporter carries it to the bridge, reads the run's events back and pushes
them here (`push`), and the browser polls `read`. Nothing about the run lives
only in this process: the reporter keeps the truth and re-sends whatever a fresh
portal instance is missing, so a deploy of this very portal, mid-task, loses
nothing. Events are text and short activity lines (tool name and its command or
path), never tool output.
"""
from __future__ import annotations

import re
import threading
import time

MAX_MESSAGE = 8000
MAX_EVENTS = 5000
MAX_TEXT = 4000
KEEP_S = 6 * 3600          # how long a finished task stays readable
JOB_TTL = 2 * 3600         # how long the home queue keeps offering an unanswered task
OFFLINE_S = 30             # no push at all for this long: skippy-pc isn't picking it up
STALL_S = 120              # a running task silent for this long: the link to skippy-pc broke
_CONV = re.compile(r"[A-Za-z0-9-]{8,64}")
_STATES = ("running", "done", "error")


class Busy(Exception):
    """Skippy is still on the last message."""


class NotReady(Exception):
    """skippy-pc's bridge isn't reporting the workshop."""


def _clean(event) -> dict | None:
    """One event from home, reduced to what the chat may show; anything odd is dropped."""
    if not isinstance(event, dict):
        return None
    if event.get("t") == "text" and isinstance(event.get("text"), str):
        return {"t": "text", "text": event["text"][:MAX_TEXT]}
    if event.get("t") == "tool" and isinstance(event.get("name"), str):
        return {"t": "tool", "name": event["name"][:40], "detail": str(event.get("detail") or "")[:200]}
    return None


class Workshop:
    def __init__(self, home, clock=time.time):
        self.home, self.clock = home, clock
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    def ready(self) -> bool:
        return self.home.can("workshop")

    def _prune(self) -> None:
        now = self.clock()
        for task in [t for t, r in self._tasks.items() if now - r["touched"] > KEEP_S]:
            del self._tasks[task]

    def _live_state(self, rec: dict) -> str:
        """The record's state, corrected for a link that went quiet."""
        state, now = rec["state"], self.clock()
        if state == "queued" and now - rec["created"] > OFFLINE_S:
            return "offline"
        if state == "running" and now - rec["pushed_at"] > STALL_S:
            return "stalled"
        return state

    def send(self, owner: str, conv: str, message: str) -> str:
        message = (message or "").strip()
        if not message:
            raise ValueError("Say something.")
        if len(message) > MAX_MESSAGE:
            raise ValueError(f"That's too long: keep it under {MAX_MESSAGE} characters.")
        if not _CONV.fullmatch(conv or ""):
            raise ValueError("Bad conversation id.")
        if not self.ready():
            raise NotReady("skippy-pc's workshop isn't answering: is the bridge running, and is home reporting?")
        with self._lock:
            self._prune()
            if any(self._live_state(r) in ("queued", "running") for r in self._tasks.values()):
                raise Busy("Skippy is still working on the last one.")
            # The job id is the task id everywhere: here, in the reporter and in the bridge.
            task = self.home.start("workshop", owner, ttl=JOB_TTL, conv=conv, message=message, who=owner)
            now = self.clock()
            self._tasks[task] = {"owner": owner, "conv": conv, "events": [], "state": "queued", "error": None,
                                 "created": now, "pushed_at": now, "touched": now, "stop": False}
        return task

    def read(self, owner: str, task: str, after: int = 0) -> dict:
        """What the browser polls: events from index `after`, and the state."""
        with self._lock:
            rec = self._tasks.get(task)
            if not rec or rec["owner"] != owner:
                return {"state": "unknown", "events": [], "next": max(0, after), "error": None}
            rec["touched"] = self.clock()
            state = self._live_state(rec)
            if state in ("queued", "running"):
                self.home.touch_chat()            # keep the reporter's quick poll going while someone watches
            after = max(0, after)
            return {"state": state, "error": rec["error"], "events": rec["events"][after:], "next": len(rec["events"])}

    def stop(self, owner: str, task: str) -> bool:
        with self._lock:
            rec = self._tasks.get(task)
            if not rec or rec["owner"] != owner or rec["state"] not in ("queued", "running"):
                return False
            rec["stop"] = True
        return True

    def resync(self, owner: str, task: str) -> dict:
        """The browser's "check again": ask skippy-pc to look at this task now. Marks it (so the home listing shows
        `want`) and starts the reporter's quick polling; the reporter's reconciler answers within seconds."""
        with self._lock:
            rec = self._tasks.get(task)
            if rec is not None and rec["owner"] == owner:
                rec["resync"] = self.clock()
        self.home.touch_chat()
        seen = self.read(owner, task, 0)                 # state only: the browser keeps the events it already has
        return {"state": seen["state"], "error": seen["error"]}

    def push(self, task: str, who: str, conv: str, start: int, events: list, state: str, error: str | None) -> dict:
        """From skippy-pc (the home reporter). `start` is the index of events[0] in the run's own list; if this
        portal is missing earlier ones (it was redeployed), the reply's `have` tells the reporter to send them."""
        if not isinstance(events, list) or not isinstance(start, int) or start < 0 or state not in _STATES:
            raise ValueError("bad push")
        now = self.clock()
        with self._lock:
            rec = self._tasks.get(task)
            if rec is None:                       # a fresh instance: rebuild the record from what home says
                rec = self._tasks[task] = {"owner": str(who), "conv": str(conv), "events": [], "state": "running",
                                           "error": None, "created": now, "pushed_at": now, "touched": now,
                                           "stop": False}
            have = len(rec["events"])
            if start <= have:
                for event in events[have - start:]:
                    cleaned = _clean(event)
                    if cleaned and len(rec["events"]) < MAX_EVENTS:
                        rec["events"].append(cleaned)
                rec["state"] = state
                rec["error"] = (str(error)[:500] if error else None) if state != "running" else None
            rec["pushed_at"], rec["touched"] = now, now
            reply = {"have": len(rec["events"]), "stop": rec["stop"]}
        if state != "running":
            self.home.finish(task, {"result": {"state": state}})     # stop offering the job to the reporter
        return reply
