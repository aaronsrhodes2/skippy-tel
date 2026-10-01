"""The private Skippy: the Captain's chat on his dashboard, backed by Claude Code
(Opus, full permissions) running on skippy-pc through the bridge.

Only an admin can reach it (the portal's Google login plus ADMIN_EMAILS). The
portal can't reach skippy-pc, so a message becomes a job in the home queue; the
home reporter carries it to the bridge, reads the run's events back and pushes
them here (`push`), and the browser polls `read`. Nothing about the run lives
only in this process: the bridge keeps the truth and the reporter re-sends
whatever a fresh portal instance is missing, so a deploy of this very portal,
mid-task, loses nothing. A deploy also wipes the home job queue, so the reporter
does not wait to be asked: it compares what the bridge holds with what `listing`
says this portal holds, every few seconds, and pushes the difference (the
reconciler in services/home-reporter/reporter.py). Events are text and short
activity lines (tool name and its command or path), never tool output.
"""
from __future__ import annotations

import re
import threading
import time

MAX_MESSAGE = 8000
MAX_EVENTS = 5000
MAX_TEXT = 4000
MAX_LISTED = 50            # records `listing` shows the reporter's reconciler
KEEP_S = 6 * 3600          # how long a finished task stays readable
JOB_TTL = 2 * 3600         # how long the home queue keeps offering an unanswered task
OFFLINE_S = 30             # no push at all for this long: skippy-pc isn't picking it up
STALL_S = 120              # a running task silent for this long: the link to skippy-pc broke
WANT_S = 120               # a person's "check again" is still worth answering for this long
_CONV = re.compile(r"[A-Za-z0-9-]{8,64}")
_STATES = ("running", "done", "error")
_FINAL = ("done", "error")


class Busy(Exception):
    """Skippy is still on the last message."""


class NotReady(Exception):
    """skippy-pc's bridge isn't reporting the workshop."""


class Paused(NotReady):
    """The Captain paused the private Skippy from the Admin page (recovery.py). A NotReady, so the chat's send route
    answers it as it answers any other "can't take a message now": a plain sentence, 503."""


PAUSED_MESSAGE = ("The private Skippy is paused (Admin page, Private Skippy card). Turn it back on there to send messages. "
                  "A deploy of the portal also turns it back on.")


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
        # The Admin page's kill switch. In this process's memory on purpose: the agent can't reach it (its only way into
        # the portal is a push and a deploy, and pausing cancels its run first). A deploy forgets it; the card says so.
        self.paused: dict | None = None          # {"by": email, "at": epoch s} while paused

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
        if self.paused:
            raise Paused(PAUSED_MESSAGE)
        if not self.ready():
            why = self.home.capability_reason("workshop")        # the bridge's own words, when it said why
            raise NotReady(f"skippy-pc's workshop isn't ready: {why}" if why else
                           "skippy-pc's workshop isn't answering: is the bridge running, and is home reporting?")
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
        """What the browser polls: events from index `after`, and the state. A task that is still going also says
        `quiet_s`, how long since skippy-pc last pushed anything, so the page can tell "no word for 45 s" from a
        slow task. `next` never goes below `after`: a record rebuilt by a fresh portal holds fewer events for a
        moment, and a page that took `next` at its word would show the same events twice."""
        with self._lock:
            rec = self._tasks.get(task)
            if not rec or rec["owner"] != owner:
                return {"state": "unknown", "events": [], "next": max(0, after), "error": None}
            now = self.clock()
            rec["touched"] = now
            state = self._live_state(rec)
            if state in ("queued", "running"):
                self.home.touch_chat()            # keep the reporter's quick poll going while someone watches
            after = max(0, after)
            out = {"state": state, "error": rec["error"], "events": rec["events"][after:],
                   "next": max(after, len(rec["events"]))}
            if rec["state"] in ("queued", "running"):          # the raw state: "stalled" and "offline" are these, gone quiet
                out["quiet_s"] = max(0, int(now - rec["pushed_at"]))
            return out

    def _withdraw(self, task: str, rec: dict, why: str) -> None:
        """Stop a task from the portal's side (call with the lock held). `stop` rides back to the bridge on the next push
        (the reporter relays it), and the home job is finished so the queue never offers it again: a message still waiting
        for skippy-pc (home off, the reporter down) is then never delivered, not delivered hours later beside the next one
        (audit L06). A task skippy-pc never acknowledged ends here, since nothing else will end it; a running one ends when
        the bridge says it stopped."""
        rec["stop"] = True
        self.home.finish(task, {"error": why})
        if rec["state"] == "queued":
            rec["state"], rec["error"] = "error", "stopped"
            rec["touched"] = self.clock()

    def stop(self, owner: str, task: str) -> bool:
        with self._lock:
            rec = self._tasks.get(task)
            if not rec or rec["owner"] != owner or rec["state"] not in ("queued", "running"):
                return False
            self._withdraw(task, rec, "stopped")
        return True

    # ── the Admin page's controls (recovery.py; admin only, like everything here) ──────────────────────
    def current(self) -> dict | None:
        """The newest task that hasn't ended, else the newest one: what the Private Skippy card shows."""
        now = self.clock()
        with self._lock:
            if not self._tasks:
                return None
            ordered = sorted(self._tasks.items(), key=lambda kv: kv[1]["created"], reverse=True)
            task, rec = next(((t, r) for t, r in ordered if r["state"] not in _FINAL), ordered[0])
            return {"task": task, "state": self._live_state(rec), "error": rec["error"], "owner": rec["owner"],
                    "age_s": max(0, int(now - rec["created"])), "quiet_s": max(0, int(now - rec["pushed_at"])),
                    "stopping": rec["stop"] and rec["state"] not in _FINAL, "open": rec["state"] not in _FINAL}

    def cancel_current(self) -> list[str]:
        """Stop every task that hasn't ended (normally the one current run), for anyone. Returns their ids."""
        with self._lock:
            open_ = [(t, r) for t, r in self._tasks.items() if r["state"] in ("queued", "running")]
            for task, rec in open_:
                self._withdraw(task, rec, "cancelled from the Admin page")
        return [t for t, _ in open_]

    def clear_stuck(self) -> list[str]:
        """End every unfinished record in the portal now, whatever skippy-pc last said: the chat stops spinning on it and
        Send comes back. Anything still running at home is told to stop (and a late "running" from it can't reopen an
        ended record: see push). Returns the ids cleared."""
        with self._lock:
            open_ = [(t, r) for t, r in self._tasks.items() if r["state"] not in _FINAL]
            for task, rec in open_:
                self._withdraw(task, rec, "cleared from the Admin page")
                rec["state"], rec["error"], rec["touched"] = "error", "Cleared from the Admin page.", self.clock()
        return [t for t, _ in open_]

    def pause(self, by: str) -> list[str]:
        """Refuse new messages and cancel the current run. Returns the ids cancelled."""
        self.paused = {"by": by, "at": self.clock()}
        return self.cancel_current()

    def resume(self) -> bool:
        was, self.paused = self.paused is not None, None
        return was

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

    def listing(self) -> dict:
        """What this portal holds, for the reporter's reconciler (GET /home/workshop, home token): every record, newest
        first. `state` is the live one (so "stalled" shows), `have` is how many events it holds, `final` says it ended
        (done or error: its events are all it will ever get), `quiet_s` is the time since the last push and `want` says a
        person pressed "check again" lately. The owner is included: the caller holds the home token, the one that
        pushes records for any owner anyway."""
        now = self.clock()
        with self._lock:
            self._prune()
            newest_first = sorted(reversed(list(self._tasks.items())), key=lambda kv: kv[1]["created"], reverse=True)
            tasks = [{"task": task, "state": self._live_state(rec), "have": len(rec["events"]),
                      "final": rec["state"] in _FINAL, "owner": rec["owner"],
                      "quiet_s": max(0, int(now - rec["pushed_at"])),
                      "want": rec.get("resync") is not None and now - rec["resync"] <= WANT_S}
                     for task, rec in newest_first[:MAX_LISTED]]
        return {"tasks": tasks}

    def push(self, task: str, who: str, conv: str, start: int, events: list, state: str, error: str | None) -> dict:
        """From skippy-pc (the home reporter). `start` is the index of events[0] in the run's own list; if this
        portal is missing earlier ones (it was redeployed), the reply's `have` tells the reporter to send them.

        Several things push the same run (the streaming relay, and the reconciler that repairs whatever the relay
        missed), so a push is idempotent and can arrive late:
          * a push is a heartbeat: any accepted one refreshes `pushed_at`, running or not, even with no new event;
          * events are only ever appended at `have`, so repeating or overlapping a push adds nothing twice;
          * a task that ended stays ended against a stale "running" (a relay that read the run a moment before
            the reconciler delivered its end must not reopen it); a new end may still replace an old one, because
            the bridge is the truth and the reporter's own guess ("lost contact") may have been wrong;
          * only a push that really made the record final stops the home queue offering the job: one that left a
            gap (`start` beyond `have`) said nothing about the end;
          * a task nobody owns can't be created: no one could ever read it."""
        if not isinstance(events, list) or not isinstance(start, int) or start < 0 or state not in _STATES:
            raise ValueError("bad push")
        now = self.clock()
        ended = False
        with self._lock:
            rec = self._tasks.get(task)
            if rec is None:                       # a fresh instance: rebuild the record from what home says
                if not str(who or "").strip():
                    raise ValueError("a task with no owner")
                rec = self._tasks[task] = {"owner": str(who), "conv": str(conv), "events": [], "state": "running",
                                           "error": None, "created": now, "pushed_at": now, "touched": now,
                                           "stop": False}
            have = len(rec["events"])
            if start <= have:
                for event in events[have - start:]:
                    cleaned = _clean(event)
                    if cleaned and len(rec["events"]) < MAX_EVENTS:
                        rec["events"].append(cleaned)
                if state != "running":
                    rec["state"], rec["error"], ended = state, (str(error)[:500] if error else None), True
                elif rec["state"] not in _FINAL:
                    rec["state"], rec["error"] = "running", None
            elif state == "running" and rec["state"] == "queued":
                rec["state"] = "running"          # a gap, but skippy-pc is on it: it is no longer waiting to be picked up
            rec["pushed_at"], rec["touched"] = now, now
            rec.pop("resync", None)               # this is the answer to anyone's "check again"
            reply = {"have": len(rec["events"]), "stop": rec["stop"]}
        if ended:
            self.home.finish(task, {"result": {"state": state}})     # stop offering the job to the reporter
        return reply
