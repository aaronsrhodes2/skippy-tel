"""
SkippyTel — Mac Bilby UI proxy

Forwards requests to Mac Bilby's Flask web UI (http://skippy-mac:7334).

Three endpoints:
  GET /bilby/ui           → full Mac Bilby UI (passthrough theme — glasses-optimised)
  GET /bilby/ui/public    → public-facing UI: passthrough theme with all content/
                            reputation tags stripped, larger artist/title text.
                            Safe to share as an audience display URL at live shows.
  GET /bilby/karaoke      → proxy to Mac Bilby /api/karaoke.
                            Returns {"line", "next_line", "elapsed_pct"}.
                            Returns {"line": null, ...} silently if Mac Bilby offline —
                            BilbyModule in SkippyDroid degrades gracefully to no lyrics.

If Mac Bilby is unreachable (offline, no Tailscale) → 503 for UI routes, graceful
null for karaoke (to avoid breaking BilbyModule's poll loop).
Assets (CSS, JS, images) embedded in stage9_dj_suggest.py's HTML output, so a
single HTML response is sufficient; no separate asset proxy needed.
"""

import requests
from flask import Blueprint, Response, jsonify, stream_with_context

from app.config import settings

bp = Blueprint("bilby_proxy", __name__)

_TIMEOUT = 2.0   # seconds — Mac Bilby on Tailscale should respond well within this


def _proxy_mac_bilby(params: dict | None = None) -> Response:
    """
    Fetch Mac Bilby's HTML and stream it back to the caller.
    Returns a Flask Response or a 503 JSON error if unreachable.
    """
    mac_url = (settings.bilby_mac_url or "").rstrip("/")
    if not mac_url:
        return jsonify({
            "error": "BILBY_MAC_URL not configured",
            "hint":  "Set BILBY_MAC_URL=http://skippy-mac:7334 in .env",
        }), 503

    target = f"{mac_url}/"
    try:
        upstream = requests.get(
            target,
            params   = params or {},
            timeout  = _TIMEOUT,
            stream   = True,
            headers  = {"Accept": "text/html"},
        )
        upstream.raise_for_status()
    except requests.exceptions.RequestException as exc:
        return jsonify({
            "error":  "Mac Bilby unreachable",
            "detail": str(exc),
            "hint":   f"Is {mac_url} up and on Tailscale?",
        }), 503

    content_type = upstream.headers.get("Content-Type", "text/html; charset=utf-8")
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=8192)),
        status       = upstream.status_code,
        content_type = content_type,
    )


@bp.get("/bilby/ui")
def bilby_ui():
    """
    Full Mac Bilby passthrough UI — for the Captain's glasses / phone.
    Includes all deck info, content flags, and BPM/key data.
    """
    return _proxy_mac_bilby()


@bp.get("/bilby/ui/public")
def bilby_ui_public():
    """
    Public-safe Mac Bilby UI — for audience display at live shows.
    Passes ?public=1 to Mac Bilby which renders the passthrough theme
    with reputation and content tags hidden, and larger artist/title text.
    """
    return _proxy_mac_bilby(params={"public": "1"})


@bp.get("/bilby/karaoke")
def bilby_karaoke():
    """
    Proxy to Mac Bilby's GET /api/karaoke.

    Returns:
      { "line": str|null, "next_line": str|null, "elapsed_pct": float }

    Always returns 200 — returns null line when Mac Bilby is offline or
    karaoke data is unavailable. BilbyModule degrades gracefully to no lyrics.
    """
    _EMPTY = {"line": None, "next_line": None, "elapsed_pct": 0.0}

    mac_url = (settings.bilby_mac_url or "").rstrip("/")
    if not mac_url:
        return jsonify(_EMPTY)

    try:
        resp = requests.get(
            f"{mac_url}/api/karaoke",
            timeout = 1.0,   # fail fast — karaoke is enhancement only
        )
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
