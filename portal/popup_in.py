"""Big files for pop-ups (docs/popups.md "Big files"): a pass for skippy-pc's popup-gate, so a file too big to ride in a chat message
(a whole track to split into stems) goes from the browser straight home, never through the portal.

The gate (services/popup-gate/gate.py) is on the internet through Tailscale Funnel, :10000/in. A pass is good for ONE file: its id (made
here, 32 hex, so nobody can guess another's), the most bytes it may have, and whose it is (chat_history.owner_id). The key comes from the
home token's sha256 with its own label, like the music, video and voice gates', so no other gate's pass opens this one. The home reporter
says where the gate is and whether it is ready ("popup_gate"), and checks the owner again when a job uses the file.

Guests may send DAILY_GUEST_BYTES a day (the Captain no limit): a pass spends its bytes when it is signed. The count lives in this
process (the portal runs one instance), so a redeploy forgives the day.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time

import chat_history

MAX_BYTES = 64 * 1024 * 1024            # one file (popup-gate MAX_BYTES; popups.MAX_INPUT_MB)
DAILY_GUEST_BYTES = 200 * 1024 * 1024   # a guest's files a day, all together (the Captain, 2026-10-29)
PASS_S = 3600                           # long enough for a slow phone upload of 64 MB
GATE_URL = re.compile(r"https://[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net(:10000)?/in")


def gate_key(token_sha256: str) -> bytes:
    """Must match services/popup-gate/gate.py gate_key()."""
    return hmac.new(token_sha256.encode(), b"popup-gate-v1", hashlib.sha256).digest()


def sign(key: bytes, exp: int, file_id: str, max_bytes: int, owner: str) -> str:
    """Must match services/popup-gate/gate.py sign()."""
    text = f"popup-in:{exp}:{file_id}:{max_bytes}:{owner}"
    return f"{exp}.{file_id}.{max_bytes}.{owner}.{hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:32]}"


class PopupIn:
    def __init__(self, home, clock=time.time, gate_url_re=None):
        self.home, self.clock = home, clock
        self.gate_url_re = gate_url_re or GATE_URL
        self._lock = threading.Lock()
        self._spent: dict[str, tuple[int, int]] = {}     # owner id -> (day, bytes passed that day)

    def gate_url(self) -> str | None:
        info = ((self.home.report or {}).get("capabilities") or {}).get("popup_gate")
        url = info.get("url") if isinstance(info, dict) else None
        return url if isinstance(url, str) and self.gate_url_re.fullmatch(url) else None

    def left_today(self, email: str, is_captain: bool) -> int:
        if is_captain:
            return MAX_BYTES
        day = int(self.clock() // 86400)
        with self._lock:
            spent_day, spent = self._spent.get(chat_history.owner_id(email), (day, 0))
        return max(0, DAILY_GUEST_BYTES - (spent if spent_day == day else 0))

    def grant(self, email: str, is_captain: bool, size) -> dict:
        """A pass for one file of `size` bytes, or {"ok": False, "why"} in one plain sentence."""
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            return {"ok": False, "why": "Say how big the file is."}
        if size > MAX_BYTES:
            return {"ok": False, "why": f"That file is over {MAX_BYTES // (1024 * 1024)} MB, the most one can be."}
        stored = self.home._stored_hash()
        if not stored or not self.home.online():
            return {"ok": False, "why": "skippy-pc isn't reporting in, so big files can't go home right now."}
        url = self.gate_url()
        if not url or not self.home.can("popup_gate"):
            return {"ok": False, "why": "skippy-pc is up, but its door for big files isn't answering."}
        owner, day = chat_history.owner_id(email), int(self.clock() // 86400)
        with self._lock:
            if not is_captain:
                spent_day, spent = self._spent.get(owner, (day, 0))
                spent = spent if spent_day == day else 0
                if spent + size > DAILY_GUEST_BYTES:
                    return {"ok": False, "why": f"That's today's {DAILY_GUEST_BYTES // (1024 * 1024)} MB of big files used up; "
                                                "it resets tomorrow."}
                self._spent[owner] = (day, spent + size)
        file_id, exp = secrets.token_hex(16), int(self.clock()) + PASS_S
        return {"ok": True, "url": url, "id": file_id, "max": size,
                "pass": sign(gate_key(stored), exp, file_id, size, owner)}
