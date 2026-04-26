"""
SkippyTel — Mac Bilby UI proxy

Forwards requests to Mac Bilby's Flask web UI (http://skippy-mac:7334).

Three endpoints:

  GET /bilby/ui
      Full Mac Bilby passthrough — for the Captain's glasses / phone only.
      Streams the raw HTML from Mac Bilby unchanged.

  GET /bilby/ui/public
      Audience-safe display URL. All sanitization happens HERE on SkippyTel,
      not on Mac Bilby — so we own and control exactly what leaves the wire.

      Stripped server-side:
        • All <script> tags (no JS reaches the client)
        • All interactive elements: <form>, <input>, <button>, <select>,
          <textarea> — prompt input, shuffle, skip, nothing
        • Elements with inline event handlers (onclick, onchange, etc.)
        • Reputation / content tag badges (.rep-badge, .content-flag,
          .lyric-flags, .deck-controls, .prompt-box and related selectors)
        • All HTML comments (may carry template debug info)
        • All data-* attributes (may carry internal state)
        • <meta> tags except charset and viewport
        • <link> rel=prefetch/preload/dns-prefetch (fingerprinting vectors)
        • Injected CSS override at end of <head> as a final belt-and-suspenders
          layer: hides any control-flavoured elements that slipped through

      NOT stripped:
        • <style> tags — visual CSS is fine; browsers don't execute CSS
        • The now-playing display: artist, title, BPM, key, karaoke line

  GET /bilby/karaoke
      Proxy to Mac Bilby /api/karaoke.
      Returns {"line", "next_line", "elapsed_pct"}.
      Always 200; null fields when Mac Bilby is offline.

If Mac Bilby is unreachable → 503 JSON for UI routes, graceful null for karaoke.
"""
import requests
from bs4 import BeautifulSoup, Comment
from flask import Blueprint, Response, jsonify, stream_with_context

from app.config import settings

bp = Blueprint("bilby_proxy", __name__)

_TIMEOUT_UI      = 3.0   # seconds — page fetch; Tailscale should be fast
_TIMEOUT_KARAOKE = 1.0   # seconds — fail fast, karaoke is optional


# ── Inline event-handler attributes to strip ─────────────────────────────────
_EVENT_ATTRS = {
    "onabort", "onanimationend", "onanimationiteration", "onanimationstart",
    "onblur", "oncanplay", "oncanplaythrough", "onchange", "onclick",
    "oncontextmenu", "ondblclick", "ondrag", "ondragend", "ondragenter",
    "ondragleave", "ondragover", "ondragstart", "ondrop", "ondurationchange",
    "onemptied", "onended", "onerror", "onfocus", "onformdata",
    "oninput", "oninvalid", "onkeydown", "onkeypress", "onkeyup",
    "onload", "onloadeddata", "onloadedmetadata", "onloadstart",
    "onmousedown", "onmouseenter", "onmouseleave", "onmousemove",
    "onmouseout", "onmouseover", "onmouseup", "onpause", "onplay",
    "onplaying", "onprogress", "onratechange", "onreset", "onresize",
    "onscroll", "onseeked", "onseeking", "onselect", "onstalled",
    "onsubmit", "onsuspend", "ontimeupdate", "ontoggle",
    "ontransitionend", "onvolumechange", "onwaiting", "onwheel",
}

# ── CSS class fragments that flag control / rep-tag elements ─────────────────
# Any element whose class list contains ANY of these substrings is removed.
_CONTROL_CLASS_FRAGMENTS = (
    "rep-badge",
    "lyric-flag",
    "content-flag",
    "deck-control",
    "prompt",
    "shuffle",
    "skip-btn",
    "next-btn",
    "prev-btn",
    "vote",
    "flag-btn",
    "control-bar",
    "tag-badge",
    "badge--convicted",
    "badge--accused",
    "badge--settled",
    "badge--racism",
    "badge--bigotry",
    "badge--sexual",
    "badge--instrumental",
    "badge--explicit",
)

# ── CSS injected at end of <head> — final belt-and-suspenders layer ───────────
_PUBLIC_CSS_OVERRIDE = """
<style id="skippy-public-sanitizer">
/* SkippyTel public proxy — any control that survived the DOM pass is hidden */
button, input, select, textarea, form,
[onclick], [onchange], [onsubmit], [onkeydown],
.rep-badge, .lyric-flags, .content-flag, .deck-controls,
.prompt, .prompt-box, .shuffle-btn, .skip-btn,
.next-btn, .prev-btn, .vote, .flag-btn, .control-bar, .tag-badge,
[class*="badge--convicted"], [class*="badge--accused"],
[class*="badge--settled"], [class*="badge--racism"],
[class*="badge--bigotry"], [class*="badge--sexual"],
[class*="badge--instrumental"], [class*="badge--explicit"] {
    display: none !important;
    pointer-events: none !important;
    visibility: hidden !important;
}
/* Reclaim badge-row space — bump artist/title text to fill the gap */
.an, .artist-name, .track-title, .now-playing-title {
    font-size: 125% !important;
}
</style>
"""


def _sanitize_for_public(html: str) -> str:
    """
    Parse `html`, strip all interactive and metadata-carrying elements,
    and return clean read-only HTML safe for public display.

    Sanitization layers:
      1. Remove all <script> tags
      2. Remove interactive form controls
      3. Remove elements with inline event handlers
      4. Remove elements with control/rep-tag class fragments
      5. Strip data-* attributes from every element
      6. Strip HTML comments
      7. Remove non-essential <meta> tags
      8. Remove prefetch/preload <link> hints
      9. Inject _PUBLIC_CSS_OVERRIDE into <head> as final backstop
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1. No JavaScript reaches the public client.
    for tag in soup.find_all("script"):
        tag.decompose()

    # 2. Interactive controls gone.
    for tag_name in ("form", "input", "button", "select", "textarea"):
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # 3. Any element still carrying an inline event handler.
    for tag in soup.find_all(True):
        for attr in _EVENT_ATTRS:
            if tag.has_attr(attr):
                tag.decompose()
                break

    # 4. Elements whose class list matches known control/rep-tag fragments.
    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class", []))
        if any(f in classes.lower() for f in _CONTROL_CLASS_FRAGMENTS):
            tag.decompose()

    # 5. Strip data-* attributes — can carry track IDs, rep scores, etc.
    for tag in soup.find_all(True):
        data_attrs = [a for a in list(tag.attrs) if a.startswith("data-")]
        for attr in data_attrs:
            del tag[attr]

    # 6. HTML comments may carry template debug info or internal flags.
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # 7. Strip <meta> except charset and viewport.
    for tag in soup.find_all("meta"):
        charset = tag.get("charset")
        name    = (tag.get("name") or "").lower()
        http_eq = (tag.get("http-equiv") or "").lower()
        if charset or name == "viewport" or http_eq == "content-type":
            continue
        tag.decompose()

    # 8. <link> hints that reveal site structure or enable fingerprinting.
    for tag in soup.find_all("link"):
        rel = " ".join(tag.get("rel", [])).lower()
        if any(r in rel for r in ("prefetch", "preload", "dns-prefetch", "preconnect")):
            tag.decompose()

    # 9. Final backstop: inject override CSS before </head>.
    head = soup.find("head")
    if head:
        head.append(BeautifulSoup(_PUBLIC_CSS_OVERRIDE, "html.parser"))

    return str(soup)


@bp.get("/bilby/ui")
def bilby_ui():
    """
    Full Mac Bilby passthrough — Captain's eyes only (phone / glasses).
    Raw HTML streamed from Mac Bilby without modification.
    """
    mac_url = (settings.bilby_mac_url or "").rstrip("/")
    if not mac_url:
        return jsonify({
            "error": "BILBY_MAC_URL not configured",
            "hint":  "Set BILBY_MAC_URL=http://skippy-mac:7334 in .env",
        }), 503

    try:
        upstream = requests.get(
            f"{mac_url}/",
            timeout=_TIMEOUT_UI,
            stream=True,
            headers={"Accept": "text/html"},
        )
        upstream.raise_for_status()
    except requests.exceptions.RequestException as exc:
        return jsonify({"error": "Mac Bilby unreachable", "detail": str(exc)}), 503

    content_type = upstream.headers.get("Content-Type", "text/html; charset=utf-8")
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=8192)),
        status=upstream.status_code,
        content_type=content_type,
    )


@bp.get("/bilby/ui/public")
def bilby_ui_public():
    """
    Audience-safe Bilby display — safe for venue screens or public URLs.

    Fetches Mac Bilby's full HTML and sanitizes it completely server-side.
    The client receives only the now-playing display with zero interactivity,
    zero tags, and zero metadata.
    """
    mac_url = (settings.bilby_mac_url or "").rstrip("/")
    if not mac_url:
        return jsonify({
            "error": "BILBY_MAC_URL not configured",
            "hint":  "Set BILBY_MAC_URL=http://skippy-mac:7334 in .env",
        }), 503

    try:
        resp = requests.get(
            f"{mac_url}/",
            timeout=_TIMEOUT_UI,
            headers={"Accept": "text/html"},
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        return jsonify({
            "error":  "Mac Bilby unreachable",
            "detail": str(exc),
            "hint":   f"Is {mac_url} up and on Tailscale?",
        }), 503

    clean = _sanitize_for_public(resp.text)
    return Response(clean, status=200, content_type="text/html; charset=utf-8")


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
