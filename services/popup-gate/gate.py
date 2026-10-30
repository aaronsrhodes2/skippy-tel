"""popup-gate: the road home for a file a pop-up should work on (docs/popups.md), too big to ride in a chat message.

A chat attachment rides inside the message and the job, so it is held to about 1 MB; a whole track for stem separation is 5 to 60 MB.
That file comes here instead, straight from the browser, never through the portal: Tailscale Funnel carries
https://skippy-pc.<tailnet>.ts.net:10000/in/ to this container (published on 127.0.0.1:3008 only; the rest of :10000 is the voice gate).
The portal signs a pass for ONE file: its id, the most bytes it may have, and whose it is. This gate shows the internet two things:

    GET  /health               the readiness contract (ready.py), with how many files it holds          (no pass: the reporter's probe)
    PUT  /t/<pass>/file        body = the file (a picture or a sound, by its own first bytes), at most the pass's bytes and MAX_BYTES
                               -> {"id", "type", "bytes"}; one file per pass

It keeps <dir>/<id>.<ext> and <id>.json ({owner, type, name, bytes, at}) for KEEP_S, then deletes them. The home reporter reads them from
the same folder (mounted read-only) when it runs the pop-up job whose input it is, and checks the owner there.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import pathlib
import re
import threading
import time

from flask import Flask, Response, abort, request
from werkzeug.exceptions import HTTPException

import ready as ready_mod

MAX_BYTES = 64 * 1024 * 1024        # one file (the Captain, 2026-10-29: a whole track; portal/popups.py MAX_INPUT_MB)
KEEP_S = 24 * 3600                  # a file waits this long for its job (a patient job may wait up to a day)
MAX_PASS_S = 2 * 3600
PASS_RE = re.compile(r"([0-9]{9,11})\.([0-9a-f]{32})\.([0-9]{1,9})\.([0-9a-f]{16})\.([0-9a-f]{32})")
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
       "audio/wav": "wav", "audio/flac": "flac", "audio/ogg": "ogg", "audio/mpeg": "mp3"}
MESSAGES = {400: "bad request", 403: "forbidden", 404: "not found", 405: "method not allowed", 409: "that pass was already used",
            413: "too large", 415: "not a picture or a sound", 500: "something went wrong"}


def gate_key(home_token: str) -> bytes:
    """Must match portal/popup_in.py gate_key(): its own label, so no other gate's pass opens this one."""
    hashed = hashlib.sha256(home_token.encode()).hexdigest()
    return hmac.new(hashed.encode(), b"popup-gate-v1", hashlib.sha256).digest()


def sign(key: bytes, exp: int, file_id: str, max_bytes: int, owner: str) -> str:
    """Must match portal/popup_in.py sign()."""
    text = f"popup-in:{exp}:{file_id}:{max_bytes}:{owner}"
    return f"{exp}.{file_id}.{max_bytes}.{owner}.{hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:32]}"


def valid(key: bytes, token: str, now: float) -> dict | None:
    """{"id", "max", "owner"} for a good pass, else None."""
    m = PASS_RE.fullmatch(token or "")
    if not m or not key:
        return None
    exp, file_id, max_bytes, owner = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
    if not now <= exp <= now + MAX_PASS_S:
        return None
    if not hmac.compare_digest(sign(key, exp, file_id, max_bytes, owner).encode(), token.encode()):
        return None
    return {"id": file_id, "max": min(max_bytes, MAX_BYTES), "owner": owner}


def sniff(head: bytes) -> str | None:
    """A picture's or a sound's type by its own first bytes (never by what the browser says), else None."""
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[:4] == b"fLaC":
        return "audio/flac"
    if head[:4] == b"OggS":
        return "audio/ogg"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    return None


class Store:
    def __init__(self, directory: str, clock):
        self.dir, self.clock = pathlib.Path(directory), clock
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def sweep(self) -> None:
        cutoff = self.clock() - KEEP_S
        for path in self.dir.iterdir():
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass

    def count(self) -> int:
        return sum(1 for p in self.dir.glob("*.json"))

    def taken(self, file_id: str) -> bool:
        return (self.dir / f"{file_id}.json").exists() or any(self.dir.glob(f"{file_id}.part"))

    def take(self, grant: dict, stream, length: int | None, name: str) -> dict:
        """Write the body to <id>.part, checking size and type as it comes, then put it in place with its .json. abort()s on a bad one."""
        file_id = grant["id"]
        with self._lock:
            if self.taken(file_id):
                abort(409)
            part = self.dir / f"{file_id}.part"
            part.touch()
        if length is not None and length > grant["max"]:
            part.unlink(missing_ok=True)
            abort(413)
        total, head, ctype = 0, b"", None
        try:
            with open(part, "wb") as out:
                while True:
                    chunk = stream.read(1 << 20)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > grant["max"]:
                        abort(413)
                    if ctype is None:
                        head = (head + chunk)[:16]
                        if len(head) >= 12:
                            ctype = sniff(head) or abort(415)
                    out.write(chunk)
            ctype = ctype or sniff(head)
            if not total or not ctype:
                abort(415 if total else 400)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        part.replace(self.dir / f"{file_id}.{EXT[ctype]}")
        meta = {"id": file_id, "owner": grant["owner"], "type": ctype, "name": re.sub(r"[^\w .()+-]", "_", name)[:80] or "file",
                "bytes": total, "at": int(self.clock())}
        (self.dir / f"{file_id}.json").write_text(json.dumps(meta))
        return {"id": file_id, "type": ctype, "bytes": total}


def create_app(key: bytes, directory: str, clock=time.time) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.url_map.merge_slashes = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES + 4096
    store = Store(directory, clock)
    ready_state = ready_mod.Readiness("popup-gate")

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
        grant = valid(key, m.group(1), clock())
        if not grant:
            abort(403)
        request.environ["popup_gate.grant"] = grant
        return None

    @app.after_request
    def headers(resp: Response):
        resp.headers["Access-Control-Allow-Origin"] = "*"          # the pass is the credential, never a cookie
        resp.headers["Cache-Control"] = "no-store"
        if request.method == "OPTIONS":
            resp.headers["Access-Control-Allow-Methods"] = "GET, PUT, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-File-Name"
            resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    @app.errorhandler(HTTPException)
    def http_error(e: HTTPException):
        return err(e.code or 500)

    @app.errorhandler(Exception)
    def crash(e: Exception):
        logging.exception("popup-gate: %s", e)
        return err(500)

    @app.get("/health")
    def health():
        store.sweep()
        rep = ready_state.report(ready=True, ok=True, files=store.count(), max_bytes=MAX_BYTES)
        return Response(json.dumps(rep), mimetype="application/json")

    @app.put("/t/<token>/file")
    def put_file(token):
        store.sweep()
        with ready_state.job("upload"):
            got = store.take(request.environ["popup_gate.grant"], request.stream, request.content_length,
                             request.headers.get("X-File-Name", "file"))
        return Response(json.dumps(got), mimetype="application/json")

    inner = app.wsgi_app

    def strip_in(environ, start_response):            # Funnel's /in mount, should it pass the prefix on: routed as if it hadn't
        if environ.get("PATH_INFO", "").startswith("/in/"):
            environ["PATH_INFO"] = environ["PATH_INFO"][3:]
        return inner(environ, start_response)
    app.wsgi_app = strip_in
    return app


def from_env() -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    token = os.environ.get("SKIPPY_HOME_TOKEN", "").strip()
    if not token:
        logging.warning("popup-gate: SKIPPY_HOME_TOKEN isn't set; every pass will be refused")
    return create_app(gate_key(token) if token else b"", os.environ.get("INPUTS_DIR", "/inputs"))
