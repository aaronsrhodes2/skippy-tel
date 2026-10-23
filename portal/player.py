"""Bilby's Audio Service: the Captain's library on his iPhone, Mac, PC and Tesla, like Spotify but his, in the browser.

The music never passes through here. skippy-pc's music-gate (services/music-gate/gate.py) serves the files to the
internet through Tailscale Funnel, and only for a pass this module signs: the key comes from the home token's sha256,
which this portal already keeps in VM metadata and skippy-pc already has, so there is no extra secret. The home
reporter says where the gate is and whether it answers (capability "music_gate").

What does live here is the "Connect" part: each open player checks in every few seconds with what it is playing and
where it is in it. Another device can pick up where that one left off, or send it play/pause/skip as a remote. That is
in memory (the portal runs one instance); a redeploy forgets it, and the players simply check in again.

A browser only makes sound after a tap on the page, and that starts over at every reload, so a player also says whether
it is "armed" (a play() has worked since its page loaded) and whether it is waiting for a tap. A remote's Play to a player
nobody has tapped can then say so instead of silently doing nothing.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time

NAME = "Bilby's Audio Service"
SHORT_NAME = "Bilby"
PASS_S = 8 * 3600             # a pass lasts a listening session at least; the player asks again before it runs out
PASS_BUCKET_S = 6 * 3600      # ... and it expires on a six-hour grid, so every pass handed out inside one bucket is the SAME string:
                              # the gate carries the pass in the URL path, so the browser's cache (covers a week, songs a day) only
                              # helps across page loads if the URL does not change on every load
LIVE_S = 90                   # a device that checked in this recently is listed as open
KEEP_S = 30 * 24 * 3600       # what a device last played is offered for a month
MAX_QUEUE = 2000
MAX_PATH = 400
MAX_DEVICES = 12
ACTIONS = ("play", "pause", "next", "prev")
# The speakers on skippy-pc. Each is a browser window there (tools/room_players.py) pinned to one audio output, a Connect device
# with a fixed id that is listed even while idle. `match` is tested against the output's name as Chrome reports it (Windows'
# own name for the endpoint), so a renamed or replaced speaker is a change to its pattern here.
ROOMS = {
    "downstairs": {"name": "Downstairs (HDMI)", "match": "EPSON|NVIDIA|HDMI"},
    "upstairs": {"name": "Upstairs (Bluetooth)", "match": "Enclave"},
}
# The room boxes (services/satellite/rooms: a Waveshare ESP32-S3 86 box per room), checked in by skippy-pc's hub through the home-token route
# /home/music/checkin, never by a browser: a box is "box-<room>", its Connect device "skippyroom-<room>". Listed like a speaker window.
BOX_ROOM = re.compile(r"box-[a-z0-9-]{1,20}")
BOX_DEVICE = re.compile(r"skippyroom-[a-z0-9-]{1,20}")
TAKE = re.compile(r"take:[A-Za-z0-9_-]{8,40}")      # "pick up what that device is playing": a room's push, since a page can only pull
REPEATS = ("off", "all", "one")
GATE_URL = re.compile(r"https://[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net")


def gate_key(token_sha256: str) -> bytes:
    """Must match gate.gate_key(): HMAC of a fixed label under the home token's sha256 (hex)."""
    return hmac.new(token_sha256.encode(), b"music-gate-v1", hashlib.sha256).digest()


def sign(key: bytes, exp: int) -> str:
    return f"{exp}.{hmac.new(key, f'music:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]}"


def _text(value, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _state(body: dict, previous: dict | None = None) -> dict:
    """What a player says it's doing, kept to known fields of sane size.

    A player sends its queue only when it changed (a window of up to 600 paths is ~50 KB: too much for every few
    seconds), so a body without `queue` keeps the one already held and just moves `idx` along it."""
    queue = body.get("queue")
    if isinstance(queue, list):
        queue = [q for q in queue[:MAX_QUEUE] if isinstance(q, str) and 0 < len(q) <= MAX_PATH]
    else:
        queue = list(previous["queue"]) if previous else []
    try:
        pos = max(0.0, min(float(body.get("pos") or 0), 86400.0))
    except (TypeError, ValueError):
        pos = 0.0
    idx = body.get("idx")
    idx = idx if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(queue) else 0
    armed = body.get("armed")
    return {"track": _text(body.get("track"), MAX_PATH), "title": _text(body.get("title"), 200),
            "artist": _text(body.get("artist"), 200), "pos": round(pos, 1), "playing": body.get("playing") is True,
            "queue": queue, "idx": idx, "dj": body.get("dj") is True, "shuffle": body.get("shuffle") is True,
            "repeat": body.get("repeat") if body.get("repeat") in REPEATS else "off",
            # armed: None for an older page that does not say; otherwise has a play() worked since the page loaded?
            # tap: the page is waiting for someone to tap it (a play was refused, or a saved queue came back after a reload)
            "armed": armed if isinstance(armed, bool) else None, "tap": body.get("tap") is True}


class Player:
    def __init__(self, home, clock=time.time, gate_url_re=None):
        self.home, self.clock = home, clock
        # Tests and tools/dev_music.py hand in a pattern for a gate on this machine. Production never does: a gate address
        # off the tailnet is never signed for.
        self.gate_url_re = gate_url_re or GATE_URL
        self._lock = threading.Lock()
        self._devices: dict[str, dict[str, dict]] = {}     # user → device id → {name, state, seen, updated}
        self._commands: dict[str, dict[str, list]] = {}    # user → device id → queued actions

    # ── the gate ─────────────────────────────────────────────────────────
    def gate_url(self) -> str | None:
        info = ((self.home.report or {}).get("capabilities") or {}).get("music_gate")
        url = info.get("url") if isinstance(info, dict) else None
        return url.rstrip("/") if isinstance(url, str) and self.gate_url_re.fullmatch(url.rstrip("/")) else None

    def gate(self) -> dict:
        """Where the library is and a pass for it, or why it can't be played right now."""
        stored = self.home._stored_hash()
        if not stored:
            return {"ok": False, "why": "skippy-pc isn't paired with the portal."}
        if not self.home.online():
            return {"ok": False, "why": "skippy-pc isn't reporting in, so the library is out of reach."}
        url = self.gate_url()
        if not url or not self.home.can("music_gate"):
            return {"ok": False, "why": "skippy-pc is up, but its music gate isn't answering."}
        exp = (int(self.clock()) // PASS_BUCKET_S + 1) * PASS_BUCKET_S + PASS_S      # 8 to 14 hours from now: see PASS_BUCKET_S
        return {"ok": True, "url": url, "pass": sign(gate_key(stored), exp), "exp": exp}

    # ── Connect: devices, handoff, remote ────────────────────────────────
    def checkin(self, user: str, device: str, name: str, body: dict | None, room: str = "", box: bool = False) -> dict:
        """A player saying what it's doing (body) or just looking (None). Returns the actions waiting for it and every
        device's latest, newest first. `room` is set by the speaker windows on skippy-pc (one of ROOMS), or by the hub for a
        room box (`box`: only the home-token route says so, and then only a BOX_ROOM)."""
        now = self.clock()
        with self._lock:
            mine = self._devices.setdefault(user, {})
            d = mine.setdefault(device, {"name": "", "state": None, "seen": 0.0, "updated": 0.0, "room": ""})
            d["name"], d["seen"] = _text(name, 40) or "A browser", now
            d["room"] = room if room in ROOMS or (box and BOX_ROOM.fullmatch(room or "")) else ""
            if body is not None:
                d["state"], d["updated"] = _state(body, d["state"]), now
            for gone in [k for k, v in mine.items() if now - v["seen"] > KEEP_S]:
                del mine[gone]
            while len(mine) > MAX_DEVICES:
                del mine[min(mine, key=lambda k: mine[k]["seen"])]
            waiting = self._commands.get(user, {}).pop(device, [])
            return {"commands": waiting, "devices": self._list(user, device, now)}

    def _list(self, user: str, me: str, now: float) -> list[dict]:
        out = []
        for dev_id, d in self._devices.get(user, {}).items():
            if dev_id == me or (not d.get("room") and (not d["state"] or not d["state"]["track"])):
                continue                    # a speaker window is listed even when idle: it is somewhere to send the music
            state = dict(d["state"] or _state({}))
            live = now - d["seen"] < LIVE_S
            if state["playing"] and live:      # where it is now, not where it was at its last check-in
                state["pos"] = round(state["pos"] + (now - d["updated"]), 1)
            state["playing"] = state["playing"] and live
            out.append({"id": dev_id, "name": d["name"], "live": live, "age": int(now - d["updated"]), "state": state,
                        "room": d.get("room") or ""})
        return sorted(out, key=lambda x: x["age"])

    def command(self, user: str, device: str, action: str) -> bool:
        if action not in ACTIONS and not TAKE.fullmatch(action):
            return False
        with self._lock:
            d = self._devices.get(user, {}).get(device)
            if not d or self.clock() - d["seen"] > LIVE_S:
                return False
            box = self._commands.setdefault(user, {}).setdefault(device, [])
            box[:] = (box + [action])[-5:]
        return True

    def needs_tap(self, user: str, device: str) -> bool:
        """Will a Play sent to this device be refused by its browser until someone taps it? True when the device says nothing has
        played since its page loaded (armed is false) or that it is waiting on a tap; False when it is armed or does not say."""
        with self._lock:
            state = (self._devices.get(user, {}).get(device) or {}).get("state")
        return bool(state) and (state.get("armed") is False or state.get("tap") is True)
