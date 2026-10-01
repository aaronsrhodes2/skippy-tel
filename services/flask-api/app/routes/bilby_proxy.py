"""
SkippyTel — Mac Bilby karaoke proxy (data only)

  GET /bilby/karaoke
      Proxy to Mac Bilby's /api/karaoke.
      Returns {"line", "next_line", "elapsed_pct"}.
      Always 200; null fields when Mac Bilby is offline.

This is the lyric-line DATA feed that SkippyChat/SkippyDroid's BilbyModule
polls every 500 ms (the shelved phone apps). It used to share this module with
the HTML interface routes (/bilby/ui, /bilby/ui/public, a full Mac-UI proxy and
its sanitizer); skippy core no longer serves any HTML, so those went away and
the karaoke JSON stayed.
"""
import requests
from flask import Blueprint, jsonify

from app.config import settings

bp = Blueprint("bilby_proxy", __name__)

_TIMEOUT_KARAOKE = 1.0   # seconds — fail fast, karaoke is optional


@bp.get("/bilby/karaoke")
def bilby_karaoke():
    """
    Proxy to Mac Bilby's GET /api/karaoke.

    Returns:
      { "line": str|null, "next_line": str|null, "elapsed_pct": float }

    Always returns 200 — null fields when Mac Bilby is offline.
    BilbyModule in SkippyDroid degrades gracefully.
    """
    _EMPTY = {"line": None, "next_line": None, "elapsed_pct": 0.0}

    mac_url = (settings.bilby_mac_url or "").rstrip("/")
    if not mac_url:
        return jsonify(_EMPTY)

    try:
        resp = requests.get(f"{mac_url}/api/karaoke", timeout=_TIMEOUT_KARAOKE)
        if not resp.ok:
            return jsonify(_EMPTY)
        data = resp.json()
        return jsonify({
            "line":        data.get("line"),
            "next_line":   data.get("next_line"),
            "elapsed_pct": float(data.get("elapsed_pct", 0.0)),
        })
    except Exception:
        return jsonify(_EMPTY)
