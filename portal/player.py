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

The house stereo (the hub's radio, Connect device skippyroom-stereo) plays ONE stream on whichever speakers are switched on, and says so in its
check-in: `kind` (song, spotify or book: what the current queue item is), `outs` (its speakers: id, name, on, open, floor) and `held` (paused
because no speaker is listening). The page flips a speaker with the command `out:<id>:on|off`. All of it is additive: a radio that sends none
of it reads as no speakers and not held, and a portal that does not know it simply never keeps it.
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
# The Audiobooks app (audiobooks.py) plays on a room box through the same commands queue: the hub's Connect hands `book:` ones to the room's BookPlayer
# (services/satellite/audiobooks.py COMMAND, kept identical): play:<book id>[:<second>], pause, resume, skip:<seconds>, chapter:<1|-1>, speed:<x>.
BOOK_CMD = re.compile(r"book:(?:play:[a-z0-9-]{1,200}(?::\d{1,6}(?:\.\d)?)?|pause|resume|skip:-?\d{1,4}|chapter:-?1|speed:\d(?:\.\d{1,2})?)")
MIX = re.compile(r"mix:(?:on|off)")                  # the house stereo's DJ mode (services/satellite/dj.py), switched from Bilby's stereo pane
# The house stereo's speakers (services/satellite/radio.py): one stream, played on whichever speakers the person switched on. A speaker's id is
# "enclave", "hdmi" or "pod:<room>" (a colon inside it), and the page's `out:<id>:on|off` flips that speaker's switch ("off" means off).
OUT_ID = re.compile(r"[a-z0-9_-]{1,24}(?::[a-z0-9_-]{1,24})?")
OUT = re.compile(r"out:" + OUT_ID.pattern + r":(?:on|off)")
OUT_FLOORS = ("upstairs", "downstairs")
KINDS = ("song", "spotify", "book")                  # what the current item of a queue is
MAX_OUTS = 12
REPEATS = ("off", "all", "one")
GATE_URL = re.compile(r"https://[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net")


def gate_key(token_sha256: str) -> bytes:
    """Must match gate.gate_key(): HMAC of a fixed label under the home token's sha256 (hex)."""
    return hmac.new(token_sha256.encode(), b"music-gate-v1", hashlib.sha256).digest()


def sign(key: bytes, exp: int) -> str:
    return f"{exp}.{hmac.new(key, f'music:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]}"


def _text(value, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _outs(value) -> list[dict]:
    """The house stereo's speakers, each typed and kept short: {id, name, on, open, floor}. An entry with a malformed id (or a repeat of one) is dropped, so a
    page that turns an id into a command can only ever send one OUT accepts. on: the person's switch; open: the speaker is really there right now."""
    out, seen = [], set()
    for o in value[:MAX_OUTS] if isinstance(value, list) else []:
        oid = o.get("id") if isinstance(o, dict) else None
        if not isinstance(oid, str) or not OUT_ID.fullmatch(oid) or oid in seen:
            continue
        seen.add(oid)
        out.append({"id": oid, "name": _text(o.get("name"), 40) or oid.rsplit(":", 1)[-1], "on": o.get("on") is True, "open": o.get("open") is True,
                    "floor": o.get("floor") if o.get("floor") in OUT_FLOORS else ""})
    return out


def _held(value) -> dict | None:
    """The stream is paused BECAUSE no speaker is listening: {why (a short sentence), since (epoch seconds)}, else None. It resumes by itself."""
    if not isinstance(value, dict):
        return None
    since = value.get("since")
    since = round(float(since), 1) if isinstance(since, (int, float)) and not isinstance(since, bool) and 0 <= since < 4e9 else 0.0
    return {"why": _text(value.get("why"), 80), "since": since}


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
    book = body.get("book")
    if isinstance(book, dict) and isinstance(book.get("id"), str) and re.fullmatch(r"[a-z0-9-]{1,200}", book["id"]):
        try:
            book = {"id": book["id"], "title": _text(book.get("title"), 200), "pos": round(max(0.0, min(float(book.get("pos") or 0), 864000.0)), 1),
                    "chapter": int(book.get("chapter") or 0), "playing": book.get("playing") is True, "speed": round(float(book.get("speed") or 1.0), 2)}
        except (TypeError, ValueError):
            book = None
    else:
        book = None
    # titles: names for queue items the library has none for (a Spotify URI), kept like the queue; mix: the stereo's DJ mode and crossfade
    titles = body.get("titles")
    if isinstance(titles, dict):
        titles = {k: [_text(v[0], 200), _text(v[1] if len(v) > 1 else "", 200)] for k, v in list(titles.items())[:MAX_QUEUE]
                  if isinstance(k, str) and 0 < len(k) <= MAX_PATH and isinstance(v, (list, tuple)) and v}
    else:
        titles = dict(previous["titles"]) if previous and previous.get("titles") else {}
    mix = body.get("mix")
    try:
        mix = {"on": mix.get("on") is True, "xfade": round(max(0.0, min(float(mix.get("xfade") or 0), 30.0)), 1)} if isinstance(mix, dict) else None
    except (TypeError, ValueError):
        mix = None
    return {"book": book, "titles": titles, "mix": mix, "track": _text(body.get("track"), MAX_PATH), "title": _text(body.get("title"), 200),
            "artist": _text(body.get("artist"), 200), "pos": round(pos, 1), "playing": body.get("playing") is True,
            "queue": queue, "idx": idx, "dj": body.get("dj") is True, "shuffle": body.get("shuffle") is True,
            "repeat": body.get("repeat") if body.get("repeat") in REPEATS else "off",
            # armed: None for an older page that does not say; otherwise has a play() worked since the page loaded?
            # tap: the page is waiting for someone to tap it (a play was refused, or a saved queue came back after a reload)
            "armed": armed if isinstance(armed, bool) else None, "tap": body.get("tap") is True,
            # the house stereo (all three are additive: an older radio omits them, and then they read "", [] and None):
            # kind: what the current item is; outs: the speakers one stream plays on; held: paused because no speaker is listening
            "kind": body.get("kind") if body.get("kind") in KINDS else "", "outs": _outs(body.get("outs")), "held": _held(body.get("held"))}


class Player:
    def __init__(self, home, clock=time.time, gate_url_re=None, edge=None):
        self.home, self.clock = home, clock
        # Tests and tools/dev_music.py hand in a pattern for a gate on this machine. Production never does: a gate address
        # off the tailnet is never signed for.
        self.gate_url_re = gate_url_re or GATE_URL
        self.edge = edge        # the direct door (edge.py): the pass also says where to reach the gate without the Funnel, when that works
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
        return {"ok": True, "url": url, "pass": sign(gate_key(stored), exp), "exp": exp,
                "direct": self.edge.direct("music") if self.edge else None}

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

    def listing(self, user: str) -> list[dict]:
        """Every device that is listed under "Play on" for this person right now (what a check-in is answered with), newest first."""
        with self._lock:
            return self._list(user, "", self.clock())

    def command(self, user: str, device: str, action: str) -> bool:
        if action not in ACTIONS and not TAKE.fullmatch(action) and not MIX.fullmatch(action) and not BOOK_CMD.fullmatch(action) and not OUT.fullmatch(action):
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
