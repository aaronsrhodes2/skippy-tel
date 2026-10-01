"""
SkippyTel phone/glasses state relay — the data side of zPhone's mirror.

Skippy core has no HTML interface any more (the Captain's decision: the browser
UI lives on the cloud portal). The old single-page mirror at GET /portal, the
legacy GET /portal/hud snapshot that only that page read, and the unused
inline "legacy" page were removed. Everything below is DATA that the shelved
phone/glasses apps (SkippyChat / SkippyDroid zHUD) push to and read from.

Endpoints:
  GET  /portal/history                — last N chat messages (JSON)
  GET  /portal/events                 — SSE stream of new messages
  GET  /portal/image/<id>             — base64 image for a message

  POST /portal/hud-state              — zHUD pushes HudFeed JSON here
  GET  /portal/hud-state              — current HudFeed snapshot
  GET  /portal/hud-state/events       — SSE stream of HudFeed deltas

  POST /portal/chat-state             — SkippyChat pushes ChatFeed JSON here
  GET  /portal/chat-state             — current ChatFeed snapshot
  GET  /portal/chat-state/events      — SSE stream of ChatFeed deltas

  POST /portal/hud-frame              — zHUD posts its rendered JPEG here
  GET  /portal/hud-frame.jpg          — the latest frame
  GET  /portal/hud-stream             — MJPEG stream of the frames

  POST /portal/droid-state            — DEPRECATED back-compat alias for hud-state
  GET  /portal/droid-state            — DEPRECATED — reads from _hud_state

Architecture:
  Source of truth = the phone (zPhone software running on emulator/S23).
  This module just receives state pushes, caches them, and fans out to
  any connected SSE subscribers.

Internal-network only (Tailscale / LAN). No authentication.
"""
import json
import queue as _queue
import threading
import time

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app import conversation_store as cs

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
    # Hardpoint candy state — SkippyChat pushes every ~5 s, so a viewer of
    # /portal/chat-state(/events) can render the candy without physical glasses.
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
