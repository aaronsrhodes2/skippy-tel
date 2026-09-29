"""Bilby music service client for SkippyTel.

Mac-side Bilby is stage9_dj_suggest.py, running on port 7334 at
`skippy-mac` (Tailscale MagicDNS). SkippyTel polls its
/bilby/status endpoint and surfaces the result as a Now Playing
card on the zPhone.

Two modes handled by the /bilby/* route layer:
  source=traktor  — proxy to Mac Bilby daemon (Traktor is playing)
  source=drive    — SkippyTel streams from Google Drive directly
                    (placeholder: returns 503 until Drive is wired)

This module handles the `source=traktor` path only. Drive streaming
lives in the bilby route once the Music folder ID is confirmed.
"""
import httpx

from app.config import settings

# Timeout for polling the Mac Bilby daemon.
# 3s connect / 4s read — Mac may be asleep; fail fast so the phone
# doesn't block. Caller should handle None gracefully.
_TIMEOUT = httpx.Timeout(connect=3.0, read=4.0, write=4.0, pool=4.0)


def now_playing() -> dict | None:
    """Poll Mac Bilby's /bilby/status.

    Returns the route layer's shape ({traktor_alive, playing_deck, loaded,
    now_playing}) on success, or None on any error (Mac offline, Traktor not
    running, timeout, etc.). now_playing carries the Mac's full detail
    (bpm, key, genre, duration_s, elapsed_s, art_url…) when a deck is playing.
    """
    url = f"{settings.bilby_mac_url}/bilby/status"
    try:
        r = httpx.get(url, timeout=_TIMEOUT)
        if r.is_success:
            return normalize(r.json())
    except Exception:
        pass
    return None


def normalize(data: dict) -> dict:
    now = data.get("now_playing")
    deck = (now or {}).get("deck")
    if not deck:
        deck = "a" if data.get("playing_a") else "b" if data.get("playing_b") else None
    return {
        "traktor_alive": bool(data.get("online")),
        "playing_deck": deck,
        "loaded": {"a": data.get("deck_a"), "b": data.get("deck_b")},
        "now_playing": now,
    }
