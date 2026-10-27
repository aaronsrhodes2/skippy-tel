"""music-gate: the only piece of skippy-pc on the public internet, and all it does is hand out the Captain's music.

Tailscale Funnel carries https://skippy-pc.<tailnet>.ts.net/ to this container (published on 127.0.0.1:3005 only), so
Bilby's Audio Service on the portal plays the library on any device, the Tesla included, without the device joining the tailnet.

Nothing here works without a pass: every path but /health starts with /t/<exp>.<sig>/, a signature only the portal can
make. The portal signs with a key derived from the home token's sha256 (the one thing both sides already hold: the
portal keeps the hash in VM metadata, skippy-pc keeps the token in the vault), so there's no new secret to set up.
A pass lasts hours, not days; the player asks the portal for a fresh one as it runs out.

    GET /health                       {"ok": true, "ready": ..., "tracks": n, "lyrics": n}   (no pass: the reporter's probe)
    GET /t/<pass>/catalog.json        the whole library, gzipped, with an ETag (library.py)
    GET /t/<pass>/lyrics.json         {"v":1,"by":{<catalog path>: "one sentence about the song"}}, gzipped, ETag (lyrics.py);
                                      an empty `by` until skippy-core's flask-api has answered, or if it is down
    GET /t/<pass>/a/<path>            one track, with Range (seeking; Safari insists on it)
    GET /t/<pass>/art/<path>          its embedded cover, as it is in the file
    GET /t/<pass>/art/<path>?w=192    ...as a JPEG thumbnail; w is 96, 192, 384 or 768 (anything else snaps to the nearest, a
                                      non-number is a 400), cached on disk and bounded (art.py)

The player lives on the portal's origin, so a response's ETag is exposed to it and If-None-Match is allowed in its requests.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file
from flask import g as _g

import ready as ready_mod

from art import Busy, ThumbCache, pick_size
from library import AUDIO, Library, etag_matches, read_art
from lyrics import DEFAULT_URL, LEVELS, LyricsStore

MAX_PASS_S = 2 * 24 * 3600    # a pass claiming to last longer than this is refused, whoever signed it


def gate_key(home_token: str) -> bytes:
    """The signing key, from the home token's sha256 (hex): the portal only has the hash, so both sides start there."""
    hashed = hashlib.sha256(home_token.encode()).hexdigest()
    return hmac.new(hashed.encode(), b"music-gate-v1", hashlib.sha256).digest()


def sign(key: bytes, exp: int) -> str:
    return f"{exp}.{hmac.new(key, f'music:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]}"


def valid(key: bytes, token: str, now: float) -> bool:
    m = re.fullmatch(r"(\d{9,11})\.([0-9a-f]{32})", token or "")
    if not m or not key:
        return False
    exp = int(m.group(1))
    if not now <= exp <= now + MAX_PASS_S:
        return False
    return hmac.compare_digest(sign(key, exp), token)


def create_app(library: Library, key: bytes, clock=time.time, lyrics: LyricsStore | None = None,
               thumbs: ThumbCache | None = None) -> Flask:
    app = Flask(__name__)
    lyrics = lyrics or LyricsStore(None, url="")             # none given: an empty `by`, which a player copes with
    thumbs = thumbs or ThumbCache(library.state / "art-cache")
    app.config.update(LIBRARY=library, LYRICS=lyrics, THUMBS=thumbs)     # what the tests (and from_env's wiring check) look at

    @app.after_request
    def headers(resp: Response):
        # The player lives on the portal's origin; a pass is the credential, never a cookie, so any origin may read.
        resp.headers["Access-Control-Allow-Origin"] = "*"
        # ETag must be exposed or the player can't read it to cache the catalog by it; and it sends If-None-Match itself,
        # which isn't a "simple" header, so the browser asks permission (an OPTIONS request) first.
        resp.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, ETag"
        resp.headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "If-None-Match, If-Modified-Since, Range, If-Range"
        resp.headers["Access-Control-Max-Age"] = "86400"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    def check(token: str) -> None:
        if not valid(key, token, clock()):
            abort(403)

    ready_state = ready_mod.Readiness("music-gate")

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
        rep = ready_state.report(ready=bool(library.ready), detail="" if library.ready else "the music library is still being read",
                                 ok=True, tracks=library.count, scanning=library.scanning, lyrics=lyrics.count)
        return jsonify(rep)

    @app.get("/t/<token>/catalog.json")
    def catalog(token):
        check(token)
        if not library.catalog_gz:
            return jsonify(error="the library is still being read; try again in a minute"), 503
        if etag_matches(request.headers.get("If-None-Match"), library.etag):
            return Response(status=304, headers={"ETag": library.etag, "Cache-Control": "private, no-cache"})
        return Response(library.catalog_gz, mimetype="application/json",
                        headers={"Content-Encoding": "gzip", "ETag": library.etag, "Cache-Control": "private, no-cache",
                                 "Vary": "Accept-Encoding"})

    @app.get("/t/<token>/lyrics.json")
    def lyric_lines(token):
        check(token)
        gz, etag = lyrics.served
        if etag_matches(request.headers.get("If-None-Match"), etag):
            return Response(status=304, headers={"ETag": etag, "Cache-Control": "private, no-cache"})
        return Response(gz, mimetype="application/json",
                        headers={"Content-Encoding": "gzip", "ETag": etag, "Cache-Control": "private, no-cache",
                                 "Vary": "Accept-Encoding"})

    @app.get("/t/<token>/a/<path:rel>")
    def audio(token, rel):
        check(token)
        full = library.resolve(rel)
        if not full:
            abort(404)
        return send_file(full, mimetype=AUDIO[full.suffix.lower()], conditional=True, max_age=86400)

    @app.get("/t/<token>/art/<path:rel>")
    def art(token, rel):
        check(token)
        width = None
        if "w" in request.args:
            width = pick_size(request.args["w"])
            if width is None:
                return jsonify(error="w must be a whole number of pixels (96, 192, 384 or 768)"), 400
        full = library.resolve(rel)
        if not full:
            abort(404)
        if width is None:                                    # no ?w=: the cover as it is in the file
            found = read_art(full)
            if not found:
                abort(404)
            data, mime = found
            return Response(data, mimetype=mime, headers={"Cache-Control": "private, max-age=604800"})
        try:
            made = thumbs.get(rel, full, width, lambda: read_art(full))
        except Busy:                                         # a burst of uncached covers: ask again in a moment
            return jsonify(error="busy making thumbnails; try again"), 503, {"Retry-After": "1"}
        if not made:
            abort(404)
        etag, data = made
        resp = Response(data, mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})
        resp.set_etag(etag)
        return resp.make_conditional(request)

    return app


def from_env() -> Flask:
    """gunicorn's entry point: the library at /music, the tag cache in /state, the key from SKIPPY_HOME_TOKEN."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    token = os.environ.get("SKIPPY_HOME_TOKEN", "").strip()
    if not token:
        logging.warning("music-gate: SKIPPY_HOME_TOKEN isn't set; every pass will be refused")
    state = os.environ.get("STATE_DIR", "/state")
    library = Library(os.environ.get("MUSIC_ROOT", "/music"), state)
    # The lyric summaries are matched against the catalog, so they're matched again whenever the catalog is rebuilt.
    mode = os.environ.get("LYRICS_MATCH", "song").strip().lower()
    if mode not in LEVELS:
        logging.warning("music-gate: LYRICS_MATCH=%r isn't one of %s; using 'song'", mode, "/".join(LEVELS))
        mode = "song"
    lyrics = LyricsStore(state, os.environ.get("LYRICS_URL", DEFAULT_URL), with_songs=mode == "song")
    lyrics.rebuild(library.identities)
    library.on_build.append(lyrics.rebuild)
    threading.Thread(target=library.run_forever, daemon=True, name="library-scan").start()
    if lyrics.url:
        threading.Thread(target=lyrics.run_forever, daemon=True, name="lyrics-refresh").start()
    try:
        cache_mb = float(os.environ.get("ART_CACHE_MB", "600"))
    except ValueError:                                       # a typo in the env must not stop the music
        logging.warning("music-gate: ART_CACHE_MB isn't a number; using 600")
        cache_mb = 600.0
    thumbs = ThumbCache(Path(state) / "art-cache", int(max(cache_mb, 1) * 1024 * 1024))
    return create_app(library, gate_key(token) if token else b"", lyrics=lyrics, thumbs=thumbs)
