"""The house conversation, forwarded to the Maker Portal's permanent "Home" chat (words only, never audio).

Only what was ADDRESSED to Skippy goes: the words after his name or a tap, or inside an open conversation (Room.handle), what he said back
(Room.say), his timers and announcements, and one folded line for the tools he used. Room talk that was not for him ("heard something that was
not for me") never reaches this module, and nothing is queued while a room's recorder runs (`hub.py --record`).

The hub already dials the portal for Bilby Connect (music.Connect, the home token), so the same way carries this: every ~1.5 s a POST of the
queued events to /home/house/sync, whose reply carries what the Captain typed in the Home chat ("say to Skippy" or "make Skippy say") and
whether forwarding is on. emit() is a deque append: it never waits, and never raises.

    SATELLITE_HOME_CHAT=off   keeps the hub from forwarding anything (the portal's own switch does the same from the Home chat)
"""
from __future__ import annotations

import asyncio
import time
from collections import deque

MAX_TEXT = 600
KINDS = ("heard", "said", "announce", "actions")
POLL_S = 1.5


class HomeChat:
    def __init__(self, portal: str, token: str, enabled: bool = True, post=None, log=print, sleep=asyncio.sleep, clock=time.time):
        self.portal, self.token, self.enabled, self.log, self.sleep, self.clock = portal.rstrip("/"), token, enabled, log, sleep, clock
        self.post = post or self._post
        self.queue: deque = deque(maxlen=300)
        self.forward = True                                      # the portal's switch (the Captain's): off = nothing is queued
        self._warned = False
        self.tasks: set = set()

    def _post(self, url: str, body: dict) -> dict:
        import requests                                          # noqa: PLC0415
        r = requests.post(url, json=body, headers={"Authorization": f"Bearer {self.token}"}, timeout=10)
        r.raise_for_status()
        return r.json()

    def emit(self, room, kind: str, **fields) -> None:
        """Queue one turn event for the Home chat. Quiet, instant, and refused for a room whose recorder runs or while forwarding is off."""
        try:
            if not self.enabled or not self.forward or kind not in KINDS or getattr(room, "recorder", None) is not None:
                return
            event = {"at": round(self.clock(), 1), "room": str(getattr(room, "name", room)), "kind": kind}
            for k, v in fields.items():
                if v is None:
                    continue
                event[k] = v[:MAX_TEXT] if isinstance(v, str) else v
            self.queue.append(event)
        except Exception:                                        # noqa: BLE001 — a log line must never hurt an answer
            pass

    async def once(self, hub) -> None:
        events = []
        while self.queue:
            events.append(self.queue.popleft())
        body = {"events": events, "rooms": sorted(hub.rooms)}
        try:
            reply = await asyncio.to_thread(self.post, f"{self.portal}/home/house/sync", body)
            self._warned = False
        except Exception as e:                                   # noqa: BLE001 — the portal is away: keep the words for the next try
            for event in reversed(events):
                self.queue.appendleft(event)
            if not self._warned:
                self.log(f"(Home chat: the portal is not answering: {type(e).__name__})")
                self._warned = True
            return
        reply = reply if isinstance(reply, dict) else {}
        self.forward = reply.get("forward", True) is not False
        if not self.forward:
            self.queue.clear()
        for cmd in reply.get("commands") or []:
            task = asyncio.ensure_future(self.command(hub, cmd))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def command(self, hub, cmd: dict) -> None:
        """What the Captain typed in the Home chat: "say" = as if spoken in the room, "announce" = Skippy says exactly this."""
        try:
            kind, text, speak = str(cmd.get("kind")), str(cmd.get("text") or "").strip()[:MAX_TEXT], cmd.get("speak") is not False
            where = str(cmd.get("room") or "*")
            rooms = [hub.rooms[where]] if where in hub.rooms else ([] if where != "*" else list(hub.rooms.values()))
            if not text or not rooms:
                self.log(f"(Home chat: nothing to do for {kind!r} in {where!r})")
                return
            if kind == "say":
                await rooms[0].typed(text, speak)               # one answer, from the first room when "all"
            elif kind == "announce":
                for room in rooms:
                    await room.announce(text, typed=True)
        except Exception as e:                                   # noqa: BLE001
            self.log(f"(Home chat: command failed: {type(e).__name__}: {e})")

    async def run(self, hub) -> None:
        if not self.enabled:
            self.log("Home chat: forwarding is off (SATELLITE_HOME_CHAT=off)")
            return
        while True:
            await self.once(hub)
            await self.sleep(POLL_S)
