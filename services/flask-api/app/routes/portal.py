"""
SkippyTel Diagnostic Portal — derived monitoring service for zPhone.

Endpoints:
  GET  /portal                        — single-page HTML portal
  GET  /portal/history                — last N chat messages (JSON)
  GET  /portal/events                 — SSE stream of new messages
  GET  /portal/image/<id>             — base64 image for a message

  POST /portal/hud-state              — zHUD pushes HudFeed JSON here
  GET  /portal/hud-state              — current HudFeed snapshot
  GET  /portal/hud-state/events       — SSE stream of HudFeed deltas

  POST /portal/chat-state             — SkippyChat pushes ChatFeed JSON here
  GET  /portal/chat-state             — current ChatFeed snapshot
  GET  /portal/chat-state/events      — SSE stream of ChatFeed deltas

  POST /portal/droid-state            — DEPRECATED back-compat alias for hud-state
  GET  /portal/droid-state            — DEPRECATED — reads from _hud_state

  GET  /portal/hud                    — DEPRECATED legacy combined snapshot
                                        (kept until portal _HTML drops it)

Architecture:
  Source of truth = the phone (zPhone software running on emulator/S23).
  This module just receives state pushes, caches them, and fans out to
  any connected portal SSE subscribers.

Internal-network only (Tailscale / LAN). No authentication.
"""
import json
import queue as _queue
import threading
import time
from typing import Optional

import httpx
from flask import Blueprint, Response, jsonify, request, stream_with_context

from app import conversation_store as cs
from app.routes.ups    import UPS_MONITOR_URL
from app.config        import settings

bp = Blueprint("portal", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# State stores — full HudFeed + ChatFeed shapes (the contract with the phone).
# ─────────────────────────────────────────────────────────────────────────────

# Default HudFeed shape — every field None/empty until zHUD pushes.
_hud_state: dict = {
    "ts":         None,    # epoch ms of last push
    "corners": {
        "tl": {"row1": "", "row2": "", "color": "green", "blink": False, "hidden": True},
        "tr": {"row1": "", "row2": "", "color": "green", "blink": False, "hidden": True},
        "bl": {"row1": "", "row2": "", "color": "green", "blink": False, "hidden": True},
        "br": {"row1": "", "row2": "", "color": "green", "blink": False, "hidden": True},
    },
    "top_strip":  [],      # list of {"text": "...", "color": "green"}, max 5 chars each
    "bot_strip":  [],
    "left_bar":   [],      # list of {"icon": "·", "color": "green"}
    "right_bar":  [],
    "viewport":   {"mode": "idle", "content": None},   # "idle" | "streaming"
    "shades":     None,    # True = shades on, False = eyes open
    "fullscreen": None,    # service id when zHUD is hidden by fullscreen module
    "battery":    None,    # 0–100
    "locked":     None,    # touch-lock overlay state
}

# Default ChatFeed shape — empty defaults until SkippyChat pushes.
_chat_state: dict = {
    "ts":        None,
    "messages":  [],       # last N — full source of truth lives in conversation_store
    "draft":     {"text": "", "mode": "VIBE"},
    "tts_mode":  "ANDROID",
    "muted":     False,
    "header":    {"music_status": "none", "track": None},
    # Hardpoint candy state — SkippyChat pushes every ~5 s.
    # Portal uses this to render candy overlays without physical glasses.
    "candy": {
        "tl": {"row1": "●", "row2": "SKIPPY", "color": "dim", "hidden": False},
        "tr": {"nagatha": False, "claude": False, "grumpy": False, "color": "dim", "hidden": False},
        "bl": {"row1": "",    "row2": "",       "color": "dim", "hidden": True},
        "br": {"row1": "—",   "row2": "",       "color": "amber","hidden": False},
        "predator_mode": "NONE",
        "translation":   "",
    },
}

# Subscriber queues for SSE fan-out — separate per surface.
_hud_subscribers:  list[_queue.Queue] = []
_chat_subscribers: list[_queue.Queue] = []
_subs_lock = threading.Lock()

# ── zHUD MJPEG mirror — latest JPEG frame pushed by the phone ───────────────
# zHUD captures its Compose root every ~200ms, encodes JPEG, POSTs the bytes
# to /portal/hud-frame.  /portal/hud-stream serves them as MJPEG so any
# browser shows the live render in an <img> with zero JS.
_hud_frame: bytes = b""
_hud_frame_ts: int = 0
_hud_frame_subscribers: list[_queue.Queue] = []   # signaled (empty payload) on new frame


def _fanout(subs: list[_queue.Queue], payload: str) -> None:
    """Push a JSON string to all live subscribers; drop dead ones."""
    dead: list[_queue.Queue] = []
    with _subs_lock:
        for q in subs:
            try:
                q.put_nowait(payload)
            except _queue.Full:
                dead.append(q)
        for q in dead:
            try: subs.remove(q)
            except ValueError: pass


def _subscribe(subs: list[_queue.Queue]) -> _queue.Queue:
    q: _queue.Queue = _queue.Queue(maxsize=64)
    with _subs_lock:
        subs.append(q)
    return q


def _unsubscribe(subs: list[_queue.Queue], q: _queue.Queue) -> None:
    with _subs_lock:
        try: subs.remove(q)
        except ValueError: pass


def _merge(target: dict, body: dict) -> None:
    """Recursively merge incoming body into target dict, preserving keys not
    pushed at every nesting level. So a caller can push, e.g.:
        {"corners": {"tl": {"blink": true}}}
    and only the tl.blink field changes — the other tl fields and other
    corners are untouched."""
    for k, v in body.items():
        if k in target and isinstance(target[k], dict) and isinstance(v, dict):
            _merge(target[k], v)
        else:
            target[k] = v


# Legacy alias kept until the old _HTML stops reading it.
_droid_state = _hud_state

# ── Palette (mirrors ChatPalette.kt exactly) ──────────────────────────────────
_BG          = "#000000"
_GREEN       = "#00ff88"
_DIM_GREEN   = "#003322"
_DIM_GRN_HI  = "#006644"
_WHITE       = "#ffffff"
_AMBER       = "#ffaa00"
_CYAN        = "#00ccff"
_VIOLET      = "#aa44ff"
_RED         = "#ff4444"

# ── /portal/history ───────────────────────────────────────────────────────────

@bp.get("/portal/history")
def portal_history():
    limit = min(int(request.args.get("limit", 100)), 500)
    return jsonify(cs.get_recent(limit))


# ── /portal/events (SSE) ─────────────────────────────────────────────────────

@bp.get("/portal/events")
def portal_events():
    def stream():
        q = cs.subscribe()
        try:
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield f"data: {msg}\n\n"
                except _queue.Empty:
                    # keep-alive ping so nginx / browser doesn't close the connection
                    yield 'data: {"type":"ping"}\n\n'
        except GeneratorExit:
            pass
        finally:
            cs.unsubscribe(q)

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── /portal/image/<id> ────────────────────────────────────────────────────────

@bp.get("/portal/image/<int:msg_id>")
def portal_image(msg_id: int):
    b64 = cs.get_image_b64(msg_id)
    if not b64:
        return jsonify({"error": "not_found"}), 404
    # Strip data-URL prefix if present; return as JSON for the JS to render
    if b64.startswith("data:"):
        return jsonify({"data_url": b64})
    return jsonify({"data_url": f"data:image/png;base64,{b64}"})


# ── /portal/hud ───────────────────────────────────────────────────────────────

@bp.get("/portal/hud")
def portal_hud():
    """Lightweight HUD snapshot used to seed the right-panel on first paint."""
    ups_data: dict = {}
    bilby_data: dict = {}

    try:
        r = httpx.get(f"{UPS_MONITOR_URL}/status", timeout=2.0)
        ups_data = r.json()
    except Exception:
        ups_data = {"error": "ups_offline"}

    try:
        r = httpx.get(f"http://localhost:{settings.port if hasattr(settings,'port') else 5000}/bilby/status", timeout=2.0)
        bilby_data = r.json()
    except Exception:
        bilby_data = {"source": "none"}

    return jsonify({
        "ts":    int(time.time() * 1000),
        "ups":   ups_data,
        "bilby": bilby_data,
        "droid": _droid_state,
    })


# ── /portal/hud-state — zHUD HudFeed publisher endpoint ─────────────────────

@bp.post("/portal/hud-state")
def portal_hud_state_post():
    """zHUD (and SkippyChat for shades/battery/locked) pushes HudFeed deltas here.
    Body: any subset of HudFeed fields. Nested dicts (e.g. 'corners') merge one
    level deep so a caller can update one corner without wiping the others.
    """
    body = request.get_json(silent=True) or {}
    body["ts"] = int(time.time() * 1000)
    _merge(_hud_state, body)
    _fanout(_hud_subscribers, json.dumps(_hud_state))
    return jsonify({"ok": True, "state": _hud_state})


@bp.get("/portal/hud-state")
def portal_hud_state_get():
    return jsonify(_hud_state)


@bp.get("/portal/hud-state/events")
def portal_hud_state_events():
    def stream():
        q = _subscribe(_hud_subscribers)
        # First message: send current snapshot so new clients hydrate immediately.
        yield f"data: {json.dumps(_hud_state)}\n\n"
        try:
            while True:
                try:
                    payload = q.get(timeout=25)
                    yield f"data: {payload}\n\n"
                except _queue.Empty:
                    yield 'data: {"type":"ping"}\n\n'
        except GeneratorExit:
            pass
        finally:
            _unsubscribe(_hud_subscribers, q)

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── /portal/chat-state — SkippyChat ChatFeed publisher endpoint ─────────────

@bp.post("/portal/chat-state")
def portal_chat_state_post():
    body = request.get_json(silent=True) or {}
    body["ts"] = int(time.time() * 1000)
    _merge(_chat_state, body)
    _fanout(_chat_subscribers, json.dumps(_chat_state))
    return jsonify({"ok": True, "state": _chat_state})


@bp.get("/portal/chat-state")
def portal_chat_state_get():
    return jsonify(_chat_state)


@bp.get("/portal/chat-state/events")
def portal_chat_state_events():
    def stream():
        q = _subscribe(_chat_subscribers)
        yield f"data: {json.dumps(_chat_state)}\n\n"
        try:
            while True:
                try:
                    payload = q.get(timeout=25)
                    yield f"data: {payload}\n\n"
                except _queue.Empty:
                    yield 'data: {"type":"ping"}\n\n'
        except GeneratorExit:
            pass
        finally:
            _unsubscribe(_chat_subscribers, q)

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── /portal/hud-frame — zHUD posts its current rendered JPEG here ──────────

@bp.post("/portal/hud-frame")
def portal_hud_frame_post():
    """zHUD POSTs raw JPEG bytes (Content-Type: image/jpeg) every ~200ms.
    We cache the latest frame and signal MJPEG subscribers."""
    global _hud_frame, _hud_frame_ts
    raw = request.get_data(cache=False)
    if not raw:
        return jsonify({"error": "empty"}), 400
    _hud_frame = raw
    _hud_frame_ts = int(time.time() * 1000)
    # Signal all MJPEG subscribers (empty token — they re-read _hud_frame)
    with _subs_lock:
        dead = []
        for q in _hud_frame_subscribers:
            try: q.put_nowait(b"")
            except _queue.Full: dead.append(q)
        for q in dead:
            try: _hud_frame_subscribers.remove(q)
            except ValueError: pass
    return jsonify({"ok": True, "ts": _hud_frame_ts, "bytes": len(raw)})


@bp.get("/portal/hud-frame.jpg")
def portal_hud_frame_get():
    """Single-shot accessor for the latest frame."""
    if not _hud_frame:
        return Response(b"", status=404)
    return Response(_hud_frame, mimetype="image/jpeg",
                    headers={"Cache-Control": "no-cache, no-store"})


@bp.get("/portal/hud-stream")
def portal_hud_stream():
    """MJPEG (multipart/x-mixed-replace) stream of the latest zHUD frames.
    Browsers consume this natively in <img src="..."> with no JS."""
    BOUNDARY = b"frame"

    def stream():
        q: _queue.Queue = _queue.Queue(maxsize=4)
        with _subs_lock:
            _hud_frame_subscribers.append(q)
        try:
            # Send current frame immediately (so a reconnect gets pixels right away)
            if _hud_frame:
                yield (b"--" + BOUNDARY + b"\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(_hud_frame)).encode() + b"\r\n\r\n"
                       + _hud_frame + b"\r\n")
            while True:
                try:
                    q.get(timeout=20)
                except _queue.Empty:
                    # Heartbeat — re-send last frame so the browser keeps the
                    # connection considered live; harmless if no-op.
                    if not _hud_frame:
                        continue
                if not _hud_frame:
                    continue
                yield (b"--" + BOUNDARY + b"\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(_hud_frame)).encode() + b"\r\n\r\n"
                       + _hud_frame + b"\r\n")
        except GeneratorExit:
            pass
        finally:
            with _subs_lock:
                try: _hud_frame_subscribers.remove(q)
                except ValueError: pass

    return Response(
        stream_with_context(stream()),
        mimetype=f"multipart/x-mixed-replace; boundary={BOUNDARY.decode()}",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                 "Pragma": "no-cache",
                 "X-Accel-Buffering": "no"},
    )


# ── /portal/droid-state — DEPRECATED back-compat alias for hud-state ────────

@bp.post("/portal/droid-state")
def portal_droid_state():
    """DEPRECATED. Old narrow shape (shades, viewport, battery, fullscreen, locked).
    Forwards to /portal/hud-state so existing SkippyChat builds keep working."""
    body = request.get_json(silent=True) or {}
    delta = {k: body[k] for k in ("shades", "viewport", "battery", "fullscreen", "locked") if k in body}
    if delta:
        delta["ts"] = int(time.time() * 1000)
        _merge(_hud_state, delta)
        _fanout(_hud_subscribers, json.dumps(_hud_state))
    return jsonify({"ok": True, "state": _hud_state})


@bp.get("/portal/droid-state")
def portal_droid_state_get():
    """DEPRECATED. Returns subset of _hud_state matching the legacy shape."""
    return jsonify({k: _hud_state.get(k) for k in
                    ("shades", "viewport", "battery", "fullscreen", "locked")})


# ── /portal ───────────────────────────────────────────────────────────────────

@bp.get("/portal")
def portal_page():
    return Response(_HTML, mimetype="text/html")


# ── HTML ──────────────────────────────────────────────────────────────────────

_HTML_LEGACY_REPLACED = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Skippy Portal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:        #000000;
  --green:     #00ff88;
  --dim-green: #003322;
  --dim-hi:    #006644;
  --white:     #ffffff;
  --amber:     #ffaa00;
  --cyan:      #00ccff;
  --violet:    #aa44ff;
  --red:       #ff4444;
  --font:      'Atkinson Hyperlegible', sans-serif;
  --mono:      'Courier New', monospace;
  --divider:   1px solid var(--dim-green);
}

html, body {
  height: 100%; background: var(--bg); color: var(--white);
  font-family: var(--font); font-size: 14px;
  overflow: hidden; user-select: none;
}

/* ── Root: two columns, no gap ─────────────────────────────────────────────── */
#root { display: flex; height: 100vh; }

/* ── LEFT: Chat feed ────────────────────────────────────────────────────────── */
#left {
  display: flex; flex-direction: column;
  width: 42%; min-width: 300px; max-width: 580px;
  border-right: var(--divider); overflow: hidden;
}

#feed-header {
  flex-shrink: 0;
  padding: 7px 14px; border-bottom: var(--divider);
  display: flex; align-items: center; gap: 8px;
  font-size: 11px; color: var(--dim-hi); letter-spacing: 0.08em;
}
#conn-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--dim-green); transition: background 0.4s;
}
#conn-dot.live { background: var(--green); }

/* Feed scrolls — only area on the left that does */
#feed {
  flex: 1; overflow-y: auto; padding: 10px 10px;
  display: flex; flex-direction: column; gap: 5px;
  scroll-behavior: smooth;
}
#feed::-webkit-scrollbar { width: 3px; }
#feed::-webkit-scrollbar-thumb { background: var(--dim-green); border-radius: 2px; }

#input-bar {
  flex-shrink: 0;
  display: flex; align-items: center; gap: 6px;
  padding: 7px 10px; border-top: var(--divider);
}
#msg-input {
  flex: 1; background: transparent; border: 1px solid var(--dim-green);
  border-radius: 3px; color: var(--white); font-family: var(--font);
  font-size: 14px; padding: 5px 9px; outline: none;
  transition: border-color 0.2s;
}
#msg-input:focus { border-color: var(--dim-hi); }
#msg-input::placeholder { color: var(--dim-hi); }

.btn {
  background: transparent; border: 1px solid var(--dim-green);
  border-radius: 3px; color: var(--dim-hi); cursor: pointer;
  font-family: var(--font); font-size: 14px; padding: 5px 9px;
  transition: border-color 0.15s, color 0.15s; flex-shrink: 0;
}
.btn:hover  { border-color: var(--green); color: var(--green); }
.btn.active { border-color: var(--amber); color: var(--amber); }
.btn:disabled { opacity: 0.3; cursor: default; }

/* ── Bubbles ────────────────────────────────────────────────────────────────── */
.bubble { display: flex; flex-direction: column; }
.bubble.user      { align-items: flex-end; }
.bubble.assistant { align-items: flex-start; }
.bubble.system    { align-items: center; }

.narration {
  font-size: 11px; font-style: italic; color: var(--dim-hi);
  margin-bottom: 2px; padding: 0 3px;
}
.bubble-text {
  max-width: 90%; font-size: 15px; line-height: 1.45; padding: 1px 3px;
  white-space: pre-wrap; word-break: break-word;
}
.bubble.user      .bubble-text { color: var(--white); text-align: right; }
.bubble.assistant .bubble-text { color: var(--green); }
.bubble.system    .bubble-text { color: var(--dim-hi); font-family: var(--mono); font-size: 11px; }
.bubble-text.failed { color: var(--red); }

.tier-badge {
  font-size: 10px; font-family: var(--mono); color: var(--dim-hi);
  margin-top: 1px; padding: 0 3px;
}
.tier-badge.cloud   { color: var(--violet); }
.tier-badge.nagatha { color: var(--cyan); }
.tier-badge.trust,
.tier-badge.solid   { color: var(--amber); }

.source-label {
  font-size: 10px; font-family: var(--mono); color: var(--dim-green);
  margin-bottom: 1px; padding: 0 3px;
}
.source-label.portal { color: var(--amber); }

.img-badge {
  display: inline-flex; align-items: center; gap: 6px;
  border: 1px solid var(--dim-green); border-radius: 5px;
  padding: 3px 7px; margin-top: 5px; cursor: pointer;
  font-size: 11px; color: var(--dim-hi);
  transition: border-color 0.15s, color 0.15s;
}
.img-badge:hover { border-color: var(--green); color: var(--green); }
.img-full { max-width: 100%; border-radius: 3px; margin-top: 5px; display: block; }

/* ── RIGHT: two stacked panels, no scroll anywhere ──────────────────────────── */
#right {
  flex: 1; display: flex; flex-direction: column; overflow: hidden;
  min-width: 0;
}

/* Shared section header bar */
.panel-header {
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: space-between;
  padding: 5px 12px; border-bottom: var(--divider);
  font-size: 10px; letter-spacing: 0.1em; color: var(--dim-hi);
  background: var(--bg);
}
.panel-label { color: var(--dim-hi); }
.panel-meta  { color: var(--dim-green); font-family: var(--mono); }

/* ── VIEWPORT panel (top ~60%) — Bilby bigcard, no scroll ─────────────────── */
#viewport-panel {
  flex: 0 0 60%; display: flex; flex-direction: column;
  border-bottom: 2px solid var(--dim-hi); overflow: hidden;
}
#bilby-frame {
  flex: 1; width: 100%; border: none; outline: none; background: var(--bg);
  display: block; overflow: hidden; pointer-events: none;
}

/* ── HUD STATE panel (bottom ~40%) — 2-cell: VIEWPORT + SHADES ────────────── */
#hud-panel {
  flex: 1; display: flex; flex-direction: column; overflow: hidden;
}
#hud-grid {
  flex: 1; display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr;
  gap: 0; overflow: hidden;
}

.hud-cell {
  display: flex; flex-direction: column; justify-content: center;
  padding: 16px 22px; overflow: hidden;
  border-right: var(--divider);
}
.hud-cell:last-child { border-right: none; }

.hud-lbl {
  font-size: 10px; letter-spacing: 0.12em; color: var(--dim-hi);
  margin-bottom: 10px;
}
.hud-state {
  font-size: 20px; font-weight: bold; letter-spacing: 0.04em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.hud-state.green  { color: var(--green); }
.hud-state.amber  { color: var(--amber); }
.hud-state.dim    { color: var(--dim-hi); }
.hud-state.white  { color: var(--white); }
.hud-sub {
  font-size: 11px; color: var(--dim-hi); margin-top: 6px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
</style>
</head>
<body>
<div id="root">

<!-- ── LEFT: Chat feed ────────────────────────────────────────────────────── -->
<div id="left">
  <div id="feed-header">
    <div id="conn-dot"></div>
    <span id="conn-label">SKIPPY PORTAL</span>
    <span style="margin-left:auto" id="msg-count">—</span>
  </div>
  <div id="feed"></div>
  <div id="input-bar">
    <input id="msg-input" type="text" placeholder="Message Skippy…" autocomplete="off">
    <button class="btn" id="send-btn">↑</button>
  </div>
</div>

<!-- ── RIGHT ──────────────────────────────────────────────────────────────── -->
<div id="right">

  <!-- VIEWPORT: Bilby bigcard — top 60% -->
  <div id="viewport-panel">
    <div class="panel-header">
      <span class="panel-label">VIEWPORT · BILBY BIGCARD</span>
      <span class="panel-meta" id="bilby-status">—</span>
    </div>
    <iframe id="bilby-frame" src="/bilby/bigcard" title="Bilby"
      scrolling="no" sandbox="allow-scripts allow-same-origin"></iframe>
  </div>

  <!-- HUD STATUS: chrome zones — bottom 40% -->
  <div id="hud-panel">
    <div class="panel-header">
      <span class="panel-label">SKIPPYDROID HUD</span>
      <span class="panel-meta" id="hud-updated">—</span>
    </div>
    <div id="hud-grid">

      <!-- Cell 1: VIEWPORT state -->
      <div class="hud-cell">
        <div class="hud-lbl">VIEWPORT</div>
        <div class="hud-state dim" id="vp-state">—</div>
        <div class="hud-sub" id="vp-sub">checking…</div>
      </div>

      <!-- Cell 2: SHADES state -->
      <div class="hud-cell">
        <div class="hud-lbl">SHADES</div>
        <div class="hud-state dim" id="shades-state">—</div>
        <div class="hud-sub" id="shades-sub">waiting for phone</div>
      </div>

    </div>
  </div>

</div><!-- #right -->
</div><!-- #root -->

<script>
const $ = id => document.getElementById(id);
const feed = $('feed');

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

function tierClass(t) {
  if (t === 'cloud')             return 'cloud';
  if (t === 'nagatha')           return 'nagatha';
  if (t === 'trust_awesomeness') return 'trust';
  if (t === 'solid_gold')        return 'solid';
  return '';
}
function tierLabel(t) {
  return {'local':'ollama','cloud':'claude','nagatha':'nagatha',
          'trust_awesomeness':'trust_awesomeness',
          'solid_gold':'solid_gold','dropship':'dropship'}[t] || t || '';
}

let msgCount = 0;
function setMsgCount(n) { msgCount = n; $('msg-count').textContent = n + ' msgs'; }

// ── Render bubble ─────────────────────────────────────────────────────────────
function renderBubble(msg) {
  const div = document.createElement('div');
  div.className = `bubble ${msg.role}`;
  let html = '';
  if (msg.source === 'portal')
    html += `<span class="source-label portal">portal</span>`;
  if (msg.narration)
    html += `<span class="narration">${escHtml(msg.narration)}</span>`;
  html += `<div class="${msg.failed ? 'bubble-text failed' : 'bubble-text'}">${escHtml(msg.text)}</div>`;
  if (msg.hasImage)
    html += `<div class="img-badge" onclick="expandImage(${msg.id},this)">🖼 tap to expand</div>`;
  const tl = tierLabel(msg.tier);
  if (tl) html += `<span class="tier-badge ${tierClass(msg.tier)}">${escHtml(tl)}</span>`;
  div.innerHTML = html;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
}

async function expandImage(msgId, badge) {
  try {
    const d = await (await fetch(`/portal/image/${msgId}`)).json();
    if (d.data_url) {
      const img = document.createElement('img');
      img.src = d.data_url; img.className = 'img-full';
      badge.replaceWith(img);
    }
  } catch (_) {}
}
window.expandImage = expandImage;

// ── History ───────────────────────────────────────────────────────────────────
async function loadHistory() {
  try {
    const msgs = await (await fetch('/portal/history')).json();
    msgs.forEach(renderBubble);
    setMsgCount(msgs.length);
    if (msgs.length) updateLastMsg(msgs[msgs.length - 1].text);
  } catch (_) {}
}

// ── SSE ───────────────────────────────────────────────────────────────────────
function connectSSE() {
  const es = new EventSource('/portal/events');
  es.onopen  = () => { $('conn-dot').classList.add('live'); $('conn-label').textContent = 'SKIPPY PORTAL · LIVE'; };
  es.onerror = () => { $('conn-dot').classList.remove('live'); $('conn-label').textContent = 'SKIPPY PORTAL · reconnecting…'; };
  es.onmessage = e => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'ping') return;
      renderBubble(msg);
      setMsgCount(msgCount + 1);
      updateLastMsg(msg.text);
    } catch (_) {}
  };
}

function updateLastMsg(text) {
  $('last-msg').textContent = (text || '').slice(0, 90);
}

// ── Send ──────────────────────────────────────────────────────────────────────
const input = $('msg-input');
const sendBtn = $('send-btn');

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  input.value = ''; sendBtn.disabled = true;
  try {
    await fetch('/intent/unmatched', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({text, _portal_source:'portal'}),
    });
  } catch (_) {}
  sendBtn.disabled = false; input.focus();
}
sendBtn.onclick = sendMessage;
input.addEventListener('keydown', e => { if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });

// ── HUD state refresh ─────────────────────────────────────────────────────────
async function refreshHud() {
  try {
    // Viewport: infer from bilby status
    const hd = await (await fetch('/portal/hud')).json();
    const bilby = hd.bilby || {};
    const np = bilby.now_playing || {};
    const streaming = np.title && bilby.source !== 'none';
    const vpEl  = $('vp-state');
    const vpSub = $('vp-sub');
    if (streaming) {
      vpEl.textContent = '● STREAMING';
      vpEl.className = 'hud-state green';
      vpSub.textContent = `♪ ${np.artist || ''} — ${np.title}`;
    } else {
      vpEl.textContent = '○ IDLE';
      vpEl.className = 'hud-state dim';
      vpSub.textContent = bilby.source === 'none' ? 'no source' : 'standby';
    }

    // Viewport header label
    $('bilby-status').textContent = streaming
      ? `♪ ${np.artist || ''} — ${np.title}`
      : 'idle';

    // Shades: read from droid-state endpoint
    const ds = await (await fetch('/portal/droid-state')).json();
    const shadesEl  = $('shades-state');
    const shadesSub = $('shades-sub');
    if (ds.shades === true) {
      shadesEl.textContent = '◉ SHADES ON';
      shadesEl.className = 'hud-state amber';
      shadesSub.textContent = 'physical layer · high contrast';
    } else if (ds.shades === false) {
      shadesEl.textContent = '◎ EYES OPEN';
      shadesEl.className = 'hud-state green';
      shadesSub.textContent = 'passthrough · world visible';
    } else {
      shadesEl.textContent = '— UNKNOWN';
      shadesEl.className = 'hud-state dim';
      shadesSub.textContent = 'waiting for phone push';
    }

    $('hud-updated').textContent = new Date().toLocaleTimeString('en-US',{hour12:false});
  } catch (_) { $('hud-updated').textContent = 'error'; }
}
refreshHud(); setInterval(refreshHud, 5_000);

loadHistory().then(connectSSE);
</script>
</body>
</html>
"""

# ── New HTML — true mirror of SkippyDroid HUD ────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Skippy Portal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:        #000000;
  --green:     #00ff88;
  --dim-green: #003322;
  --dim-hi:    #006644;
  --white:     #ffffff;
  --amber:     #ffaa00;
  --cyan:      #00ccff;
  --violet:    #aa44ff;
  --red:       #ff4444;
  --vp-blue:   #000018;          /* dark blue viewport tint */
  --font:      'Atkinson Hyperlegible', sans-serif;
  --mono:      'Courier New', monospace;
  --divider:   1px solid var(--dim-hi);
}

html, body {
  height: 100%; background: #0a0a0a; color: var(--white);
  font-family: var(--font); font-size: 14px;
  overflow: hidden; user-select: none;
}
body {
  /* Center the locked-ratio block within the viewport;
     dim the area around it so the lock is visible. */
  display: flex; align-items: center; justify-content: center;
}

/* ── Root: ratio-locked layout block ──────────────────────────────────────
   Composite of three S23-native-resolution panels:
     • Chat:        1080 × 2340  (S23 portrait)        — 29.87% block width
     • Fullscreen:  2340 × 1170  (S23 landscape, half) — 70.13% block width
     • zHUD:        2340 × 1170  (S23 landscape, half) — 70.13% block width
   Block dimensions: 3615 × 2340 → ratio ≈ 1.545:1.
   Margins/corners pin to borders so any underlying device ratio (S23 vs
   Viture) absorbs cleanly into the structural layout. */
#root {
  aspect-ratio: 3615 / 2340;
  width:  min(100vw, calc(100vh * 3615 / 2340));
  height: min(100vh, calc(100vw * 2340 / 3615));
  display: flex;
  background: var(--bg);
  box-shadow: 0 0 0 1px var(--dim-green);
}

/* ── Corner hard-points (fixed at 4 window corners, over everything) ──────── */
.corner-hp {
  position: fixed; width: 26px; height: 26px;
  z-index: 100; pointer-events: none;
}
.corner-hp.tl { top: 0;    left: 0;
  border-top:    2px solid var(--green);
  border-left:   2px solid var(--green); }
.corner-hp.tr { top: 0;    right: 0;
  border-top:    2px solid var(--green);
  border-right:  2px solid var(--green); }
.corner-hp.bl { bottom: 0; left: 0;
  border-bottom: 2px solid var(--green);
  border-left:   2px solid var(--green); }
.corner-hp.br { bottom: 0; right: 0;
  border-bottom: 2px solid var(--green);
  border-right:  2px solid var(--green); }

/* ── LEFT: SkippyChat mirror at S23 portrait native (1080×2340) ─────────── */
#panel-chat {
  /* 1080 / 3615 = 29.876% of locked block width — derives 1:2.167 ratio */
  width: calc(1080 / 3615 * 100%);
  height: 100%; flex-shrink: 0;
  display: flex; flex-direction: column;
  border-right: 1px solid var(--dim-green); overflow: hidden;
  position: relative;  /* anchor for .candy absolute children */
}

#feed-header {
  flex-shrink: 0; padding: 7px 14px; border-bottom: 1px solid var(--dim-green);
  display: flex; align-items: center; gap: 8px;
  font-size: 11px; color: var(--dim-hi); letter-spacing: 0.08em;
}
#conn-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--dim-green); transition: background 0.4s;
}
#conn-dot.live { background: var(--green); }

#feed {
  flex: 1; overflow-y: auto; padding: 10px;
  display: flex; flex-direction: column; gap: 5px;
  scroll-behavior: smooth;
}
#feed::-webkit-scrollbar { width: 3px; }
#feed::-webkit-scrollbar-thumb { background: var(--dim-green); border-radius: 2px; }

#input-bar {
  flex-shrink: 0; display: flex; align-items: center; gap: 6px;
  padding: 7px 10px; border-top: 1px solid var(--dim-green);
}
#msg-input {
  flex: 1; background: transparent; border: 1px solid var(--dim-green);
  border-radius: 3px; color: var(--white); font-family: var(--font);
  font-size: 14px; padding: 5px 9px; outline: none;
  transition: border-color 0.2s;
}
#msg-input:focus { border-color: var(--dim-hi); }
#msg-input::placeholder { color: var(--dim-hi); }
.btn {
  background: transparent; border: 1px solid var(--dim-green);
  border-radius: 3px; color: var(--dim-hi); cursor: pointer;
  font-family: var(--font); font-size: 14px; padding: 5px 9px;
  transition: border-color 0.15s, color 0.15s; flex-shrink: 0;
}
.btn:hover  { border-color: var(--green); color: var(--green); }
.btn:disabled { opacity: 0.3; cursor: default; }

/* ── Bubbles ──────────────────────────────────────────────────────────────── */
.bubble { display: flex; flex-direction: column; }
.bubble.user      { align-items: flex-end; }
.bubble.assistant { align-items: flex-start; }

.narration {
  font-size: 11px; font-style: italic; color: var(--dim-hi);
  margin-bottom: 2px; padding: 0 3px;
}
.bubble-text {
  max-width: 90%; font-size: 15px; line-height: 1.45; padding: 1px 3px;
  white-space: pre-wrap; word-break: break-word;
}
.bubble.user      .bubble-text { color: var(--white); text-align: right; }
.bubble.assistant .bubble-text { color: var(--green); }
.bubble-text.failed { color: var(--red); }

.tier-badge {
  font-size: 10px; font-family: var(--mono); color: var(--dim-hi);
  margin-top: 1px; padding: 0 3px;
}
.tier-badge.cloud   { color: var(--violet); }
.tier-badge.nagatha { color: var(--cyan); }
.tier-badge.trust,
.tier-badge.solid   { color: var(--amber); }

.source-label {
  font-size: 10px; font-family: var(--mono); color: var(--dim-green);
  margin-bottom: 1px; padding: 0 3px;
}
.source-label.portal { color: var(--amber); }

.img-badge {
  display: inline-flex; align-items: center; gap: 6px;
  border: 1px solid var(--dim-green); border-radius: 5px;
  padding: 3px 7px; margin-top: 5px; cursor: pointer;
  font-size: 11px; color: var(--dim-hi);
}
.img-badge:hover { border-color: var(--green); color: var(--green); }
.img-full { max-width: 100%; border-radius: 3px; margin-top: 5px; display: block; }

/* ── RIGHT: stacked Fullscreen + zHUD, each at S23 landscape native ──────── */
#panel-right {
  /* 2535 / 3615 = 70.124% of locked block width;
     contains two stacked panels each at 2340×1170 (50% height = S23 landscape) */
  width: calc(2535 / 3615 * 100%);
  height: 100%; flex-shrink: 0;
  display: flex; flex-direction: column;
  overflow: hidden; min-width: 0;
}

/* shared header bar above each right-side section */
.panel-header {
  flex-shrink: 0;
  display: flex; align-items: center; justify-content: space-between;
  padding: 4px 14px; border-bottom: 1px solid var(--dim-green);
  font-size: 10px; letter-spacing: 0.1em; color: var(--dim-hi);
}
.panel-header .meta { color: var(--dim-green); font-family: var(--mono); }

/* ── Fullscreen panel (top of right side, blank by default) ───────────────── */
#panel-fullscreen {
  width: 100%; height: 50%; flex: none;
  display: flex; flex-direction: column; overflow: hidden;
  border-bottom: 1px solid var(--dim-hi);
}
#fullscreen-body {
  flex: 1; background: var(--bg); display: flex;
  align-items: center; justify-content: center;
  color: var(--dim-green); font-size: 11px; letter-spacing: 0.15em;
}

/* ── zHUD frame (16:10 mirror of Viture canvas) ───────────────────────────── */
/*
   Visual structure (matches the mockup):
   ┌────────────────────────────────────────────────┐
   │ ▣ [── white chrome strip + cyan accent ──]   ▣ │   ← top chrome
   │ ║                                            ║ │
   │ ║  [white+cyan ║]   VIEWPORT     [║+cyan]    ║ │   ← left/right chrome
   │ ║              ║   (dark blue)    ║          ║ │     wrap viewport
   │ ║              ║   inner frame    ║          ║ │
   │ ▣ [── white chrome strip + cyan accent ──]   ▣ │   ← bottom chrome
   └────────────────────────────────────────────────┘
       ▣ = filled violet corner hard-point (sits OVER chrome corners)
*/

#panel-hud {
  width: 100%; height: 50%; flex: none;
  display: flex; flex-direction: column; overflow: hidden;
}

/* The HUD canvas — fills the parent.  The MJPEG mirror image is sized
   to fit while preserving aspect ratio (object-fit: contain). */
#hud-canvas {
  position: relative;
  flex: 1; width: 100%; height: 100%;
  background: var(--bg);
  display: flex; align-items: center; justify-content: center;
  overflow: hidden;
}
#hud-mjpeg {
  max-width: 100%; max-height: 100%;
  width: auto; height: auto;
  display: block;
  /* Object-fit:contain keeps zHUD's render proportions intact regardless
     of the panel's S23-landscape outer container. */
  object-fit: contain;
  transition: opacity 0.2s;
}
#hud-no-frame {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: var(--dim-green); font-size: 11px; letter-spacing: 0.15em;
  pointer-events: none;
  /* Hidden when the <img> has loaded a frame (we toggle via JS) */
}

/* Zones live in a 3×3 absolute layout */
.hud-z {
  position: absolute; overflow: hidden;
  display: flex; flex-direction: column; justify-content: center;
  font-size: 11px; line-height: 1.2;
  padding: 8px 12px;
}
#z-top-start  { top: 0; left: 0;
                width: 13.5%; height: var(--chrome-top); }
#z-top-center { top: 0; left: 13.5%; right: 16.7%; height: var(--chrome-top);
                align-items: center; }
#z-top-end    { top: 0; right: 0;
                width: 16.7%; height: var(--chrome-top);
                align-items: flex-end; }

#z-left-bar   { top: var(--chrome-top); bottom: var(--chrome-bot); left: 0;
                width: var(--chrome-left);
                align-items: center; justify-content: center;
                gap: 4px; padding: 8px 0; }
#z-right-bar  { top: var(--chrome-top); bottom: var(--chrome-bot); right: 0;
                width: var(--chrome-right);
                align-items: center; justify-content: flex-end;
                gap: 3px; padding: 8px 0; }

#z-bot-start  { bottom: 0; left: 0;
                width: 15.6%; height: var(--chrome-bot); }
#z-bot-center { bottom: 0; left: 15.6%; right: 10.4%; height: var(--chrome-bot);
                align-items: center; }
#z-bot-end    { bottom: 0; right: 0;
                width: 10.4%; height: var(--chrome-bot);
                align-items: flex-end; }

/* ── Viewport (subtly dark blue tint just for browser-side contrast) ──── */
#z-viewport {
  position: absolute;
  top: var(--chrome-top); bottom: var(--chrome-bot);
  left: var(--chrome-left); right: var(--chrome-right);
  background: var(--vp-blue); padding: 0;
}
#z-viewport iframe {
  position: absolute; inset: 0; width: 100%; height: 100%;
  border: none; outline: none; background: var(--vp-blue);
  pointer-events: none; opacity: 0; transition: opacity 0.3s;
}
#z-viewport iframe.streaming { opacity: 1; }
#vp-idle {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: var(--dim-green); font-size: 10px; letter-spacing: 0.2em;
}

/* ── Top/bottom margin slots ─────────────────────────────────────────────
   Each margin holds N slots, each slot up to 5 chars.  Slots are evenly
   distributed in a flex row.  Text is monospace green.  Symbols/animations
   preferred (clock "12:45", date "26APR", indicator "●●○○○", etc.). */
#z-top-center, #z-bot-center {
  flex-direction: row !important;
  align-items: center; justify-content: center;
  gap: 18px;
}
.slot {
  font-family: var(--mono);
  font-size: 13px;
  color: var(--green);
  letter-spacing: 0.05em;
  min-width: 5ch; text-align: center;
  white-space: nowrap; overflow: hidden;
}
.slot.dim   { color: var(--dim-hi); }
.slot.amber { color: var(--amber); }
.slot.red   { color: var(--red); }

/* ── Side margins — vertical icon columns ────────────────────────────── */
/* Each cell holds one mono-colored icon (default green, can change to
   amber/red for status).  Icons stack from top, dim placeholders below. */
#z-left-bar, #z-right-bar {
  gap: 8px; padding: 12px 0;
  align-items: center; justify-content: flex-start;
}
.icon-cell {
  width: 18px; height: 18px;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; color: var(--dim-green);
  border: 1px solid var(--dim-green);
  border-radius: 2px;
}
.icon-cell.lit       { color: var(--green); border-color: var(--green); }
.icon-cell.lit-amber { color: var(--amber); border-color: var(--amber); }
.icon-cell.lit-red   { color: var(--red);   border-color: var(--red); }

/* ── Corner hard-points — 2×5 char text blocks at 4 HUD corners ──────── */
/* Spec: same content displayed across all Main 3 (chat/fullscreen/zHUD).
   Each corner holds 2 rows × 5 chars OR a symbol.  Default green; can
   color-change, blink, or disappear (display:none). */
.hud-corner {
  position: absolute;
  width: var(--chrome-left); /* match left chrome width for a square-ish feel */
  height: var(--chrome-top);
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  font-family: var(--mono); font-size: 11px;
  color: var(--green);
  line-height: 1.15;
  letter-spacing: 0.04em;
  border: 1px solid var(--green);
  z-index: 5; pointer-events: none;
}
.hud-corner.tl { top: 0; left: 0; }
.hud-corner.tr { top: 0; right: 0; width: var(--chrome-right); }
.hud-corner.bl { bottom: 0; left: 0; height: var(--chrome-bot); }
.hud-corner.br { bottom: 0; right: 0;
                 width: var(--chrome-right); height: var(--chrome-bot); }
.hud-corner .row1, .hud-corner .row2 { white-space: nowrap; }
.hud-corner.amber  { color: var(--amber); border-color: var(--amber); }
.hud-corner.red    { color: var(--red);   border-color: var(--red); }
.hud-corner.blink  { animation: cornerBlink 1s steps(2) infinite; }
.hud-corner.hidden { display: none; }
@keyframes cornerBlink { 50% { opacity: 0.25; } }

/* Subtle dim-green dividers between zones so the structure reads in browser */
#z-top-center { border-bottom: 1px solid var(--dim-green); }
#z-bot-center { border-top:    1px solid var(--dim-green); }
#z-left-bar   { border-right:  1px solid var(--dim-green); }
#z-right-bar  { border-left:   1px solid var(--dim-green); }

/* Zone label/value typography */
.zlbl { font-size: 9px; letter-spacing: 0.1em; color: var(--dim-hi);
        margin-bottom: 2px; }
.zval { font-size: 13px; color: var(--white); white-space: nowrap;
        overflow: hidden; text-overflow: ellipsis; }
.zval.green { color: var(--green); }
.zval.amber { color: var(--amber); }
.zval.dim   { color: var(--dim-hi); }
.zval.large { font-size: 16px; letter-spacing: 0.04em; }
.zsub  { font-size: 10px; color: var(--dim-hi); margin-top: 1px;
         white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* Bar pips */
.pip {
  width: 4px; background: var(--dim-green); border-radius: 1px;
}
.pip.lit-green { background: var(--green); }
.pip.lit-amber { background: var(--amber); }
.pip.lit-red   { background: var(--red); }
#z-left-bar .pip  { height: 14%; }
#z-right-bar .pip { height: 14%; width: 4px; }

/* ── Hardpoint candy overlay inside SkippyChat panel ─────────────────────────
   Mirrors the phone's 3 corner candies: TL health, TR tier/signal, BR Bilby.
   Positioned absolutely inside #panel-chat which carries position:relative.
   Default state is grey so they're always visible — even before phone data arrives.  */
.candy {
  position: absolute; z-index: 20;
  width: 82px; height: 42px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 1px;
  font-family: var(--mono); font-size: 10px; line-height: 1.25;
  border: 1px solid #444;
  background: rgba(30,30,30,0.85); backdrop-filter: blur(3px);
  pointer-events: none; color: #888;    /* grey = idle/unknown */
  letter-spacing: 0.04em;
}
.candy.tl { top: 33px;    left: 3px; }
.candy.tr { top: 33px;    right: 3px; }
.candy.br { bottom: 42px; right: 3px; }
.candy.hidden { display: none !important; }
.candy.green  { color: var(--green);  border-color: var(--dim-hi); background: rgba(0,20,10,0.85); }
.candy.amber  { color: var(--amber);  border-color: var(--amber);  background: rgba(30,15,0,0.85); }
.candy.red    { color: var(--red);    border-color: var(--red);    background: rgba(30,0,0,0.85); }
.candy.cyan   { color: var(--cyan);   border-color: var(--cyan);   background: rgba(0,20,30,0.85); }
.candy.violet { color: var(--violet); border-color: var(--violet); background: rgba(20,0,30,0.85); }
.candy .c-r1, .candy .c-r2, .candy .c-r3 { white-space: nowrap; overflow: hidden;
                               max-width: 96px; text-align: center; font-size: 11px; }
/* TR pill defaults — individual active colours set by JS */
.candy.tr .c-r1, .candy.tr .c-r2, .candy.tr .c-r3 { color: #484848; }

/* ── AR LAYER panel (top of right side) ─────────────────────────────────────
   Future: map lines, game UI.  Blank canvas for now.                       */
#panel-ar {
  width: 100%; height: 50%; flex: none;
  display: flex; flex-direction: column; overflow: hidden;
  border-bottom: 1px solid var(--dim-hi);
}
#ar-body {
  flex: 1; position: relative; background: var(--bg);
  display: flex; align-items: center; justify-content: center;
}
#ar-canvas { position: absolute; inset: 0; width: 100%; height: 100%; }
#ar-idle   { color: var(--dim-green); font-size: 10px; letter-spacing: 0.15em;
             pointer-events: none; z-index: 1; }

/* ── GLASSES simulation panel (bottom of right side) ─────────────────────────
   CSS-rendered mirror of GlassesHudPresentation — works without physical
   glasses plugged in.  Driven entirely by /portal/chat-state SSE.          */
#panel-glasses {
  width: 100%; height: 50%; flex: none;
  display: flex; flex-direction: column; overflow: hidden;
}
#glasses-canvas {
  flex: 1; position: relative;
  background: #000;   /* pure black = transparent on Viture XR */
  overflow: hidden;
}

/* Corner candies in the glasses view.
   Default is grey so they're always visible even before phone data arrives — pure
   black would be invisible against the black glasses-canvas background.          */
.gc {
  position: absolute; width: 108px; height: 52px;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px;
  font-family: var(--mono); font-size: 12px; line-height: 1.25;
  border: 1px solid #444;
  background: rgba(30,30,30,0.7);
  color: #888;    /* grey = idle/unknown */
  pointer-events: none;
  letter-spacing: 0.04em;
}
.gc.tl { top:    10px; left:  10px; }
.gc.tr { top:    10px; right: 10px; }
.gc.bl { bottom: 10px; left:  10px; }
.gc.br { bottom: 10px; right: 10px; }
.gc .r1, .gc .r2, .gc .r3 { white-space: nowrap; overflow: hidden;
                    max-width: 100px; text-align: center; font-size: 11px; }
/* TR pill defaults — active colours set by JS */
.gc.tr .r1, .gc.tr .r2, .gc.tr .r3 { color: #484848; }
.gc.hidden { display: none !important; }
.gc.green  { color: var(--green);  border-color: var(--dim-hi); background: rgba(0,20,10,0.7); }
.gc.amber  { color: var(--amber);  border-color: var(--amber);  background: rgba(30,15,0,0.7); }
.gc.red    { color: var(--red);    border-color: var(--red);    background: rgba(30,0,0,0.7); }
.gc.cyan   { color: var(--cyan);   border-color: var(--cyan);   background: rgba(0,20,30,0.7); }
.gc.violet { color: var(--violet); border-color: var(--violet); background: rgba(20,0,30,0.7); }

/* Top strip — compass bearing + translation text.
   Grey border + grey text when empty; amber when content is present.  */
#gc-top-strip {
  position: absolute; top: 10px;
  left: 130px; right: 130px;
  height: 52px;   /* same height as gc corners */
  display: flex; align-items: center; justify-content: center;
  font-family: var(--mono); font-size: 13px;
  color: var(--amber); letter-spacing: 0.05em;
  overflow: hidden; white-space: nowrap;
  border: 1px solid #444;
  background: rgba(30,30,30,0.5);
}
#gc-top-strip:empty {
  border-color: #333;
  background: rgba(20,20,20,0.4);
}

/* Predator tint overlay */
#gc-predator {
  position: absolute; inset: 0;
  pointer-events: none; display: none;
}

/* No-data idle text */
#gc-idle {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  color: var(--dim-green); font-size: 11px; letter-spacing: 0.15em;
  pointer-events: none;
}
</style>
</head>
<body>

<!-- 4 corner hard-points: fixed overlay over all panels -->
<div class="corner-hp tl"></div>
<div class="corner-hp tr"></div>
<div class="corner-hp bl"></div>
<div class="corner-hp br"></div>

<div id="root">

<!-- ── LEFT: SkippyChat mirror ──────────────────────────────────────────── -->
<div id="panel-chat">
  <div id="feed-header">
    <div id="conn-dot"></div>
    <span id="conn-label">SKIPPY PORTAL</span>
    <span style="margin-left:auto" id="msg-count">—</span>
  </div>
  <div id="feed"></div>
  <div id="input-bar">
    <input id="msg-input" type="text" placeholder="Message Skippy…" autocomplete="off">
    <button class="btn" id="send-btn">↑</button>
  </div>
  <!-- Hardpoint candy overlay — mirrors phone corner candies -->
  <div class="candy tl hidden" id="cc-tl"><div class="c-r1"></div><div class="c-r2"></div></div>
  <div class="candy tr hidden" id="cc-tr"><div class="c-r1 pill-nagatha">NAGATHA</div><div class="c-r2 pill-claude">THINKING CAP</div><div class="c-r3 pill-grumpy">GRUMPY</div></div>
  <div class="candy br hidden" id="cc-br"><div class="c-r1"></div><div class="c-r2"></div></div>
</div>

<!-- ── RIGHT: AR layer + Glasses simulation ─────────────────────────────── -->
<div id="panel-right">

  <!-- AR LAYER — canvas for future AR content: map lines, game UI -->
  <div id="panel-ar">
    <div class="panel-header">
      <span>AR LAYER</span>
      <span class="meta" id="ar-meta">canvas ready</span>
    </div>
    <div id="ar-body">
      <canvas id="ar-canvas"></canvas>
      <div id="ar-idle">— AR canvas · map lines · game output —</div>
    </div>
  </div>

  <!-- GLASSES — CSS simulation of GlassesHudPresentation (no glasses needed) -->
  <div id="panel-glasses">
    <div class="panel-header">
      <span>GLASSES</span>
      <span class="meta" id="glasses-ts">—</span>
    </div>
    <div id="glasses-canvas">
      <!-- 4 corner candies matching GlassesHudPresentation layout -->
      <div class="gc tl" id="gc-tl"><div class="r1">●</div><div class="r2">SKIPPY</div></div>
      <div class="gc tr" id="gc-tr"><div class="r1 pill-nagatha">NAGATHA</div><div class="r2 pill-claude">THINKING CAP</div><div class="r3 pill-grumpy">GRUMPY</div></div>
      <div class="gc bl" id="gc-bl"><div class="r1"></div><div class="r2"></div></div>
      <div class="gc br" id="gc-br"><div class="r1">—</div><div class="r2"></div></div>
      <!-- Top strip: compass bearing + translation text -->
      <div id="gc-top-strip"></div>
      <!-- Predator vision tint overlay -->
      <div id="gc-predator"></div>
      <!-- Idle placeholder — hidden once phone pushes data -->
      <div id="gc-idle">waiting for phone…</div>
    </div>
  </div><!-- #panel-glasses -->

</div><!-- #panel-right -->
</div><!-- #root -->

<script>
const $ = id => document.getElementById(id);
const feed = $('feed');

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}
function tierClass(t) {
  return ({cloud:'cloud',nagatha:'nagatha',trust_awesomeness:'trust',solid_gold:'solid'}[t]) || '';
}
function tierLabel(t) {
  return ({local:'ollama',cloud:'claude',nagatha:'nagatha',
           trust_awesomeness:'trust_awesomeness',solid_gold:'solid_gold',
           dropship:'dropship'}[t]) || t || '';
}

let msgCount = 0;
function setMsgCount(n) { msgCount = n; $('msg-count').textContent = n + ' msgs'; }

function renderBubble(msg) {
  const div = document.createElement('div');
  div.className = `bubble ${msg.role}`;
  let html = '';
  if (msg.source === 'portal')
    html += `<span class="source-label portal">portal</span>`;
  if (msg.narration)
    html += `<span class="narration">${escHtml(msg.narration)}</span>`;
  html += `<div class="${msg.failed ? 'bubble-text failed' : 'bubble-text'}">${escHtml(msg.text)}</div>`;
  if (msg.hasImage)
    html += `<div class="img-badge" onclick="expandImage(${msg.id},this)">🖼 tap to expand</div>`;
  const tl = tierLabel(msg.tier);
  if (tl) html += `<span class="tier-badge ${tierClass(msg.tier)}">${escHtml(tl)}</span>`;
  div.innerHTML = html;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
}

async function expandImage(msgId, badge) {
  try {
    const d = await (await fetch(`/portal/image/${msgId}`)).json();
    if (d.data_url) {
      const img = document.createElement('img');
      img.src = d.data_url; img.className = 'img-full';
      badge.replaceWith(img);
    }
  } catch (_) {}
}
window.expandImage = expandImage;

async function loadHistory() {
  try {
    const msgs = await (await fetch('/portal/history')).json();
    msgs.forEach(renderBubble);
    setMsgCount(msgs.length);
  } catch (_) {}
}

function connectSSE() {
  const es = new EventSource('/portal/events');
  es.onopen  = () => { $('conn-dot').classList.add('live');
                       $('conn-label').textContent = 'SKIPPY PORTAL · LIVE'; };
  es.onerror = () => { $('conn-dot').classList.remove('live');
                       $('conn-label').textContent = 'SKIPPY PORTAL · reconnecting…'; };
  es.onmessage = e => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'ping') return;
      renderBubble(msg);
      setMsgCount(msgCount + 1);
    } catch (_) {}
  };
}

// ── Send ──────────────────────────────────────────────────────────────────────
const input = $('msg-input');
const sendBtn = $('send-btn');
async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  input.value = ''; sendBtn.disabled = true;
  try {
    await fetch('/intent/unmatched', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({text, _portal_source:'portal'}),
    });
  } catch (_) {}
  sendBtn.disabled = false; input.focus();
}
sendBtn.onclick = sendMessage;
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

// ── Candy helpers ─────────────────────────────────────────────────────────────
// colorClass: map server color name → CSS class on .candy / .gc elements.
function colorClass(c) {
  return ({'green':'green','amber':'amber','red':'red','cyan':'cyan','violet':'violet'}[c]) || 'green';
}

// Update one corner candy in the SkippyChat left panel.
function updateChatCandy(pos, data) {
  const el = $('cc-' + pos);
  if (!el) return;
  if (!data || data.hidden) { el.className = `candy ${pos} hidden`; return; }
  if (pos === 'tr') {
    // TR = three AI-tier pills (Nagatha, Thinking Cap, Grumpy)
    _applyPill(el.querySelector('.pill-nagatha'),   data.nagatha, '#4285F4');
    _applyPill(el.querySelector('.pill-claude'),    data.claude,  '#DA7756');
    _applyPill(el.querySelector('.pill-grumpy'),    data.grumpy,  '#EEEEEE');
    el.className = `candy ${pos}`;
  } else {
    el.querySelector('.c-r1').textContent = data.row1 || '';
    el.querySelector('.c-r2').textContent = data.row2 || '';
    el.className = `candy ${pos} ${colorClass(data.color)}`;
  }
}

// Update one corner candy in the GLASSES simulation panel.
function updateGlassesCorner(pos, data) {
  const el = $('gc-' + pos);
  if (!el) return;
  if (!data || data.hidden) { el.className = `gc ${pos} hidden`; return; }
  if (pos === 'tr') {
    // TR = three AI-tier pills
    _applyPill(el.querySelector('.pill-nagatha'),   data.nagatha, '#4285F4');
    _applyPill(el.querySelector('.pill-claude'),    data.claude,  '#DA7756');
    _applyPill(el.querySelector('.pill-grumpy'),    data.grumpy,  '#EEEEEE');
    el.className = `gc ${pos}`;
  } else {
    el.querySelector('.r1').textContent = data.row1 || '';
    el.querySelector('.r2').textContent = data.row2 || '';
    el.className = `gc ${pos} ${colorClass(data.color)}`;
  }
}

// Apply an active/inactive colour to a pill span element.
function _applyPill(el, active, activeColor) {
  if (!el) return;
  el.style.color      = active ? activeColor : '#484848';
  el.style.fontWeight = active ? 'bold' : 'normal';
}

// Predator vision tint colors matching PredatorMode.kt exactly.
const PREDATOR_TINTS = {
  THERMAL:         'rgba(255,102,  0,0.60)',
  NIGHT_VISION:    'rgba(  0,255, 68,0.60)',
  MOTION:          'rgba(  0,204,255,0.60)',
  UV:              'rgba(170, 68,255,0.60)',
  INFRARED:        'rgba(204,  0, 34,0.60)',
  ELECTROMAGNETIC: 'rgba(204,255,  0,0.60)',
  HUNT:            'rgba(255, 51,  0,0.60)',
};
function setPredatorMode(mode) {
  const el = $('gc-predator');
  if (!el) return;
  if (!mode || mode === 'NONE') { el.style.display = 'none'; return; }
  el.style.display = 'block';
  el.style.background = PREDATOR_TINTS[mode] || 'transparent';
}

function setTranslation(text) {
  const el = $('gc-top-strip');
  if (!el) return;
  el.textContent = text || '';
}

// Apply a full candy payload to both panels at once.
let candyFromPhone = false;
function applyCandy(candy) {
  if (!candy) return;
  candyFromPhone = true;
  updateChatCandy('tl', candy.tl);
  updateChatCandy('tr', candy.tr);
  updateChatCandy('br', candy.br);
  updateGlassesCorner('tl', candy.tl);
  updateGlassesCorner('tr', candy.tr);
  updateGlassesCorner('bl', candy.bl || null);
  updateGlassesCorner('br', candy.br);
  setPredatorMode(candy.predator_mode || 'NONE');
  setTranslation(candy.translation || '');
  // Hide idle placeholder once real data arrives.
  const idle = $('gc-idle');
  if (idle) idle.style.display = 'none';
  const ts = $('glasses-ts');
  if (ts) ts.textContent = new Date().toLocaleTimeString();
}

// ── /health fallback poller — populates candy when phone isn't pushing ────────
// Runs every 5 s.  Skips update if the phone has already sent real candy data.
async function pollHealthForCandy() {
  if (candyFromPhone) return;
  try {
    const h = await (await fetch('/health')).json();
    const online   = h.status === 'ok';
    const models   = h.models || {};
    const ollama   = models.ollama?.available ?? false;
    const tiers    = models.tiers  || {};
    const anyPaid  = Object.values(tiers).some(Boolean);
    // Derive signal bar string (4 bars max, using block chars like TierIndicatorCandy.kt)
    const latencyEl = $('conn-dot');   // proxy: green = online
    const bars  = online ? (ollama ? '████' : '██░░') : '░░░░';
    const barsColor = online ? (ollama ? 'green' : 'amber') : 'red';
    const model = anyPaid ? '~' : '~';
    const tlColor = online ? (ollama ? 'green' : 'amber') : 'red';
    applyCandy({
      tl: {row1: '●', row2: 'SKIPPY', color: tlColor},
      tr: {nagatha: false, claude: false, grumpy: false, color: barsColor},
      br: {row1: '—', row2: '', color: 'amber'},
      predator_mode: 'NONE',
      translation: '',
    });
    candyFromPhone = false;   // keep re-polling until phone pushes
  } catch (_) {}
}

setInterval(pollHealthForCandy, 5000);
pollHealthForCandy();

// ── SSE: chat-state events — receive live candy + predator + translation ──────
function connectChatStateSSE() {
  const es = new EventSource('/portal/chat-state/events');
  es.onmessage = e => {
    try {
      const chat = JSON.parse(e.data);
      if (!chat || chat.type === 'ping') return;
      if (chat.candy) applyCandy(chat.candy);
    } catch (_) {}
  };
  es.onerror = () => {};  // browser auto-reconnects
}

// ── Boot ──────────────────────────────────────────────────────────────────────
loadHistory().then(connectSSE);
connectChatStateSSE();
</script>
</body>
</html>
"""
