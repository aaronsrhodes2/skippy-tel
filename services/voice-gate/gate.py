"""voice-gate: the portal's ears and mouth for Skippy, through the one door skippy-pc has open.

Tailscale Funnel carries https://skippy-pc.<tailnet>.ts.net:10000 to this container (published on 127.0.0.1:3007 only), so
the portal's chats (/chat and the personal assistant on /me) can hear a microphone and speak in Skippy's voice on any device
without the device joining the tailnet. The portal renders the page and signs short-lived passes; this gate shows the
internet exactly two things:

    GET  /health                     {"ok", "ready", "whisper", "tts", "voices"}           (no pass: the reporter's probe)
    POST /t/<pass>/stt[?lang=xx][&raw=1]   body = one recording (webm/ogg/mp4/wav, at most 4 MB) -> {"text", "lang", "sure"[, "raw"]}
                                     (Whisper, at home; `raw` = the speech in its own language, when it isn't English and raw=1 asked)
    POST /t/<pass>/tts               {"text", "voice"?, "whole"?: true} (at most 500 chars) -> audio/mpeg in the pass's voice (or "stock"): see VOICES
    POST /t/<pass>/summary           {"text"} (at most 8000 chars) -> {"text}: what a person would SAY aloud about it, 2 to 4 short
                                     sentences (the home Ollama writes it); any pass. A long reply is spoken as this, not word for word
    GET  /t/<pass>/wake.json         {"ok", "threshold", "patience"}: the trained "Skippy" wake word, for a browser that runs it itself
    GET  /t/<pass>/wake/<file>       melspectrogram.onnx, embedding_model.onnx, skippy.onnx (the Drive page's static/skippy-wake.js)
                                     The Captain's pass only ("skippy"): the model was trained on his own voice

Passes work like the music and video gates', with their own label and signed text, so none of the three opens another. A pass
names the one voice it may speak in (the portal decides who gets which; this gate only checks the signature):

    stock       Kokoro-82M, a stock voice, rendered here at home by tts-studio. Everyone the portal lets chat (the Public mode).
    skippy      the cloned Skippy voice, F5-TTS zero-shot, rendered here at home by tts-studio. The Captain (the Private mode).

Any voice pass may also ask for "stock". The cloned voices are for personal use only, so no pass the portal gives a guest
can reach them.
"""
from __future__ import annotations

import collections
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


DOOMED_TRIES = 3          # the tries (one per store) a cache file that would not go is given before it is left for a restart: a few, so a folder nothing can be removed from costs each store a few unlinks, not a growing list


class Voice:
    """A cache of what was already said (the same words never cost twice to render).
    state_dir="" keeps it all in memory (tests); otherwise files in the cache directory.

    Storing a line never holds its reply back (2026-11-05, measured on the live mount: 319 ms median, 258 to 444 ms, before the first byte of every line that was
    not cached). The cache directory is a Windows bind mount, where one stat() costs about 0.85 ms, and the old store stat()ed all 300 cached files after every
    render to find the oldest. Now `remember` keeps the audio in memory at once (so the same words are a hit straight away) and a background thread writes the file
    and trims the cache from an order kept in memory, read from the directory one time: a store costs one write, and one unlink past `keep` files.

    A file is written whole or not there at all (its own temp file, then os.replace), and a file that would not go when its line left the cache (on that mount a file
    a reader has open cannot be unlinked) is tried again at the next stores (DOOMED_TRIES in all), so the folder ends at its cap without a restart, also after a burst of stores. Every file in
    the folder is a line of the cache (listed in `_order`), a line on its way (pending), or on the list `_doomed` of files to remove; a name leaves that list only when its file is gone, its line
    is a line of the cache again (or on its way, and a store that fails puts it back), or it was tried DOOMED_TRIES times and refused every time."""

    def __init__(self, state_dir: str, clock, cache_keep: int = 300):
        self.dir, self.clock, self.keep = state_dir, clock, cache_keep
        self._lock, self._mem = threading.Lock(), {}
        self._pending: dict[str, bytes] = {}               # lines accepted but not on disk yet: file name -> audio
        self._order: collections.OrderedDict | None = None  # the cache files, least recently used first; None until the first store reads the directory
        self._doomed: collections.OrderedDict = collections.OrderedDict()   # lines that left the cache whose files are not removed yet, oldest first: file name -> the times removing it was refused
        self._writers: set = set()
        if state_dir:
            pathlib.Path(state_dir, "tts-cache").mkdir(parents=True, exist_ok=True)

    def cached(self, text: str) -> bytes | None:
        if not self.dir:
            return self._mem.get(text)
        p = self._path(text)
        with self._lock:
            held = self._pending.get(p.name)
        if held is not None:
            return held
        try:
            data = p.read_bytes()
            os.utime(p, None)                              # recently used stays (also over a restart: the order is rebuilt from these times)
        except OSError:
            return None
        with self._lock:
            if self._order is not None and p.name in self._order:
                self._order.move_to_end(p.name)
        return data

    def remember(self, text: str, audio: bytes) -> None:
        if not self.dir:
            self._mem[text] = audio
            return
        name = self._path(text).name
        writer = threading.Thread(target=self._store, args=(name,), name="voice-cache", daemon=True)
        with self._lock:
            self._pending[name] = audio
            self._writers.add(writer)
            writer.start()                                 # (inside the lock: wait() never meets a writer that has not started)

    def wait(self, timeout: float = 5.0) -> bool:
        """Until the stores in flight are on disk (tests, a tidy shutdown). True when none is left."""
        end = time.monotonic() + timeout
        while True:
            with self._lock:
                running = list(self._writers)
            if not running:
                return True
            if time.monotonic() >= end:
                return False
            for w in running:
                w.join(max(0.0, end - time.monotonic()))

    def _store(self, name: str) -> None:
        folder = pathlib.Path(self.dir, "tts-cache")
        tmp = folder / f"{name}.{os.urandom(6).hex()}.tmp"      # its own temp file: two stores of the same words at once never write the one the other is renaming
        audio = None
        try:
            with self._lock:
                audio = self._pending.get(name)
            if audio is None:
                return
            tmp.write_bytes(audio)
            os.replace(tmp, folder / name)                 # all of it or not there: a reader never meets half a file
            order = self._known()
            with self._lock:
                order[name] = None
                order.move_to_end(name)
                if self._pending.get(name) is audio:       # listed: a line of the cache from here on, like any other. (Pending until this store's pass is over, it could be evicted in a burst by
                    del self._pending[name]                # another store meanwhile, and the retry would take "pending" for "said again" and drop it from the list: its file would stay.
                                                           # A newer take of the same words, still on its way, is left pending: that is its own.)
                while len(order) > self.keep:
                    self._doomed[order.popitem(last=False)[0]] = 0         # (refusals so far: none; nothing is ever taken off this list before its file was tried, see _unlink_doomed)
            self._unlink_doomed(folder)
        except OSError:
            try:
                tmp.unlink()                               # a store that died midway takes its temp file with it
            except OSError:
                pass
            with self._lock:
                if self._pending.get(name) is audio:       # (this store is over: it is no reason to keep a file, see _unlink_doomed)
                    del self._pending[name]
                if self._order is not None and name not in self._order:
                    self._doomed.setdefault(name, 0)       # an older file of these words may be on disk, its entry dropped because this store was on its way to replace it (Windows refuses
                                                           # a replace over a file another store is replacing): nothing on disk stays unlisted. A name no file belongs to is dropped at the pass.
            self._unlink_doomed(folder)
        finally:
            with self._lock:
                if self._pending.get(name) is audio:
                    self._pending.pop(name, None)
                self._writers.discard(threading.current_thread())

    def _unlink_doomed(self, folder: pathlib.Path) -> None:
        """Remove the files of the lines that left the cache. A file that will not go (a reader has it open: a sharing error on the Windows bind mount) stays on the list
        and is tried again at the next store, DOOMED_TRIES times in all, and is then left to the next restart (which reads the folder again); one that is gone already is dropped; a line
        said again meanwhile is a line of the cache again, not a leftover. Only a file that was tried and refused that often is ever dropped unremoved: a list cut back to the cap whatever it
        held loses, in a burst of stores (600 in a second, 16 threads: several hundred files left over a cap of 40), the names of files nobody had tried to remove."""
        with self._lock:
            mine = collections.deque(self._doomed.items())
            self._doomed.clear()                           # these files are this pass's now: a store running at the same time finds none of them to try as well (if every store of a burst went
                                                           # down the whole list, 2000 of them took 76 s, and Windows refuses a file another thread is deleting)
        try:
            while mine:
                name, refused = mine[0]
                with self._lock:
                    live = name in self._pending or (self._order is not None and name in self._order)       # (decided now, not when the list was made)
                if not live:
                    try:
                        (folder / name).unlink()
                    except FileNotFoundError:
                        pass
                    except OSError:
                        refused = (refused or 0) + 1
                        if refused < DOOMED_TRIES:
                            with self._lock:
                                self._doomed[name] = refused   # (back on the list, behind the others)
                        else:
                            log.warning("voice-gate: the cache file of a line that left it would not go %d times, left for a restart", refused)
                mine.popleft()
        finally:
            if mine:                                       # (an error that is no OSError cut the pass short: what it did not get to goes back, untried)
                with self._lock:
                    for name, refused in mine:
                        self._doomed.setdefault(name, refused)

    def _known(self) -> collections.OrderedDict:
        """The cache files, least recently used first: the directory is read once (names and times), after that cached() and remember() keep the order.
        A half-written file left by a crash (".tmp", over a minute old) is swept at that reading."""
        with self._lock:
            if self._order is not None:
                return self._order
        found: list[tuple[float, str]] = []
        try:
            with os.scandir(pathlib.Path(self.dir, "tts-cache")) as entries:
                for e in entries:
                    try:
                        if e.name.endswith(".mp3"):
                            found.append((e.stat().st_mtime, e.name))
                        elif e.name.endswith(".tmp") and time.time() - e.stat().st_mtime > 60:
                            os.unlink(e.path)
                    except OSError:
                        pass
        except OSError:
            pass
        found.sort()
        with self._lock:
            if self._order is None:
                self._order = collections.OrderedDict((name, None) for _, name in found)
            return self._order

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
               state_dir: str = "", wake_dirs: tuple = (), wake_threshold: float = 0.5,
               health_ttl: float = 5.0, health_clock=time.monotonic, whole_default: bool = False) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.url_map.merge_slashes = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_AUDIO + 4096
    busy = threading.BoundedSemaphore(slots)          # the GPU is shared with everything else at home: a few at a time
    voice = Voice(state_dir, clock)
    app.extensions["voice_cache"] = voice                # (the tests wait on its background stores)

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

    # /health needs no pass and the direct front door (edge/Caddyfile, /voice/health) makes it reachable from anywhere. Each of its two dependency probes can wait
    # 3 s on a sick Whisper or studio, and this gate has only a few threads, so a handful of anonymous calls per second would stall Skippy's own voice (reproduced
    # 2026-11-05: one call 6 s, an authenticated call 4.8 s behind eight). The answer about the dependencies is therefore kept `health_ttl` seconds, and a stale
    # one is returned at once while ONE background probe refreshes it; only the first ever call waits (video-gate keeps its Jellyfin ping 5 s for the same reason).
    memo: dict = {"at": None, "whisper": False, "voices": {}, "busy": False}
    memo_lock = threading.Lock()

    def refill() -> tuple[bool, dict]:
        try:
            w = probe(f"{whisper_url}/health")
            v = dict(studio_voices())
        except Exception:
            with memo_lock:
                memo["busy"] = False
            raise
        with memo_lock:
            memo.update(at=health_clock(), whisper=w, voices=v, busy=False)
        return w, v

    def dependencies() -> tuple[bool, dict]:
        """(whisper answers, which voices can speak): kept health_ttl seconds; nothing kept yet (or health_ttl 0) means this call asks."""
        with memo_lock:
            at = memo["at"]
            snap = (memo["whisper"], dict(memo["voices"]))
            ask_now = at is None or health_ttl <= 0
            start = (not ask_now) and not (0 <= health_clock() - at < health_ttl) and not memo["busy"]
            if start:
                memo["busy"] = True
        if ask_now:
            return refill()
        if start:
            threading.Thread(target=refill, name="voice-health", daemon=True).start()
        return snap

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
        w, voices = dependencies()
        deps = {"whisper": {"ready": bool(w), "detail": "" if w else "Whisper (speech to text, at home) isn't answering"}}
        rep = ready_state.report(ready=True, deps=deps, ok=True, whisper=w, voices=voices,
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
            form = {"hint_lang": lang} if lang else {}
            if request.args.get("raw") == "1":
                form["want_original"] = "1"
            r = post(f"{whisper_url}/transcribe", files={"audio": ("rec" + suffix, data, ctype)}, data=form, timeout=40)
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
        answer = {"text": text.strip()[:2000], "lang": str(out.get("detected_lang") or "")[:8], "sure": sure}
        original = out.get("original_text")
        if request.args.get("raw") == "1" and isinstance(original, str) and original.strip():
            answer["raw"] = original.strip()[:2000]
        return Response(json.dumps(answer), mimetype="application/json", headers={"Cache-Control": "no-store"})

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
        asked = body.get("whole") if isinstance(body, dict) else None
        out = local(which, text.strip(), one_run=asked if isinstance(asked, bool) else whole_default)
        return out

    def local(which: str, text: str, one_run: bool = False) -> Response:
        """A local voice from tts-studio: no budget (it costs nothing) and the same cache. The reply is sent once it is whole (every client here, the
        hub's Gate.tts and the pages' fetch().blob(), reads it whole anyway). `one_run` (the request's "whole": true, or TTS_WHOLE=1 for every request)
        tells the studio so: it renders a reply that fits one F5 batch in ONE run instead of one run per sentence (0.8 to 1.1 s faster for 2 to 3
        sentences, and the hub skips its ffmpeg re-encode of joined pieces, about 0.1 s). The cache is the same either way: the words are the key."""
        key_text = f"{which}|{text}"
        said = voice.cached(key_text)
        if said is not None:
            return Response(said, mimetype="audio/mpeg", headers={"Cache-Control": "no-store", "X-Voice-Cache": "hit", "X-Voice": which})
        if not studio_url:
            return err(503)
        if not busy.acquire(blocking=False):
            return err(429)
        try:
            speak = {"text": text, "voice": which, **({"whole": True} if one_run else {})}
            r = post(f"{studio_url}/speak", json=speak, stream=True, timeout=(5, 240))   # a cold load on a busy disk took 190 s
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

    def wake_file(name: str):
        for d in wake_dirs:
            p = pathlib.Path(d) / name
            if p.is_file():
                return p
        return None

    def captain() -> bool:
        return request.environ.get("voice_gate.scope") == "skippy"

    @app.get("/t/<token>/wake.json")
    def wake_info(token):
        if not captain():
            return err(403)
        ok = all(wake_file(n) for n in WAKE_FILES)
        return Response(json.dumps({"ok": ok, "threshold": wake_threshold, "patience": 2}), mimetype="application/json",
                        headers={"Cache-Control": "no-store"})

    @app.get("/t/<token>/wake/<name>")
    def wake_model(token, name):
        if not captain():
            return err(403)
        p = wake_file(name) if name in WAKE_FILES else None
        if not p:
            return err(404)
        return Response(p.read_bytes(), mimetype="application/octet-stream", headers={"Cache-Control": "private, max-age=3600"})

    return app


WAKE_FILES = ("melspectrogram.onnx", "embedding_model.onnx", "skippy.onnx")


def from_env() -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    token = os.environ.get("SKIPPY_HOME_TOKEN", "").strip()
    if not token:
        logging.warning("voice-gate: SKIPPY_HOME_TOKEN isn't set; every pass will be refused")
    return create_app(gate_key(token) if token else b"", os.environ.get("WHISPER_URL", "http://whisper:8000").rstrip("/"),
                      studio_url=os.environ.get("TTS_STUDIO_URL", "http://tts-studio:8003").rstrip("/"),
                      ollama_url=os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/"),
                      summary_model=os.environ.get("SUMMARY_MODEL", "qwen2.5:7b"),
                      state_dir=os.environ.get("STATE_DIR", "/state"),
                      wake_dirs=tuple(d for d in os.environ.get("WAKE_DIRS", "/wake:/wake-features").split(":") if d),
                      wake_threshold=float(os.environ.get("SATELLITE_WAKE_THRESHOLD") or 0.5),
                      whole_default=os.environ.get("TTS_WHOLE", "").strip() == "1")      # 1: every reply is rendered in as few F5 runs as it fits (see local())
