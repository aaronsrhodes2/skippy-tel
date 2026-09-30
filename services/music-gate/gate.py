"""music-gate: the only piece of skippy-pc on the public internet, and all it does is hand out the Captain's music.

Tailscale Funnel carries https://skippy-pc.<tailnet>.ts.net/ to this container (published on 127.0.0.1:3005 only), so
the Music app on the portal plays the library on any device, the Tesla included, without the device joining the tailnet.

Nothing here works without a pass: every path but /health starts with /t/<exp>.<sig>/, a signature only the portal can
make. The portal signs with a key derived from the home token's sha256 (the one thing both sides already hold: the
portal keeps the hash in VM metadata, skippy-pc keeps the token in the vault), so there's no new secret to set up.
A pass lasts hours, not days; the player asks the portal for a fresh one as it runs out.

    GET /health                       {"ok": true, "ready": ..., "tracks": n}   (no pass: the reporter's probe)
    GET /t/<pass>/catalog.json        the whole library, gzipped (library.py)
    GET /t/<pass>/a/<path>            one track, with Range (seeking; Safari insists on it)
    GET /t/<pass>/art/<path>          its embedded cover
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import threading
import time

from flask import Flask, Response, abort, jsonify, request, send_file

from library import AUDIO, Library, read_art

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


def create_app(library: Library, key: bytes, clock=time.time) -> Flask:
    app = Flask(__name__)

    @app.after_request
    def headers(resp: Response):
        # The player lives on the portal's origin; a pass is the credential, never a cookie, so any origin may read.
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    def check(token: str) -> None:
        if not valid(key, token, clock()):
            abort(403)

    @app.get("/health")
    def health():
        return jsonify(ok=True, ready=library.ready, tracks=library.count, scanning=library.scanning)

    @app.get("/t/<token>/catalog.json")
    def catalog(token):
        check(token)
        if not library.catalog_gz:
            return jsonify(error="the library is still being read; try again in a minute"), 503
        if library.etag and request.headers.get("If-None-Match") == library.etag:
            return Response(status=304, headers={"ETag": library.etag})
        return Response(library.catalog_gz, mimetype="application/json",
                        headers={"Content-Encoding": "gzip", "ETag": library.etag, "Cache-Control": "private, no-cache",
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
        full = library.resolve(rel)
        found = read_art(full) if full else None
        if not found:
            abort(404)
        data, mime = found
        return Response(data, mimetype=mime, headers={"Cache-Control": "private, max-age=604800"})

    return app


def from_env() -> Flask:
    """gunicorn's entry point: the library at /music, the tag cache in /state, the key from SKIPPY_HOME_TOKEN."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    token = os.environ.get("SKIPPY_HOME_TOKEN", "").strip()
    if not token:
        logging.warning("music-gate: SKIPPY_HOME_TOKEN isn't set; every pass will be refused")
    library = Library(os.environ.get("MUSIC_ROOT", "/music"), os.environ.get("STATE_DIR", "/state"))
    threading.Thread(target=library.run_forever, daemon=True, name="library-scan").start()
    return create_app(library, gate_key(token) if token else b"")
