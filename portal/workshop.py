"""The private Skippy: the Captain's chat on his dashboard, backed by Claude Code
(Sonnet 5.5, medium effort, full permissions) running on skippy-pc through the bridge.

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

import base64
import binascii
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
MAX_ATTACHED = 4                   # pictures that may go with one message (pasted screenshots, mostly)
MAX_IMAGE_B64 = 1_200_000          # one picture, as base64 (the page shrinks each to about a megabyte)
MAX_IMAGES_B64 = 3_200_000         # all of them together
_DATA_URL = re.compile(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/]+={0,2})")
_MAGIC = {"jpeg": b"\xff\xd8\xff", "png": b"\x89PNG\r\n\x1a\n", "webp": b"RIFF"}


def clean_images(raw) -> list[dict]:
    """The pictures that came with a message, as [{"mime", "b64"}], or ValueError saying what is wrong. Nothing is trusted
    from the page: each must be a base64 data URL of a jpeg, png or webp whose first bytes say so, within the size limits."""
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_ATTACHED:
        raise ValueError(f"At most {MAX_ATTACHED} pictures to a message.")
    out, total = [], 0
    for item in raw:
        m = _DATA_URL.fullmatch(item) if isinstance(item, str) else None
        if not m:
            raise ValueError("A picture wasn't a jpeg, png or webp.")
        kind, b64 = m.group(1), m.group(2)
        total += len(b64)
        if len(b64) > MAX_IMAGE_B64 or total > MAX_IMAGES_B64:
            raise ValueError("Those pictures are too big: try fewer or smaller ones.")
        try:
            head = base64.b64decode(b64[:16] + "=" * (-len(b64[:16]) % 4))[:8]
            base64.b64decode(b64, validate=True)
        except ValueError:
            raise ValueError("A picture was damaged.") from None
        if not head.startswith(_MAGIC[kind]):
            raise ValueError("A picture wasn't what it said it was.")
        out.append({"mime": "image/" + kind, "b64": b64})
    return out
_PROJECT = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_STATES = ("running", "done", "error")
_FINAL = ("done", "error")
DEFAULT_PROJECT = "skippytel"   # every message before projects, and the one a home that doesn't list projects offers
LIST_TTL = 120                  # how long a Conversations-list (or one-conversation) job waits for skippy-pc
MAX_LISTED_CONVERSATIONS = 50


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
        return {"t": "tool", "name": event["name"][:40],
                "detail": str(event.get("detail") or "")[:(1200 if event["name"] == "TodoWrite" else 200)]}      # (a checklist is long)
    if event.get("t") == "ship":
        return ship_event(event)
    return None


_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,100}")
MAX_SHIPS = 200                 # commits the ship card may ask about (the newest the portal has seen pushed)


MAX_IMAGE_BYTES = 400_000
MAX_IMAGES_PER_TASK = 3
MAX_IMAGES = 40                 # held in memory across all tasks (about 16 MB at most); the oldest go first
_B64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")


def image_event(event) -> tuple[dict, bytes] | None:
    """A screenshot from the bridge ({"t": "image", mime, name, b64}), or None. Exactly that shape; the bytes must decode,
    be at most MAX_IMAGE_BYTES, and BE the type they claim by their own first bytes (PNG or JPEG only): the portal serves
    them as that type with nosniff, so nothing else (an HTML page, a script) can ever be served from here."""
    if not isinstance(event, dict) or set(event) != {"t", "mime", "name", "b64"} or event["t"] != "image":
        return None
    mime, name, b64 = event["mime"], event["name"], event["b64"]
    if mime not in ("image/png", "image/jpeg") or not isinstance(name, str) or not isinstance(b64, str):
        return None
    if len(b64) > (MAX_IMAGE_BYTES * 4) // 3 + 4 or not _B64.fullmatch(b64):
        return None
    try:
        data = base64.b64decode(b64, validate=True)
    except (ValueError, binascii.Error):
        return None
    magic = b"\x89PNG\r\n\x1a\n" if mime == "image/png" else b"\xff\xd8\xff"
    if not data.startswith(magic) or len(data) > MAX_IMAGE_BYTES:
        return None
    clean_name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "screenshot"
    return {"t": "image", "mime": mime, "name": clean_name}, data


def ship_event(event) -> dict | None:
    """The run pushed a commit (services/claude-bridge: Workshop._ship_check). Exactly this shape or nothing: every field
    a string matching its pattern (a 40-hex sha, an owner/name repo...), the subject one line of at most 200 characters.
    The page shows the subject as text and builds links only from the checked repo and sha."""
    if not isinstance(event, dict) or set(event) != {"t", "project", "repo", "branch", "sha", "subject", "workflow"}:
        return None
    if not all(isinstance(event[k], str) for k in event):
        return None
    if not (_PROJECT.fullmatch(event["project"]) and _REPO.fullmatch(event["repo"]) and _BRANCH.fullmatch(event["branch"])
            and _SHA.fullmatch(event["sha"]) and event["workflow"] in ("", "deploy.yml", "deploy.yaml")):
        return None
    return {"t": "ship", "project": event["project"], "repo": event["repo"], "branch": event["branch"],
            "sha": event["sha"], "subject": " ".join(event["subject"].split())[:200], "workflow": event["workflow"]}


def _str(value, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _when(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _conversation_summary(raw) -> dict | None:
    """One row of the Conversations list, from skippy-pc's answer, or None if it isn't one."""
    if not isinstance(raw, dict) or not isinstance(raw.get("conv"), str) or not _CONV.fullmatch(raw["conv"]):
        return None
    project = raw.get("project") if isinstance(raw.get("project"), str) and _PROJECT.fullmatch(raw["project"]) else DEFAULT_PROJECT
    turns = raw.get("turns")
    return {"conv": raw["conv"], "project": project, "who": _str(raw.get("who"), 200),
            "title": _str(raw.get("title"), 90) or "(no message)", "started": _when(raw.get("started")),
            "last": _when(raw.get("last")) or _when(raw.get("started")),
            "turns": turns if isinstance(turns, int) and not isinstance(turns, bool) and 0 <= turns < 100000 else 0,
            "state": raw.get("state") if raw.get("state") in ("running", "done", "error") else "done"}


def _conversation(raw) -> dict | None:
    """A whole conversation from skippy-pc, reduced to what the chat shows: his messages, and Skippy's events as _clean
    allows them."""
    head = _conversation_summary({**raw, "turns": 0}) if isinstance(raw, dict) else None
    if head is None or not isinstance(raw.get("turns"), list):
        return None
    turns = []
    for t in raw["turns"][-200:]:
        if not isinstance(t, dict):
            continue
        events = [e for e in (_clean(x) for x in (t.get("events") if isinstance(t.get("events"), list) else [])[-MAX_EVENTS:]) if e]
        task = t.get("task") if isinstance(t.get("task"), str) and re.fullmatch(r"[a-f0-9]{8,32}", t["task"]) else None
        turns.append({"task": task,                # lets the page follow a turn that is still running
                      "message": t["message"][:MAX_MESSAGE] if isinstance(t.get("message"), str) else "",
                      "events": events, "at": _when(t.get("at")),
                      "state": t.get("state") if t.get("state") in ("running", "done", "error") else "done",
                      "error": t["error"][:500] if isinstance(t.get("error"), str) else None})
    trimmed = raw.get("trimmed")
    return {"conv": head["conv"], "project": head["project"], "who": head["who"], "title": head["title"],
            "turns": turns, "trimmed": trimmed if isinstance(trimmed, int) and trimmed > 0 else 0}


class Workshop:
    def __init__(self, home, clock=time.time):
        self.home, self.clock = home, clock
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()
        # The Admin page's kill switch. In this process's memory on purpose: the agent can't reach it (its only way into
        # the portal is a push and a deploy, and pausing cancels its run first). A deploy forgets it; the card says so.
        self.paused: dict | None = None          # {"by": email, "at": epoch s} while paused
        # The commits a run said it pushed, by (owner, sha): the ship card asks about these and nothing else, so a page
        # (or anyone with the Captain's cookie) can't make the portal query GitHub for an arbitrary commit.
        self._ships: dict[tuple[str, str], dict] = {}
        self.on_final = None                     # (news: dict) -> None: a run this process saw going has ended (notify.py)
        self._images: dict[tuple[str, int], tuple[str, str, bytes]] = {}   # (task, n) -> (owner, mime, bytes)

    def _keep_image(self, task: str, rec: dict, got) -> dict:
        """Hold a checked screenshot (call with the lock held) and return the event the chat gets: {"t":"image","n":..}
        (the page loads it from /me/workshop/image), or a plain line when it was refused or the task has its three."""
        n = sum(1 for e in rec["events"] if e["t"] == "image")
        if got is None or n >= MAX_IMAGES_PER_TASK:
            return {"t": "text", "text": "(a screenshot couldn't be shown)"}
        event, data = got
        self._images[(task, n)] = (rec["owner"], event["mime"], data)
        while len(self._images) > MAX_IMAGES:
            self._images.pop(next(iter(self._images)))
        return {**event, "n": n}

    def image(self, owner: str, task: str, n: int) -> tuple[str, bytes] | None:
        """(mime, bytes) of a screenshot in one of `owner`'s tasks, else None."""
        with self._lock:
            held = self._images.get((task, n))
        if held is None or held[0] != owner:
            return None
        return held[1], held[2]

    def remember_ship(self, owner: str, ship: dict) -> None:
        with self._lock:
            self._ships.pop((owner, ship["sha"]), None)
            self._ships[(owner, ship["sha"])] = ship
            while len(self._ships) > MAX_SHIPS:
                self._ships.pop(next(iter(self._ships)))

    def ship(self, owner: str, sha: str) -> dict | None:
        with self._lock:
            return self._ships.get((owner, sha))

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

    def projects(self) -> list[dict]:
        """The projects the chat offers: what skippy-pc lists, else just SkippyTel (a home reporter older than projects)."""
        return self.home.workshop_projects() or [{"id": DEFAULT_PROJECT, "name": "SkippyTel",
                                                  "repo": "aaronsrhodes2/skippy-tel", "branch": "main"}]

    def brains(self) -> list[dict]:
        """Who the agent can think with: what skippy-pc lists, else Claude alone (a home reporter older than brains)."""
        return self.home.workshop_brains() or [{"id": "claude", "label": "Claude Sonnet", "ready": True}]

    def project(self, pid: str | None) -> dict | None:
        """One offered project by id ("" = the default), or None when skippy-pc doesn't offer it."""
        pid = pid or DEFAULT_PROJECT
        return next((p for p in self.projects() if p["id"] == pid), None)

    def send(self, owner: str, conv: str, message: str, project: str = "", images=None, brain: str = "claude") -> str:
        message = (message or "").strip()
        pictures = clean_images(images)
        if not message and not pictures:
            raise ValueError("Say something.")
        if len(message) > MAX_MESSAGE:
            raise ValueError(f"That's too long: keep it under {MAX_MESSAGE} characters.")
        if not _CONV.fullmatch(conv or ""):
            raise ValueError("Bad conversation id.")
        chosen = self.project(project)
        if chosen is None:                       # only a project skippy-pc lists can be worked on: never a typed repo
            raise ValueError("skippy-pc doesn't offer that project.")
        project = chosen["id"]
        offered = next((b for b in self.brains() if b["id"] == (brain or "claude")), None)
        if offered is None:                      # only a brain skippy-pc lists: never a typed model
            raise ValueError("skippy-pc doesn't offer that brain.")
        if not offered["ready"]:
            raise NotReady(f"{offered['label']} isn't reachable from skippy-pc right now.")
        brain = offered["id"]
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
            task = self.home.start("workshop", owner, ttl=JOB_TTL, conv=conv, message=message, who=owner, project=project, brain=brain,
                                   **({"images": pictures} if pictures else {}))
            now = self.clock()
            self._tasks[task] = {"owner": owner, "conv": conv, "events": [], "state": "queued", "error": None,
                                 "created": now, "pushed_at": now, "touched": now, "stop": False, "project": project, "brain": brain,
                                 "message": message[:200]}     # its first words, for the notification (never shown to others)
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
                   "next": max(after, len(rec["events"])), "project": rec.get("project") or DEFAULT_PROJECT}
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

    # ── conversations: the Captain's history, read from skippy-pc (the bridge keeps it, conversation_log.py) ──────
    # A job each time (the portal can't reach home; a report carrying every conversation would be big for nothing), and
    # the browser polls `history_result` for the answer. What comes back is checked and cut here, field by field, since
    # it comes from whatever holds the home token, and only conversations the asker owns are shown.
    def ask_history(self, owner: str, conv: str | None = None) -> str:
        if conv is not None and not _CONV.fullmatch(conv):
            raise ValueError("Bad conversation id.")
        if conv is None:
            return self.home.start("workshop_conversations", owner, ttl=LIST_TTL)
        return self.home.start("workshop_conversation", owner, ttl=LIST_TTL, conv=conv)

    # ── New folder: D:/Aaron/development/<name>, its git repo and the private GitHub repo (services/claude-bridge/new_folder.py) ──
    def new_folder(self, owner: str, name: str) -> str:
        name = str(name or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{1,59}", name):
            raise ValueError("A folder name is letters, digits, spaces or hyphens (2 to 60).")
        return self.home.start("workshop_new_folder", owner, ttl=200, name=name)

    def new_folder_result(self, owner: str, job: str) -> dict:
        """{"state", "folder": {id, name, repo, remote, why}} once done; only the fields the picker needs, checked."""
        if not re.fullmatch(r"[a-f0-9]{8,32}", job or "") or self.home.owner_of(job) != owner:
            return {"state": "unknown"}
        seen = self.home.peek(job)
        if seen["state"] != "done":
            return {"state": seen["state"], "error": seen.get("error")}
        body = seen.get("result") if isinstance(seen.get("result"), dict) else {}
        folder = {k: str(body.get(k) or "")[:300] for k in ("id", "name", "repo", "remote", "why")}
        if not _PROJECT.fullmatch(folder["id"]) or folder["remote"] not in ("created", "pending"):
            return {"state": "error", "error": "skippy-pc's answer didn't look like a new folder."}
        return {"state": "done", "folder": folder}

    def history_result(self, owner: str, job: str) -> dict:
        """{"state": "running"|"done"|"error"|"unknown", ...}: for a finished list job `conversations`, for a finished
        one-conversation job `conversation`."""
        if not re.fullmatch(r"[a-f0-9]{8,32}", job or "") or self.home.owner_of(job) != owner:
            return {"state": "unknown"}
        seen = self.home.peek(job)
        if seen["state"] != "done":
            return {"state": seen["state"], "error": seen.get("error")}
        body = seen.get("result") if isinstance(seen.get("result"), dict) else {}
        if isinstance(body.get("conversations"), list):
            listed = [c for c in (_conversation_summary(x) for x in body["conversations"]) if c and c["who"] == owner]
            return {"state": "done", "conversations": [{k: v for k, v in c.items() if k != "who"}
                                                       for c in listed[:MAX_LISTED_CONVERSATIONS]]}
        conv = _conversation(body)
        if conv is None or conv.pop("who") != owner:
            return {"state": "error", "error": "That conversation isn't yours, or skippy-pc doesn't have it."}
        for turn in conv["turns"]:                       # a reopened conversation's ship cards may be asked about too
            for event in turn["events"]:
                if event["t"] == "ship":
                    self.remember_ship(owner, event)
        return {"state": "done", "conversation": conv}

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

    def push(self, task: str, who: str, conv: str, start: int, events: list, state: str, error: str | None,
             project: str = "", finished_age_s=None) -> dict:
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
        age = finished_age_s if isinstance(finished_age_s, (int, float)) and not isinstance(finished_age_s, bool) else None
        with self._lock:
            rec = self._tasks.get(task)
            created = rec is None
            if rec is None:                       # a fresh instance: rebuild the record from what home says
                if not str(who or "").strip():
                    raise ValueError("a task with no owner")
                rec = self._tasks[task] = {"owner": str(who), "conv": str(conv), "events": [], "state": "running",
                                           "error": None, "created": now, "pushed_at": now, "touched": now,
                                           "stop": False,
                                           "project": project if _PROJECT.fullmatch(project or "") else DEFAULT_PROJECT}
            if created and age is not None:
                rec["relearned"] = True           # a run that was already over when this portal first heard of it
            was = rec["state"]
            have = len(rec["events"])
            shipped = []
            if start <= have:
                for event in events[have - start:]:
                    if isinstance(event, dict) and event.get("t") == "image":
                        if len(rec["events"]) < MAX_EVENTS:
                            # Always one event in its place (a picture, or a line saying it couldn't be shown), so the
                            # reporter's `have` count stays in step with the bridge's list.
                            rec["events"].append(self._keep_image(task, rec, image_event(event)))
                        continue
                    cleaned = _clean(event)
                    if cleaned and len(rec["events"]) < MAX_EVENTS:
                        rec["events"].append(cleaned)
                        if cleaned["t"] == "ship":
                            shipped.append(cleaned)
                if state != "running":
                    rec["state"], rec["error"], ended = state, (str(error)[:500] if error else None), True
                elif rec["state"] not in _FINAL:
                    rec["state"], rec["error"] = "running", None
            elif state == "running" and rec["state"] == "queued":
                rec["state"] = "running"          # a gap, but skippy-pc is on it: it is no longer waiting to be picked up
            rec["pushed_at"], rec["touched"] = now, now
            rec.pop("resync", None)               # this is the answer to anyone's "check again"
            reply = {"have": len(rec["events"]), "stop": rec["stop"]}
            owner = rec["owner"]
            # Tell the Captain (portal/notify.py) only when THIS process saw the run going and now sees it end: a record
            # rebuilt from a run that was already over (a deploy, then the reporter catching up) is never news.
            final_news = None
            if ended and not created and was in ("queued", "running") and not rec.get("relearned"):
                last_text = next((e["text"] for e in reversed(rec["events"]) if e["t"] == "text"), "")
                final_news = {"owner": owner, "task": task, "project": rec.get("project") or DEFAULT_PROJECT,
                              "request": rec.get("message") or "", "state": state, "error": rec["error"],
                              "last_text": last_text, "finished_age_s": age}
        for ship in shipped:
            self.remember_ship(owner, ship)
        if final_news is not None and self.on_final is not None:
            try:
                self.on_final(final_news)
            except Exception:  # noqa: BLE001 — a notification must never fail the reporter's push
                pass
        if ended:
            self.home.finish(task, {"result": {"state": state}})     # stop offering the job to the reporter
        return reply
