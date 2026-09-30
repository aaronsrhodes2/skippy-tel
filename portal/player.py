"""The Music app: the Captain's library on his iPhone, Mac, PC and Tesla, like Spotify but his, in the browser.

The music never passes through here. skippy-pc's music-gate (services/music-gate/gate.py) serves the files to the
internet through Tailscale Funnel, and only for a pass this module signs: the key comes from the home token's sha256,
which this portal already keeps in VM metadata and skippy-pc already has, so there is no extra secret. The home
reporter says where the gate is and whether it answers (capability "music_gate").

What does live here is the "Connect" part: each open player checks in every few seconds with what it is playing and
where it is in it. Another device can pick up where that one left off, or send it play/pause/skip as a remote. That is
in memory (the portal runs one instance); a redeploy forgets it, and the players simply check in again.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time

PASS_S = 8 * 3600             # a pass lasts a listening session; the player asks again before it runs out
LIVE_S = 90                   # a device that checked in this recently is listed as open
KEEP_S = 30 * 24 * 3600       # what a device last played is offered for a month
MAX_QUEUE = 2000
MAX_PATH = 400
MAX_DEVICES = 12
ACTIONS = ("play", "pause", "next", "prev")
GATE_URL = re.compile(r"https://[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net")


def gate_key(token_sha256: str) -> bytes:
    """Must match gate.gate_key(): HMAC of a fixed label under the home token's sha256 (hex)."""
    return hmac.new(token_sha256.encode(), b"music-gate-v1", hashlib.sha256).digest()


def sign(key: bytes, exp: int) -> str:
    return f"{exp}.{hmac.new(key, f'music:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]}"


def _text(value, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _state(body: dict) -> dict:
    """What a player says it's doing, kept to known fields of sane size."""
    queue = body.get("queue")
    queue = [q for q in queue[:MAX_QUEUE] if isinstance(q, str) and 0 < len(q) <= MAX_PATH] if isinstance(queue, list) else []
    try:
        pos = max(0.0, min(float(body.get("pos") or 0), 86400.0))
    except (TypeError, ValueError):
        pos = 0.0
    idx = body.get("idx")
    idx = idx if isinstance(idx, int) and 0 <= idx < len(queue) else 0
    return {"track": _text(body.get("track"), MAX_PATH), "title": _text(body.get("title"), 200),
            "artist": _text(body.get("artist"), 200), "pos": round(pos, 1), "playing": body.get("playing") is True,
            "queue": queue, "idx": idx, "dj": body.get("dj") is True, "shuffle": body.get("shuffle") is True}


class Player:
    def __init__(self, home, clock=time.time):
        self.home, self.clock = home, clock
        self._lock = threading.Lock()
        self._devices: dict[str, dict[str, dict]] = {}     # user → device id → {name, state, seen, updated}
        self._commands: dict[str, dict[str, list]] = {}    # user → device id → queued actions

    # ── the gate ─────────────────────────────────────────────────────────
    def gate_url(self) -> str | None:
        info = ((self.home.report or {}).get("capabilities") or {}).get("music_gate")
        url = info.get("url") if isinstance(info, dict) else None
        return url.rstrip("/") if isinstance(url, str) and GATE_URL.fullmatch(url.rstrip("/")) else None

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
        exp = int(self.clock()) + PASS_S
        return {"ok": True, "url": url, "pass": sign(gate_key(stored), exp), "exp": exp}

    # ── Connect: devices, handoff, remote ────────────────────────────────
    def checkin(self, user: str, device: str, name: str, body: dict | None) -> dict:
        """A player saying what it's doing (body) or just looking (None). Returns the actions waiting for it and every
        device's latest, newest first."""
        now = self.clock()
        with self._lock:
            mine = self._devices.setdefault(user, {})
            d = mine.setdefault(device, {"name": "", "state": None, "seen": 0.0, "updated": 0.0})
            d["name"], d["seen"] = _text(name, 40) or "A browser", now
            if body is not None:
                d["state"], d["updated"] = _state(body), now
            for gone in [k for k, v in mine.items() if now - v["seen"] > KEEP_S]:
                del mine[gone]
            while len(mine) > MAX_DEVICES:
                del mine[min(mine, key=lambda k: mine[k]["seen"])]
            waiting = self._commands.get(user, {}).pop(device, [])
            return {"commands": waiting, "devices": self._list(user, device, now)}

    def _list(self, user: str, me: str, now: float) -> list[dict]:
        out = []
        for dev_id, d in self._devices.get(user, {}).items():
            if dev_id == me or not d["state"] or not d["state"]["track"]:
                continue
            state = dict(d["state"])
            live = now - d["seen"] < LIVE_S
            if state["playing"] and live:      # where it is now, not where it was at its last check-in
                state["pos"] = round(state["pos"] + (now - d["updated"]), 1)
            state["playing"] = state["playing"] and live
            out.append({"id": dev_id, "name": d["name"], "live": live, "age": int(now - d["updated"]), "state": state})
        return sorted(out, key=lambda x: x["age"])

    def command(self, user: str, device: str, action: str) -> bool:
        if action not in ACTIONS:
            return False
        with self._lock:
            d = self._devices.get(user, {}).get(device)
            if not d or self.clock() - d["seen"] > LIVE_S:
                return False
            box = self._commands.setdefault(user, {}).setdefault(device, [])
            box[:] = (box + [action])[-5:]
        return True
