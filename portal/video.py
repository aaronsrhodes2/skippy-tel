"""Nagatha's Video Service: the Captain's Jellyfin library (skippy-pc) in any browser. The sibling of Bilby's Audio Service.

The video never passes through here. skippy-pc's video-gate (services/video-gate/gate.py) streams the films to the internet
through Tailscale Funnel (port 8443 of the same hostname as the music gate, which has 443), and only for a pass this module
signs. The key comes from the home token's sha256, which this portal already keeps in VM metadata, so there is no extra
secret; the LABEL and the signed text differ from the music gate's, so a music pass never opens the video gate and a video
pass never opens the music gate. The home reporter says where the gate is and whether it is ready (capability "video_gate").

No "Connect" here (unlike the music player): where a film was left is Jellyfin's own resume point, which the player writes
through the gate, so any device (and Jellyfin's own apps) already picks up where another left off.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time

NAME = "Nagatha's Video Service"
SHORT_NAME = "Nagatha"
PASS_S = 8 * 3600             # a film night at least; the player asks again before it runs out (and on a 403)
PASS_BUCKET_S = 6 * 3600      # exp on a six-hour grid: every pass handed out inside one bucket is the SAME string, and the pass
                              # is in the URL path, so the browser's cache of posters (the gate says a week) survives a reload
# The gate is on the tailnet's Funnel: https only, a *.ts.net name, and no port or the one Funnel port it uses (8443).
# Anything else a report says is never signed for: a pass sent to another host would be a credential handed away.
GATE_URL = re.compile(r"https://[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net(:8443)?")


def gate_key(token_sha256: str) -> bytes:
    """Must match services/video-gate/gate.py gate_key(): HMAC of the video label under the home token's sha256 (hex)."""
    return hmac.new(token_sha256.encode(), b"video-gate-v1", hashlib.sha256).digest()


def sign(key: bytes, exp: int, scope: str = "rw") -> str:
    """Must match services/video-gate/gate.py sign(): "rw" for the Captain (may write Jellyfin's watch state), "ro" for a
    friend with the "video" grant (watches only). Different signed text, so a friend's pass is a different string too
    (and the gate's transcoder sessions, keyed by pass, are never shared with the Captain's)."""
    text = f"video:{exp}" if scope == "rw" else f"video-ro:{exp}"
    return f"{exp}.{hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:32]}"


class Video:
    def __init__(self, home, clock=time.time, gate_url_re=None):
        self.home, self.clock = home, clock
        # tests/dev_video.py hands in a pattern for the gate on this machine (http://127.0.0.1:3006). Production never does:
        # create_app builds this with the default, so a gate off the tailnet is never signed for.
        self.gate_url_re = gate_url_re or GATE_URL

    def gate_url(self) -> str | None:
        info = ((self.home.report or {}).get("capabilities") or {}).get("video_gate")
        url = info.get("url") if isinstance(info, dict) else None
        if not isinstance(url, str):
            return None
        url = url.rstrip("/")
        return url if self.gate_url_re.fullmatch(url) else None

    def gate(self, read_only: bool = False) -> dict:
        """Where the library is and a pass for it, or why it can't be watched right now (one plain sentence)."""
        stored = self.home._stored_hash()
        if not stored:
            return {"ok": False, "why": "skippy-pc isn't paired with the portal."}
        if not self.home.online():
            return {"ok": False, "why": "skippy-pc isn't reporting in, so the film library is out of reach."}
        url = self.gate_url()
        if not url or not self.home.can("video_gate"):
            return {"ok": False, "why": "skippy-pc is up, but its video gate (or Jellyfin behind it) isn't answering."}
        exp = (int(self.clock()) // PASS_BUCKET_S + 1) * PASS_BUCKET_S + PASS_S      # 8 to 14 hours from now: see PASS_BUCKET_S
        return {"ok": True, "url": url, "pass": sign(gate_key(stored), exp, "ro" if read_only else "rw"), "exp": exp}
