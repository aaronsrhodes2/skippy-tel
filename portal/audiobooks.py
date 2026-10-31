"""The Audiobooks app: the Captain's converted audiobook library in a browser, and on a pod or stereo. His alone: never a friend's grant.

Personal-use conversions of books he bought. The audio never passes through here: skippy-pc's audiobook-gate (services/audiobook-gate/gate.py)
serves the files, the covers and the places through Tailscale Funnel (:10000/books), and only for a pass this module signs: the key comes from
the home token's sha256 under its own label ("audiobook-gate-v1"), so a music, video or voice pass never opens it. The home reporter says where
the gate is and whether it answers (capability "book_gate").

What lives here is the Connect part: "Play on" a room box (Office pod). The hub's box checks in through Bilby's Connect (player.py; the hub says
what its book is doing), and a `book:` command queued for it is handed to the room's BookPlayer (services/satellite/audiobooks.py), which plays the
book from the same place the page saved, because the gate writes the browser's places where the hub reads them.
"""
from __future__ import annotations

import hashlib
import hmac
import re

import player as player_mod

NAME = "Audiobooks"
SHORT_NAME = "Books"
PASS_S = 8 * 3600
PASS_BUCKET_S = 6 * 3600       # a pass expires on a six-hour grid, so every pass handed out inside one bucket is the SAME string (the browser's cache keys on the URL)
# The gate is a path on the voice gate's Funnel port (:10000/books), so unlike music's address this one has a port and a mount.
GATE_URL = re.compile(r"https://[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net(:\d{2,5})?/books")


def gate_key(token_sha256: str) -> bytes:
    """Must match audiobook-gate gate.gate_key()."""
    return hmac.new(token_sha256.encode(), b"audiobook-gate-v1", hashlib.sha256).digest()


def sign(key: bytes, exp: int) -> str:
    return f"{exp}.{hmac.new(key, f'audiobooks:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]}"


class Books:
    def __init__(self, home, player, clock=None, gate_url_re=None):
        import time
        self.home, self.player, self.clock = home, player, clock or time.time
        self.gate_url_re = gate_url_re or GATE_URL      # tests and the dev harness hand in a pattern for a gate on this machine; production never does

    def gate_url(self) -> str | None:
        info = ((self.home.report or {}).get("capabilities") or {}).get("book_gate")
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
        if not url or not self.home.can("book_gate"):
            return {"ok": False, "why": "skippy-pc is up, but its audiobook gate isn't answering."}
        exp = (int(self.clock()) // PASS_BUCKET_S + 1) * PASS_BUCKET_S + PASS_S
        return {"ok": True, "url": url, "pass": sign(gate_key(stored), exp), "exp": exp}

    def rooms(self, user: str) -> list[dict]:
        """The room boxes that can play a book now (the hub checked them in), with what each one's book is doing."""
        out = []
        for d in self.player.listing(user):
            if d["room"].startswith("box-"):
                out.append({"id": d["id"], "name": d["name"], "live": d["live"], "age": d["age"], "book": d["state"].get("book")})
        return out

    def command(self, user: str, device: str, action: str) -> bool:
        """Queue a `book:` command for a room box. Only those: this route never presses the music's own keys."""
        return bool(player_mod.BOX_DEVICE.fullmatch(device) and player_mod.BOOK_CMD.fullmatch(action)) and self.player.command(user, device, action)
