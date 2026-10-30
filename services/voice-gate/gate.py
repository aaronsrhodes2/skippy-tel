"""voice-gate: the portal's ears and mouth for Skippy, through the one door skippy-pc has open.

Tailscale Funnel carries https://skippy-pc.<tailnet>.ts.net:10000 to this container (published on 127.0.0.1:3007 only), so
the portal's chats (/chat and the personal assistant on /me) can hear a microphone and speak in Skippy's voice on any device
without the device joining the tailnet. The portal renders the page and signs short-lived passes; this gate shows the
internet exactly two things:

    GET  /health                     {"ok", "ready", "whisper", "tts", "voices"}           (no pass: the reporter's probe)
    POST /t/<pass>/stt[?lang=xx]     body = one recording (webm/ogg/mp4/wav, at most 4 MB) -> {"text", "lang"}   (Whisper, at home)
    POST /t/<pass>/tts               {"text", "voice"?} (at most 500 chars) -> audio/mpeg in the pass's voice (or "stock"): see VOICES
    POST /t/<pass>/summary           {"text"} (at most 8000 chars) -> {"text}: what a person would SAY aloud about it, 2 to 4 short
                                     sentences (the home Ollama writes it); any pass. A long reply is spoken as this, not word for word

Passes work like the music and video gates', with their own label and signed text, so none of the three opens another. A pass
names the one voice it may speak in (the portal decides who gets which; this gate only checks the signature):

    stock       Kokoro-82M, a stock voice, rendered here at home by tts-studio. Everyone the portal lets chat (the Public mode).
    skippy      the cloned Skippy voice, F5-TTS zero-shot, rendered here at home by tts-studio. The Captain (the Private mode).

Any voice pass may also ask for "stock". The cloned voices are for personal use only, so no pass the portal gives a guest
can reach them.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
import logging
import os
import re
import subprocess
import threading
import time

import requests
from flask import Flask, Response, abort, request
from flask import g as _g

import ready as ready_mod
from werkzeug.exceptions import HTTPException

log = logging.getLogger("voice-gate")

MAX_PASS_S = 2 * 24 * 3600
TOKEN_RE = re.compile(r"([0-9]{9,11})\.([0-9a-f]{32})")
LANG_RE = re.compile(r"[a-z]{2,3}")
AUDIO_TYPES = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/mpeg": ".mp3", "audio/wav": ".wav",
               "audio/x-wav": ".wav", "audio/aac": ".aac", "audio/x-m4a": ".m4a"}
MAX_AUDIO = 4 * 1024 * 1024
MAX_TEXT = 500
MAX_SUMMARY_IN = 8000


SUMMARY_PROMPT = (
    "You turn a written chat reply into what a person would SAY out loud to a friend sitting next to them. Keep the key facts, "
    "numbers, names and any question that was asked, in 2 to 4 short sentences, under 60 words in all. Keep the writer's attitude "
    "and first person. Say nothing that cannot be spoken: no markdown, no code, no URLs, no file paths, no symbols read out (say "
    "'a link' or the name instead). If it lists steps, say how many and the main one. Reply with ONLY the spoken words.\n\n"
    "THE REPLY:\n")
MESSAGES = {400: "bad request", 403: "forbidden", 404: "not found", 405: "method not allowed", 413: "too large",
            415: "unsupported audio", 429: "too many requests", 500: "something went wrong",
            502: "the voice service sent something unusable", 503: "the voice service is not answering"}


def gate_key(home_token: str) -> bytes:
    """Must match portal/voice.py gate_key(): its own label, so no other gate's pass opens this one."""
    hashed = hashlib.sha256(home_token.encode()).hexdigest()
    return hmac.new(hashed.encode(), b"voice-gate-v1", hashlib.sha256).digest()


VOICES = ("stock", "skippy")
SCOPES = ("listen",) + VOICES


def sign(key: bytes, exp: int, scope: str = "listen") -> str:
    """scope: "listen" or a voice name. Must match portal/voice.py sign()."""
    text = f"voice-tts-{scope}:{exp}" if scope in VOICES else f"voice:{exp}"
    return f"{exp}.{hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:32]}"


def valid(key: bytes, token: str, now: float) -> str | None:
    """The pass's scope ("listen" or a voice name), or None when it isn't a good pass."""
    m = TOKEN_RE.fullmatch(token or "")
    if not m or not key:
        return None
    exp = int(m.group(1))
    if not now <= exp <= now + MAX_PASS_S:
        return None
    for scope in SCOPES:
        if hmac.compare_digest(sign(key, exp, scope).encode(), token.encode()):
            return scope
    return None


def voice_of(scope: str | None) -> str | None:
    """The voice a pass may speak in: None for a listen pass."""
    return scope if scope in VOICES else None


def allowed(scope: str | None, wanted: str | None) -> str | None:
    """The voice this request gets: the pass's own when it asks for none, "stock" from any voice pass, else None (refused)."""
    granted = voice_of(scope)
    if not granted:
        return None
    if wanted in (None, "", granted):
        return granted
    return "stock" if wanted == "stock" else None


class Voice:
    """A cache of what was already said (the same words never cost twice to render).
    state_dir="" keeps it all in memory (tests); otherwise files in the cache directory."""

    def __init__(self, state_dir: str, clock, cache_keep: int = 300):
        self.dir, self.clock, self.keep = state_dir, clock, cache_keep
        self._lock, self._mem = threading.Lock(), {}
        if state_dir:
            pathlib.Path(state_dir, "tts-cache").mkdir(parents=True, exist_ok=True)

    def cached(self, text: str) -> bytes | None:
        if not self.dir:
            return self._mem.get(text)
        try:
            p = self._path(text)
            data = p.read_bytes()
            os.utime(p, None)                              # recently used stays
            return data
        except OSError:
            return None

    def remember(self, text: str, audio: bytes) -> None:
        if not self.dir:
            self._mem[text] = audio
            return
        try:
            self._path(text).write_bytes(audio)
            files = sorted(pathlib.Path(self.dir, "tts-cache").glob("*.mp3"), key=lambda f: f.stat().st_mtime)
            for old in files[:-self.keep]:
                old.unlink()
        except OSError:
            pass

    def _path(self, text: str) -> pathlib.Path:
        return pathlib.Path(self.dir, "tts-cache", hashlib.sha256(text.encode()).hexdigest()[:32] + ".mp3")


def spoken(text: str) -> str:
    """The model's answer made safe to speak: no markdown marks, one line, at most 400 characters (cut at a sentence)."""
    t = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)
    t = re.sub(r"https?://\S+", "", re.sub(r"[`*_#>~|]+", "", t))
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 400:
        cut = t[:400]
        at = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = cut[:at + 1] if at > 150 else cut
    return t.strip()


def create_app(key: bytes, whisper_url: str, studio_url: str, clock=time.time,
               slots: int = 3, post=requests.post, get=requests.get,
               ollama_url: str = "", summary_model: str = "qwen2.5:7b",
               state_dir: str = "") -> Flask:
    app = Flask(__name__, static_folder=None)
    app.url_map.merge_slashes = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_AUDIO + 4096
    busy = threading.BoundedSemaphore(slots)          # the GPU is shared with everything else at home: a few at a time
    voice = Voice(state_dir, clock)

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

    def studio_voices() -> dict:
        """Which local voices can speak right now, from tts-studio's own /health."""
        out = dict.fromkeys(VOICES, False)
        if studio_url:
            try:
                r = get(f"{studio_url}/health", timeout=3)
                got = r.json().get("voices") if r.status_code == 200 else None
                if isinstance(got, dict):
                    out = {v: got.get(v) is True for v in VOICES}
            except (requests.RequestException, ValueError, AttributeError):
                pass
        return out

    ready_state = ready_mod.Readiness("voice-gate")

    @app.before_request
    def _job_begins():
        if request.path != "/health" and request.method != "OPTIONS":      # a call that does work is a job while it runs (PLATINUM section 24)
            _g._job = ready_state.begin(request.path)

    @app.teardown_request
    def _job_ends(_exc):
        token = _g.pop("_job", None)
        if token:
            ready_state.end(token)

    @app.get("/health")
    def health():
        w = probe(f"{whisper_url}/health")
        deps = {"whisper": {"ready": bool(w), "detail": "" if w else "Whisper (speech to text, at home) isn't answering"}}
        rep = ready_state.report(ready=True, deps=deps, ok=True, whisper=w, voices=dict(studio_voices()),
                                 tts=bool(studio_url))
        return Response(json.dumps(rep), mimetype="application/json", headers={"Cache-Control": "no-store"})

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
        try:                                    # how sure Whisper is of the language: the chat's Hands-off writes another language down as heard speech
            sure = round(min(1.0, max(0.0, float(out.get("confidence")))), 2)
        except (TypeError, ValueError):
            sure = 0.0
        return Response(json.dumps({"text": text.strip()[:2000], "lang": str(out.get("detected_lang") or "")[:8], "sure": sure}),
                        mimetype="application/json", headers={"Cache-Control": "no-store"})

    @app.post("/t/<token>/tts")
    def tts(token):
        body = request.get_json(silent=True)
        wanted = body.get("voice") if isinstance(body, dict) else None
        if wanted is not None and wanted not in VOICES:
            return err(400)
        which = allowed(request.environ.get("voice_gate.scope"), wanted)
        if not which:
            return err(403)                                       # the cloned voices are the Captain's alone
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            return err(400)
        if len(text) > MAX_TEXT:
            return err(413)
        out = local(which, text.strip())
        return out

    def local(which: str, text: str) -> Response:
        """A local voice from tts-studio: no budget (it costs nothing), the same cache, and streamed through as it renders, so the
        first sentence plays while the rest is still being made."""
        key_text = f"{which}|{text}"
        said = voice.cached(key_text)
        if said is not None:
            return Response(said, mimetype="audio/mpeg", headers={"Cache-Control": "no-store", "X-Voice-Cache": "hit", "X-Voice": which})
        if not studio_url:
            return err(503)
        if not busy.acquire(blocking=False):
            return err(429)
        try:
            r = post(f"{studio_url}/speak", json={"text": text, "voice": which}, stream=True, timeout=(5, 240))   # a cold load on a busy disk took 190 s
        except requests.RequestException:
            busy.release()
            return err(503)
        if r.status_code != 200:
            r.close()
            busy.release()
            return err(503)

        def relay():
            got, whole = [], False
            try:
                for piece in r.iter_content(chunk_size=None):
                    if piece:
                        got.append(piece)
                whole = True
            except requests.RequestException:
                log.warning("voice-gate: tts-studio broke off a %s line", which)
            finally:
                r.close()
                if whole and got:
                    voice.remember(key_text, b"".join(got))
            yield from got

        resp = Response(relay(), mimetype="audio/mpeg", headers={"Cache-Control": "no-store", "X-Voice": which})
        resp.call_on_close(busy.release)
        return resp

    @app.post("/t/<token>/summary")
    def summary(token):
        body = request.get_json(silent=True)
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            return err(400)
        if len(text) > MAX_SUMMARY_IN:
            return err(413)
        if not ollama_url:
            return err(503)
        if not busy.acquire(blocking=False):
            return err(429)
        try:
            r = post(f"{ollama_url}/api/generate", json={"model": summary_model, "prompt": SUMMARY_PROMPT + text.strip(), "stream": False,
                                                          "options": {"temperature": 0.3, "num_predict": 160}, "keep_alive": "30m"}, timeout=45)
            if r.status_code != 200:
                return err(503)
            said = r.json().get("response") if isinstance(r.json(), dict) else None
        except requests.RequestException:
            return err(503)
        except ValueError:
            return err(502)
        finally:
            busy.release()
        said = spoken(said) if isinstance(said, str) else ""
        if not said:
            return err(502)
        return Response(json.dumps({"text": said}), mimetype="application/json", headers={"Cache-Control": "no-store"})

    return app


def from_env() -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    token = os.environ.get("SKIPPY_HOME_TOKEN", "").strip()
    if not token:
        logging.warning("voice-gate: SKIPPY_HOME_TOKEN isn't set; every pass will be refused")
    return create_app(gate_key(token) if token else b"", os.environ.get("WHISPER_URL", "http://whisper:8000").rstrip("/"),
                      studio_url=os.environ.get("TTS_STUDIO_URL", "http://tts-studio:8003").rstrip("/"),
                      ollama_url=os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/"),
                      summary_model=os.environ.get("SUMMARY_MODEL", "qwen2.5:7b"),
                      state_dir=os.environ.get("STATE_DIR", "/state"))
