"""What skippy-pc reports about itself: service health, UPS, models, photo
tags, UI telemetry, and Bilby's music state. The home reporter
(services/home-reporter) POSTs it every ~15 s; the portal can't reach home,
so control requests ride back on the reply.

State lives in this process (the portal runs one instance), so a fresh
instance shows "waiting" for one report interval. Pairing: the dashboard
makes a token, shows it once, and keeps only its hash in VM metadata
`portal-home`.
"""
from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import secrets
import threading
import time

KEY = "portal-home"
STALE_AFTER = 60          # seconds without a report → "home is silent"
MAX_REPORT = 1_000_000    # bytes (album art included)
_ACTIONS = {("music", "skip"), ("music", "pause"), ("music", "resume"), ("music", "stop")}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Home:
    def __init__(self, vm, clock=time.time):
        self.vm, self.clock = vm, clock
        self._lock = threading.Lock()
        self.report: dict | None = None
        self.received_at: float | None = None
        self.art: dict | None = None
        self._commands: list[dict] = []
        self._ids = itertools.count(1)
        self._hash_cache: tuple[float, str] | None = None

    # ── pairing ──────────────────────────────────────────────────────────
    def pair(self) -> str:
        token = secrets.token_urlsafe(32)
        self.vm.set_metadata(KEY, json.dumps({"token_sha256": _hash(token), "paired_at": int(self.clock())}))
        self._hash_cache = None
        return token

    def _stored_hash(self) -> str:
        now = self.clock()
        if self._hash_cache and now - self._hash_cache[0] < 60:
            return self._hash_cache[1]
        try:
            h = json.loads(self.vm.get_metadata(KEY) or "{}").get("token_sha256", "")
        except Exception:  # noqa: BLE001 — unreadable means unpaired
            h = ""
        self._hash_cache = (now, h)
        return h

    def paired(self) -> bool:
        return bool(self._stored_hash())

    def authorized(self, header: str | None) -> bool:
        stored = self._stored_hash()
        if not stored or not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(_hash(header[7:].strip()), stored)

    # ── reports ──────────────────────────────────────────────────────────
    def accept(self, report: dict) -> dict:
        art = report.pop("art", None)
        report.pop("results", None)
        with self._lock:
            self.report, self.received_at = report, self.clock()
            if art and art.get("b64"):
                self.art = art
            commands, self._commands = self._commands, []
            key = self.art_key()
            need_art = bool(key) and (not self.art or self.art.get("key") != key)
        return {"commands": commands, "need_art": need_art}

    def age(self) -> int | None:
        return None if self.received_at is None else int(self.clock() - self.received_at)

    def online(self) -> bool:
        age = self.age()
        return age is not None and age < STALE_AFTER

    def art_key(self) -> str | None:
        return (((self.report or {}).get("bilby") or {}).get("now_playing") or {}).get("art_url")

    def current_art(self) -> dict | None:
        art, key = self.art, self.art_key()
        return art if art and key and art.get("key") == key else None

    def queue(self, target: str, action: str) -> bool:
        if (target, action) not in _ACTIONS:
            return False
        with self._lock:
            self._commands = [c for c in self._commands if c["target"] != target][-10:]
            self._commands.append({"id": next(self._ids), "target": target, "action": action})
        return True

    # ── views ────────────────────────────────────────────────────────────
    def music(self) -> dict:
        """What Bilby knows is playing: the DJ decks on the Mac (Traktor) and
        the phone's music session. Safe for anyone with the Bilby grant."""
        r = self.report or {}
        mac = r.get("bilby") or None
        now = (mac or {}).get("now_playing")
        return {
            "online": self.online(),
            "age": self.age(),
            "traktor": {
                "online": bool(mac),
                "now_playing": {k: v for k, v in now.items() if k != "art_url"} if now else None,
                "deck_a": (mac or {}).get("deck_a"), "deck_b": (mac or {}).get("deck_b"),
                "playing_a": bool((mac or {}).get("playing_a")),
                "playing_b": bool((mac or {}).get("playing_b")),
            },
            "has_art": self.current_art() is not None,
            "up_next": r.get("up_next"),
            "setlist": r.get("setlist") or [],
            "session": r.get("music_session"),
        }

    def status(self) -> dict:
        """The old skippy-pc /status page's content. Admin only."""
        r = self.report or {}
        return {"online": self.online(), "age": self.age(), "paired": self.paired(),
                "status": r.get("status") or {}, "services": r.get("services") or []}
