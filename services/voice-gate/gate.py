"""voice-gate: the portal's ears and mouth for Skippy, through the one door skippy-pc has open.

Tailscale Funnel carries https://skippy-pc.<tailnet>.ts.net:10000 to this container (published on 127.0.0.1:3007 only), so
the portal's chats (/chat and the personal assistant on /me) can hear a microphone and speak in Skippy's voice on any device
without the device joining the tailnet. The portal renders the page and signs short-lived passes; this gate shows the
internet exactly two things:

    GET  /health                     {"ok", "ready", "whisper", "tts"}                 (no pass: the reporter's probe)
    POST /t/<pass>/stt[?lang=xx]     body = one recording (webm/ogg/mp4/wav, at most 4 MB) -> {"text", "lang"}   (Whisper, at home)
    POST /t/<pass>/tts               {"text"} (at most 500 chars) -> audio/mpeg, Skippy's voice (ElevenLabs via flask-api)

Passes work like the music and video gates', with their own label and signed text, so none of the three opens another. Two
scopes: "listen" (anyone the portal lets chat: /stt only) and "speak" (the Captain's: /stt and /tts). The cloned Skippy voice is
for personal use only, so /tts is refused to any other pass. ElevenLabs' key stays in flask-api; this process never holds it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time

import requests
from flask import Flask, Response, abort, request
from werkzeug.exceptions import HTTPException

log = logging.getLogger("voice-gate")

MAX_PASS_S = 2 * 24 * 3600
TOKEN_RE = re.compile(r"([0-9]{9,11})\.([0-9a-f]{32})")
LANG_RE = re.compile(r"[a-z]{2,3}")
AUDIO_TYPES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/mpeg": ".mp3", "audio/wav": ".wav",
               "audio/x-wav": ".wav", "audio/aac": ".aac", "audio/x-m4a": ".m4a"}
MAX_AUDIO = 4 * 1024 * 1024
MAX_TEXT = 500
MESSAGES = {400: "bad request", 403: "forbidden", 404: "not found", 405: "method not allowed", 413: "too large",
            415: "unsupported audio", 429: "too many requests", 500: "something went wrong",
            502: "the voice service sent something unusable", 503: "the voice service is not answering"}


def gate_key(home_token: str) -> bytes:
    """Must match portal/voice.py gate_key(): its own label, so no other gate's pass opens this one."""
    hashed = hashlib.sha256(home_token.encode()).hexdigest()
    return hmac.new(hashed.encode(), b"voice-gate-v1", hashlib.sha256).digest()


def sign(key: bytes, exp: int, scope: str = "listen") -> str:
    text = f"voice-speak:{exp}" if scope == "speak" else f"voice:{exp}"
    return f"{exp}.{hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:32]}"


def valid(key: bytes, token: str, now: float) -> str | None:
    """The pass's scope ("speak" or "listen"), or None when it isn't a good pass."""
    m = TOKEN_RE.fullmatch(token or "")
    if not m or not key:
        return None
    exp = int(m.group(1))
    if not now <= exp <= now + MAX_PASS_S:
        return None
    for scope in ("speak", "listen"):
        if hmac.compare_digest(sign(key, exp, scope).encode(), token.encode()):
            return scope
    return None


def create_app(key: bytes, whisper_url: str, tts_url: str, clock=time.time, slots: int = 3,
               post=requests.post, get=requests.get) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.url_map.merge_slashes = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_AUDIO + 4096
    busy = threading.BoundedSemaphore(slots)          # the GPU is shared with everything else at home: a few at a time

    def err(code: int) -> Response:
        return Response(json.dumps({"error": MESSAGES.get(code, "request failed")}), status=code,
                        mimetype="application/json", headers={"Cache-Control": "no-store"})

    @app.before_request
    def gatekeeper():
        path = request.path
        if request.method == "OPTIONS" and (path == "/health" or path.startswith("/t/")):
            return Response(status=204)
        if path == "/health":
            return None
        m = re.match(r"/t/([^/]*)(?:/|$)", path)
        if not m:
            abort(404)
        scope = valid(key, m.group(1), clock())
        if not scope:
            abort(403)
        request.environ["voice_gate.scope"] = scope
        return None

    @app.after_request
    def headers(resp: Response):
        resp.headers["Access-Control-Allow-Origin"] = "*"          # the pass is the credential, never a cookie
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.method == "OPTIONS":
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    @app.errorhandler(HTTPException)
    def http_error(e: HTTPException):
        return err(e.code or 500)

    @app.errorhandler(Exception)
    def crash(e: Exception):
        log.error("voice-gate: %s in a handler", type(e).__name__)      # the class only: the URL carries the pass
        return err(500)

    def probe(url: str) -> bool:
        try:
            return get(url, timeout=3).status_code == 200
        except requests.RequestException:
            return False

    @app.get("/health")
    def health():
        w, t = probe(f"{whisper_url}/health"), probe(f"{tts_url}/tts/health")
        return Response(json.dumps({"ok": True, "ready": w, "whisper": w, "tts": t}), mimetype="application/json",
                        headers={"Cache-Control": "no-store"})

    @app.post("/t/<token>/stt")
    def stt(token):
        ctype = (request.mimetype or "").lower()
        suffix = AUDIO_TYPES.get(ctype)
        if not suffix:
            return err(415)
        lang = request.args.get("lang", "")
        if lang and not LANG_RE.fullmatch(lang):
            return err(400)
        data = request.get_data()
        if not data:
            return err(400)
        if not busy.acquire(blocking=False):
            return err(429)
        try:
            r = post(f"{whisper_url}/transcribe", files={"audio": ("rec" + suffix, data, ctype)},
                     data={"hint_lang": lang} if lang else {}, timeout=40)
            if r.status_code != 200:
                return err(503)
            out = r.json()
        except requests.RequestException:
            return err(503)
        except ValueError:
            return err(502)
        finally:
            busy.release()
        text = out.get("english_text") if isinstance(out, dict) else None
        if not isinstance(text, str):
            return err(502)
        return Response(json.dumps({"text": text.strip()[:2000], "lang": str(out.get("detected_lang") or "")[:8]}),
                        mimetype="application/json", headers={"Cache-Control": "no-store"})

    @app.post("/t/<token>/tts")
    def tts(token):
        if request.environ.get("voice_gate.scope") != "speak":
            return err(403)                                       # the cloned voice is the Captain's alone
        body = request.get_json(silent=True)
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            return err(400)
        if len(text) > MAX_TEXT:
            return err(413)
        if not busy.acquire(blocking=False):
            return err(429)
        try:
            r = get(f"{tts_url}/tts/stream", params={"text": text.strip()}, timeout=60)
            if r.status_code != 200 or not r.content:
                return err(503)
            audio = r.content
        except requests.RequestException:
            return err(503)
        finally:
            busy.release()
        return Response(audio, mimetype="audio/mpeg", headers={"Cache-Control": "no-store"})

    return app


def from_env() -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    token = os.environ.get("SKIPPY_HOME_TOKEN", "").strip()
    if not token:
        logging.warning("voice-gate: SKIPPY_HOME_TOKEN isn't set; every pass will be refused")
    return create_app(gate_key(token) if token else b"", os.environ.get("WHISPER_URL", "http://whisper:8000").rstrip("/"),
                      os.environ.get("TTS_URL", "http://flask-api:5000").rstrip("/"))
