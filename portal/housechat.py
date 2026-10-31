"""The permanent "Home" chat: the house conversation (what was said TO Skippy at a pod, what he said back, his timers and announcements, the tools he used),
words only, kept for the Captain forever and shown live in Skippy's chat (the first row of Your chats).

It is NOT one of chat_history's chats on purpose: those are the page's to save, rename, expire and delete; this one is written only by skippy-pc's hub
(POST /home/house/sync, the home token) and by the Captain's typed messages, so nothing can rename, expire or delete it.

Privacy: the hub forwards only what is addressed to Skippy (services/satellite/homechat.py); never audio. The Captain's switch (`forward`) tells the hub to
stop, and the thread says so. Typed messages ride back to the hub in the sync reply: "say" = treated exactly as if spoken in that room, "announce" =
Skippy says exactly this.

Storage: house-chat/home.json in the Tabletop bucket (a MemoryStore without one), newest MAX_EVENTS events.
"""
from __future__ import annotations

import json
import re
import threading
import time

from flask import Blueprint, abort, jsonify, request

PATH = "house-chat/home.json"
MAX_EVENTS = 3000
MAX_TEXT = 600
KINDS = ("heard", "said", "announce", "actions", "note")
ROOM = re.compile(r"[A-Za-z0-9_-]{1,40}")
MOODS = re.compile(r"[a-zA-Z]")


def _clean(raw, now: float) -> dict | None:
    """One event from the hub, cut to the fields the thread shows; None when it isn't one."""
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS or not ROOM.fullmatch(str(raw.get("room") or "")):
        return None
    ev = {"kind": raw["kind"], "room": raw["room"], "at": raw["at"] if isinstance(raw.get("at"), (int, float)) and raw["at"] <= now + 60 else now}
    if raw["kind"] == "actions":
        tools = [str(t)[:80] for t in raw.get("tools") or [] if isinstance(t, str)][:8]
        if not tools:
            return None
        ev["tools"] = tools
        return ev
    text = str(raw.get("text") or "").strip()[:MAX_TEXT]
    if not text:
        return None
    ev["text"] = text
    if raw["kind"] in ("said", "announce") and isinstance(raw.get("mood"), str) and len(raw["mood"]) == 1 and MOODS.fullmatch(raw["mood"]):
        ev["mood"] = raw["mood"]
    for flag in ("typed", "silent"):
        if raw.get(flag) is True:
            ev[flag] = True
    return ev


class HouseChat:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self._lock = threading.Lock()
        self._events: list[dict] | None = None
        self._state = {"forward": True}
        self.commands: list[dict] = []
        self.rooms: list[str] = []
        self.seen = 0.0                                          # when the hub last checked in

    # ── storage ──
    def _load(self) -> list[dict]:
        if self._events is None:
            try:
                got = json.loads(self.store.get(PATH) or "{}")
            except ValueError:
                got = {}
            self._events = [e for e in got.get("events", []) if isinstance(e, dict)]
            if isinstance(got.get("forward"), bool):
                self._state["forward"] = got["forward"]
            self._seq = max([e.get("seq", 0) for e in self._events] or [0])
        return self._events

    def _save(self) -> None:
        self._events = self._events[-MAX_EVENTS:]
        self.store.put(PATH, json.dumps({"forward": self._state["forward"], "events": self._events}, separators=(",", ":")).encode(), "application/json")

    def _add(self, events: list[dict]) -> int:
        self._load()
        for ev in events:
            self._seq += 1
            self._events.append({**ev, "seq": self._seq})
        if events:
            self._save()
        return self._seq

    # ── the Captain's page ──
    def view(self, after: int = 0) -> dict:
        with self._lock:
            events = self._load()
            fresh = [e for e in events if e["seq"] > after] if after else events[-300:]
            return {"events": fresh, "seq": self._seq, "forward": self._state["forward"], "rooms": list(self.rooms),
                    "hub": bool(self.seen and self.clock() - self.seen < 30)}

    def set_forward(self, on: bool) -> dict:
        with self._lock:
            self._load()
            self._state["forward"] = bool(on)
            self._add([{"kind": "note", "room": "house", "at": self.clock(), "text": "Forwarding is on: what is said to Skippy shows here." if on else
                        "Forwarding is off: nothing said at the pods is sent here until it is switched on."}])
        return {"forward": bool(on)}

    def send(self, mode: str, text: str, room: str, speak: bool) -> dict:
        """The Captain typed something. 'say': as if spoken in `room` ('*' = the first connected one). 'announce': Skippy says exactly this."""
        text = str(text or "").strip()[:MAX_TEXT]
        room = room if room == "*" or ROOM.fullmatch(str(room or "")) else ""
        if mode not in ("say", "announce") or not text or not room:
            raise ValueError("Say something, to a room.")
        now = self.clock()
        with self._lock:
            self._load()
            where = room if room != "*" else (self.rooms[0] if self.rooms else "house")
            if mode == "say":
                self._add([{"kind": "heard", "room": where, "at": now, "text": text, "typed": True}])
            elif not speak:                                      # nothing for the hub to do: it only goes in the thread
                self._add([{"kind": "announce", "room": where, "at": now, "text": text, "typed": True, "silent": True}])
            if speak or mode == "say":
                if not self._state["forward"]:
                    self._add([{"kind": "note", "room": "house", "at": now, "text": "Forwarding is off, so this was not answered."}])
                else:
                    self.commands.append({"id": str(int(now * 1000)), "kind": mode, "room": room, "text": text, "speak": bool(speak)})
                    self.commands = self.commands[-20:]
            return {"seq": self._seq}

    # ── skippy-pc's hub ──
    def sync(self, body: dict) -> dict:
        now = self.clock()
        events = [e for e in (_clean(x, now) for x in (body.get("events") or [])[:300]) if e]
        rooms = [r for r in (body.get("rooms") or []) if isinstance(r, str) and ROOM.fullmatch(r)][:20]
        with self._lock:
            self._load()
            self.seen, self.rooms = now, rooms
            if self._state["forward"]:                           # off: whatever the hub still had queued is dropped here too
                self._add(events)
            out, self.commands = self.commands, []
            return {"forward": self._state["forward"], "commands": out}


def register(app, auth, home, me, store) -> HouseChat:
    """The Home chat's routes. Captain only (admin); the sync is the hub's, behind the home token."""
    chat = HouseChat(store)
    bp = Blueprint("housechat", __name__)

    @bp.get("/home-chat/events")
    @auth.admin_required
    def home_chat_events():
        resp = jsonify(chat.view(request.args.get("after", type=int, default=0)))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.post("/home-chat/send")
    @auth.admin_required
    def home_chat_send():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(chat.send(str(body.get("mode") or ""), body.get("text"), str(body.get("room") or ""), body.get("speak") is not False))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

    @bp.post("/home-chat/forward")
    @auth.admin_required
    def home_chat_forward():
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("on"), bool):
            abort(400)
        return jsonify(chat.set_forward(body["on"]))

    @bp.post("/home/house/sync")
    def home_house_sync():
        if (request.content_length or 0) > 200_000:
            abort(413)
        if not home.authorized(request.headers.get("Authorization")):
            abort(401)
        return jsonify(chat.sync(request.get_json(silent=True) or {}))

    app.register_blueprint(bp)
    app.extensions["housechat"] = chat
    return chat
