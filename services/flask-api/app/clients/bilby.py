"""Bilby music service client for SkippyTel.

Mac-side Bilby is stage9_dj_suggest.py, running on port 7334 at
`skippy-mac` (Tailscale MagicDNS). SkippyTel polls its
/api/bilby-status endpoint and surfaces the result as a Now Playing
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
    """Poll Mac Bilby's /api/bilby-status.

    Returns the parsed JSON dict on success, or None on any error
    (Mac offline, Traktor not running, timeout, etc.).
    """
    url = f"{settings.bilby_mac_url}/api/bilby-status"
    try:
        r = httpx.get(url, timeout=_TIMEOUT)
        if r.is_success:
            return r.json()
    except Exception:
        pass
    return None
