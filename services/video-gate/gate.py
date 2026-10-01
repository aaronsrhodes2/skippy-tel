"""video-gate: Nagatha's Video Service, the Captain's Jellyfin library for the browser, through the one door skippy-pc has open.

Tailscale Funnel carries https://skippy-pc.<tailnet>.ts.net:8443 to this container (published on 127.0.0.1:3006 only), so
the Video app on the portal plays the library on any device without the device joining the tailnet. The portal never
proxies video; it renders the page and signs short-lived passes, and this gate holds the Jellyfin API key (ADMIN level:
it never leaves this process) and shows the internet a tight whitelist of what that key can do.

Nothing here works without a pass: every path but /health starts with /t/<exp>.<sig>/, a signature only the portal can
make. The key is derived from the home token's sha256, like the music gate's, but with its own label (and its own signed
text), so a music pass can never open this gate and a video pass can never open the music gate.

    GET  /health                                    {"ok", "ready", "jellyfin", "items"}   (no pass: the reporter's probe)
    GET  /t/<pass>/catalog.json                     movies/series/episodes + collections, gzipped, ETag (catalog.py)
    GET  /t/<pass>/img/<id>/<poster|backdrop|logo|thumb>?w=160|320|640|1280   scaled by Jellyfin, cached on disk (images.py)
    GET  /t/<pass>/v/<id>/direct                    the original file, Range, streamed; 409 {"use":"hls"} if browsers can't play it
    GET  /t/<pass>/v/<id>/hls/master.m3u8?audio=<i>&h=<px>&start=<sec>   transcoded HLS; playlists are REWRITTEN (hls.py)
    GET  /t/<pass>/v/<id>/hls/<file>?s=<session>    the variant playlist and segments that master advertised, nothing else
    GET  /t/<pass>/sub/<id>/<index>.vtt             one text subtitle as WebVTT
    POST /t/<pass>/progress                         {"id","pos","played"?,"state":"start|progress|stop"} → Jellyfin's watch state

Every <id> is 32 hex and must be in the catalog; every query value is validated, never forwarded; Jellyfin is reached only
through jellyfin.py, which builds each upstream URL from a template.
"""
from __future__ import annotations

import collections
import hashlib
import hmac
import json
import logging
import math
import os
import re
import threading
import time

from flask import Flask, Response, abort, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

import hls
from catalog import TICKS, Library, progress_outcome
from images import DEFAULT_WIDTH, ImageCache, nearest_width
from jellyfin import ID_RE, IMAGE_KINDS, Client, JellyfinError, shape_of

log = logging.getLogger("video-gate")

MAX_PASS_S = 2 * 24 * 3600            # a pass claiming to last longer than this is refused, whoever signed it
TOKEN_RE = re.compile(r"([0-9]{9,11})\.([0-9a-f]{32})")      # ASCII digits only: \d would accept other scripts' digits
RANGE_RE = re.compile(r"bytes=([0-9]{1,19})?-([0-9]{1,19})?")
CONTENT_RANGE_RE = re.compile(r"bytes (?:[0-9]{1,19}-[0-9]{1,19}|\*)/[0-9]{1,19}")
DATE_RE = re.compile(r"[A-Za-z0-9, :-]{10,40}")
SUB_RE = re.compile(r"([0-9]{1,3})\.vtt")
MESSAGES = {400: "bad request", 403: "forbidden", 404: "not found", 405: "method not allowed", 409: "conflict",
            413: "too large", 416: "range not satisfiable", 429: "too many requests", 500: "something went wrong",
            502: "Jellyfin sent something unusable", 503: "Jellyfin is not answering"}


def gate_key(home_token: str) -> bytes:
    """The signing key, from the home token's sha256 (hex): the portal only has the hash, so both sides start there.
    The label differs from the music gate's, so the two gates' passes are not interchangeable."""
    hashed = hashlib.sha256(home_token.encode()).hexdigest()
    return hmac.new(hashed.encode(), b"video-gate-v1", hashlib.sha256).digest()


def sign(key: bytes, exp: int) -> str:
    return f"{exp}.{hmac.new(key, f'video:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]}"


def valid(key: bytes, token: str, now: float) -> bool:
    m = TOKEN_RE.fullmatch(token or "")
    if not m or not key:
        return False
    exp = int(m.group(1))
    if not now <= exp <= now + MAX_PASS_S:
        return False
    return hmac.compare_digest(sign(key, exp).encode(), token.encode())


class Throttle:
    """At most one `True` per key per `gap` seconds; remembers a bounded number of keys."""

    def __init__(self, gap: float, clock, keep: int = 4096):
        self.gap, self.clock, self.keep = gap, clock, keep
        self._seen: collections.OrderedDict[str, float] = collections.OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            last = self._seen.get(key)
            if last is not None and now - last < self.gap:
                return False
            self._seen[key] = now
            self._seen.move_to_end(key)
            while len(self._seen) > self.keep:
                self._seen.popitem(last=False)
            return True


class SubCache:
    """Small subtitle files, kept ten minutes: the player asks again whenever the viewer switches tracks."""

    def __init__(self, clock, ttl: float = 600.0, max_bytes: int = 8 * 1024 * 1024):
        self.clock, self.ttl, self.max_bytes = clock, ttl, max_bytes
        self._d: collections.OrderedDict[tuple, tuple[float, bytes]] = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._d.get(key)
            if hit and self.clock() - hit[0] <= self.ttl:
                return hit[1]
            self._d.pop(key, None)
            return None

    def put(self, key, data: bytes):
        with self._lock:
            self._d[key] = (self.clock(), data)
            while sum(len(v[1]) for v in self._d.values()) > self.max_bytes and self._d:
                self._d.popitem(last=False)


def create_app(library: Library, client: Client, key: bytes, clock=time.time, sessions: hls.Sessions | None = None,
               images: ImageCache | None = None, max_streams: int = 32, forbidden=()) -> Flask:
    """`forbidden`: secrets (the Jellyfin key) that must never appear in a playlist we hand out, checked as a last resort."""
    app = Flask(__name__, static_folder=None)         # no /static route: the only paths are the ones listed above
    app.url_map.merge_slashes = False                # '//' is a 404, never a redirect: nothing here can bounce a client elsewhere
    app.config["MAX_CONTENT_LENGTH"] = 1024           # the one POST (progress) is a few dozen bytes
    sessions = sessions or hls.Sessions(client.stop_encoding, clock=clock)
    images = images or ImageCache("/state/img")
    slots = threading.BoundedSemaphore(max_streams)   # streams hold a thread each: keep some for the catalog and progress
    progress_gate, stop_gate = Throttle(5.0, clock), Throttle(1.0, clock)
    subs = SubCache(clock)
    app.extensions["video"] = {"library": library, "sessions": sessions, "client": client}

    # ---- plumbing ---------------------------------------------------------------------------------------------
    def err(code: int, **extra) -> Response:
        return Response(json.dumps({"error": MESSAGES.get(code, "request failed"), **extra}), status=code,
                        mimetype="application/json", headers={"Cache-Control": "no-store"})

    def from_jellyfin(e: JellyfinError) -> Response:
        if e.kind == "missing":
            return err(404)
        return err(503 if e.kind == "down" else 502)

    @app.before_request
    def gatekeeper():
        path = request.path
        if request.method == "OPTIONS" and (path == "/health" or path.startswith("/t/")):
            return Response(status=204)                # a CORS preflight carries no data, so it needs no pass
        if path == "/health":
            return None
        m = re.match(r"/t/([^/]*)(?:/|$)", path)
        if not m:
            abort(404)
        if not valid(key, m.group(1), clock()):       # before any route logic: an unknown path under /t/ is 403 too
            abort(403)
        return None

    @app.after_request
    def headers(resp: Response):
        # The player lives on the portal's origin; a pass is the credential, never a cookie, so any origin may read.
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, ETag"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.method == "OPTIONS":
            resp.headers["Access-Control-Allow-Methods"] = "GET, HEAD, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Range, If-None-Match"
            resp.headers["Access-Control-Max-Age"] = "86400"
        return resp

    @app.errorhandler(HTTPException)
    def http_error(e: HTTPException):
        return err(e.code or 500)          # fixed words only: nothing of the request is echoed back

    @app.errorhandler(Exception)
    def crash(e: Exception):
        log.error("video-gate: %s in a handler", type(e).__name__)      # the class only: no URL, pass or key can ride along
        return err(500)

    def playable(item_id: str) -> dict:
        item = library.get(item_id) if ID_RE.fullmatch(item_id) else None
        if not item or not item.get("play"):
            abort(404)
        return item

    def relay(up, status: int, headers: dict, head: bool = False) -> Response:
        """Stream an upstream response to the client without holding it in memory, and let go of Jellyfin and the slot
        the moment the client is done or gone."""
        guard = threading.Lock()

        def cleanup():
            if guard.acquire(blocking=False):          # once, whichever of the paths below gets here first
                up.close()
                slots.release()

        if head:
            cleanup()
            return Response(status=status, headers=headers)

        def body():
            try:
                for chunk in up.raw.stream(65536, decode_content=False):
                    if chunk:
                        yield chunk
            except Exception as e:  # noqa: BLE001 — Jellyfin dropping mid-file just ends the stream
                log.warning("video-gate: upstream stream ended early (%s)", type(e).__name__)
            finally:
                cleanup()

        resp = Response(body(), status=status, headers=headers)
        resp.call_on_close(cleanup)                     # also when the client leaves before the first chunk
        return resp

    def stream_headers(up, content_type: str, ranged: bool) -> dict:
        h = {"Content-Type": content_type, "Cache-Control": "private, max-age=3600"}
        length = up.headers.get("Content-Length", "")
        if re.fullmatch(r"[0-9]{1,19}", length):
            h["Content-Length"] = length
        if ranged:
            h["Accept-Ranges"] = "bytes"
            cr = up.headers.get("Content-Range", "")
            if CONTENT_RANGE_RE.fullmatch(cr):
                h["Content-Range"] = cr
            lm = up.headers.get("Last-Modified", "")
            if DATE_RE.fullmatch(lm):
                h["Last-Modified"] = lm
        return h

    # ---- routes -----------------------------------------------------------------------------------------------
    @app.get("/health")
    def health():
        try:
            up = library.online()
        except Exception:  # noqa: BLE001 — a probe must always answer
            up = False
        return jsonify(ok=True, ready=bool(library.ready and up), jellyfin=bool(up), items=library.count)

    @app.get("/t/<token>/catalog.json")
    def catalog(token):
        library.poke()                       # at most one background refresh a minute, whoever asks
        gz, etag = library.snapshot()
        if not gz:
            return err(503, retry=True)
        if request.headers.get("If-None-Match", "").removeprefix("W/") == etag:
            return Response(status=304, headers={"ETag": etag})
        return Response(gz, mimetype="application/json",
                        headers={"Content-Encoding": "gzip", "ETag": etag, "Cache-Control": "private, no-cache",
                                 "Vary": "Accept-Encoding"})

    @app.get("/t/<token>/img/<id>/<kind>")
    def image(token, id, kind):
        if not ID_RE.fullmatch(id) or kind not in IMAGE_KINDS:
            abort(404)
        tag = library.tags(id).get(kind)             # unknown id, or an item without that art: not ours to ask Jellyfin about
        if not tag:
            abort(404)
        w = request.args.get("w", "")
        if w == "":
            width = DEFAULT_WIDTH
        elif re.fullmatch(r"[0-9]{1,6}", w):
            width = nearest_width(int(w))
        else:
            abort(400)
        found = images.get(id, kind, width, tag)
        if not found:
            try:
                data, mime = client.image(id, IMAGE_KINDS[kind], width)
            except JellyfinError as e:
                return from_jellyfin(e)
            found = images.put(id, kind, width, tag, data, mime)
            if not found:                            # the disk is unusable: serve it once, uncached
                return Response(data, mimetype=mime, headers={"Cache-Control": "private, max-age=604800"})
        path, mime = found
        resp = send_file(path, mimetype=mime, conditional=True, max_age=604800)
        resp.headers["Cache-Control"] = "private, max-age=604800"
        return resp

    @app.route("/t/<token>/v/<id>/direct", methods=["GET", "HEAD"])
    def direct(token, id):
        item = playable(id)
        if not item["play"]["direct"]:
            return err(409, use="hls")
        rng = request.headers.get("Range", "")
        m = RANGE_RE.fullmatch(rng)
        forward = rng if m and (m.group(1) or m.group(2)) else None     # one plain byte range, or none
        if not slots.acquire(blocking=False):
            return err(429)
        try:
            up = client.stream(id, forward, head=request.method == "HEAD")
        except JellyfinError as e:
            slots.release()
            return from_jellyfin(e)
        except BaseException:
            slots.release()
            raise
        # Whatever the container, only MP4-family files are `direct`, and browsers want video/mp4 for all of them.
        return relay(up, up.status_code, stream_headers(up, "video/mp4", ranged=True), head=request.method == "HEAD")

    @app.get("/t/<token>/v/<id>/hls/<path:rest>")
    def hls_file(token, id, rest):
        item = playable(id)
        if rest == "master.m3u8":
            return hls_master(token, item)
        sid = request.args.get("s", "")
        if shape_of(rest) is None or not hls.valid_sid(sid):
            abort(404)
        try:
            sess = sessions.touch(sid, id)
        except hls.Busy:
            return err(429, retry=True)
        entry = sess.table.get(rest) if sess else None
        if not entry:
            abort(404)                               # only files an earlier playlist of this session advertised
        upstream = sess.upstream_params(entry[1])
        if entry[0] == "playlist":
            try:
                text = client.hls_text(id, rest, upstream)
                out = hls.rewrite_media(text, sess, forbidden)
            except JellyfinError as e:
                return from_jellyfin(e)
            except hls.PlaylistError:
                return err(502)
            return Response(out, mimetype=hls.CONTENT_TYPES["m3u8"], headers={"Cache-Control": "no-store"})
        if not slots.acquire(blocking=False):
            return err(429)
        try:
            up = client.hls_stream(id, rest, upstream)
        except JellyfinError as e:
            slots.release()
            return from_jellyfin(e)
        except BaseException:
            slots.release()
            raise
        ctype = hls.CONTENT_TYPES[rest.rsplit(".", 1)[1]]
        return relay(up, 200, stream_headers(up, ctype, ranged=False))

    def hls_master(token: str, item: dict) -> Response:
        play = item["play"]
        audio = None
        if "audio" in request.args:
            raw = request.args["audio"]
            if not re.fullmatch(r"[0-9]{1,3}", raw) or int(raw) not in {a["i"] for a in play["audio"]}:
                return err(400)
            audio = int(raw)
        height = hls.parse_height(request.args.get("h"))
        start = hls.parse_start(request.args.get("start"), item["runtime"])
        if height is None or start is None:
            return err(400)
        try:
            sess = sessions.create(token, item["id"], audio, height, start)
        except hls.Busy:
            return err(429, retry=True)
        try:
            out = hls.rewrite_master(client.hls_text(item["id"], "master.m3u8", sess.params), sess, forbidden)
        except JellyfinError as e:
            sessions.discard(sess)
            return from_jellyfin(e)
        except hls.PlaylistError:
            sessions.discard(sess)
            return err(502)
        return Response(out, mimetype=hls.CONTENT_TYPES["m3u8"], headers={"Cache-Control": "no-store"})

    @app.get("/t/<token>/sub/<id>/<name>")
    def subtitle(token, id, name):
        item = playable(id)
        m = SUB_RE.fullmatch(name)
        index = int(m.group(1)) if m else -1
        if not any(s["i"] == index and s["vtt"] for s in item["play"]["subs"]):
            abort(404)                               # also: Jellyfin answers 500 for an index it hasn't got
        data = subs.get((id, index))
        if data is None:
            try:
                data = client.subtitle(id, index)
            except JellyfinError as e:
                return from_jellyfin(e)
            if not data.lstrip(b"\xef\xbb\xbf").startswith(b"WEBVTT"):
                return err(502)
            subs.put((id, index), data)
        return Response(data, content_type="text/vtt; charset=utf-8", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/t/<token>/progress")
    def progress(token):
        def reject(_):
            raise ValueError("no NaN or Infinity")
        try:
            body = json.loads(request.get_data(cache=False), parse_constant=reject)
        except (ValueError, UnicodeDecodeError):
            return err(400)
        if not isinstance(body, dict) or not set(body) <= {"id", "pos", "played", "state"}:
            return err(400)
        state, played_flag, pos = body.get("state"), body.get("played"), body.get("pos")
        if state not in ("start", "progress", "stop") or not isinstance(body.get("id"), str):
            return err(400)
        if played_flag is not None and not isinstance(played_flag, bool):
            return err(400)
        if pos is None:
            if state != "start":
                return err(400)
        elif isinstance(pos, bool) or not isinstance(pos, (int, float)) or not math.isfinite(pos) or pos < 0:
            return err(400)
        item = playable(body["id"])
        runtime = item["runtime"]
        if pos is not None and pos > (runtime * 1.05 + 60 if runtime else 172800):
            return err(400)
        if state == "stop":
            sessions.stop_for(token, item["id"])     # the film is over for this viewer: free the transcoder at once
        if state == "start" or pos is None:
            return jsonify(ok=True, relayed=False)
        if not (stop_gate if state == "stop" else progress_gate).allow(f"{item['id']}"):
            return jsonify(ok=True, throttled=True)
        resume, played = progress_outcome(pos, runtime, state, played_flag)
        fields = {"PlaybackPositionTicks": resume * TICKS,
                  "LastPlayedDate": time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime(clock()))}
        if played is not None:
            fields["Played"] = played
            if played and not item["played"]:
                fields["PlayCount"] = 1
        try:
            client.set_user_data(library.ensure_user(), item["id"], fields)
        except JellyfinError as e:
            return from_jellyfin(e)
        library.apply_progress(item["id"], resume, played)
        return jsonify(ok=True, resume=resume, played=item["played"] if played is None else played)

    return app


def from_env() -> Flask:
    """gunicorn's entry point: Jellyfin and its key from the environment (the vault .env), the state in /state."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    token = os.environ.get("SKIPPY_HOME_TOKEN", "").strip()
    api_key = os.environ.get("JELLYFIN_API_KEY", "").strip()
    if not token:
        logging.warning("video-gate: SKIPPY_HOME_TOKEN isn't set; every pass will be refused")
    try:
        client = Client(os.environ.get("JELLYFIN_URL", "http://host.docker.internal:8096"), api_key)
    except ValueError as e:
        raise RuntimeError(f"video-gate cannot start: {e}") from None     # the message never carries the key
    library = Library(client, os.environ.get("JELLYFIN_USER", ""),
                      refresh_s=float(os.environ.get("CATALOG_REFRESH_S", "300")))
    sessions = hls.Sessions(client.stop_encoding, cap=int(os.environ.get("HLS_MAX_TRANSCODES", "2")),
                            idle_s=float(os.environ.get("HLS_IDLE_S", "120")))
    images = ImageCache(os.path.join(os.environ.get("STATE_DIR", "/state"), "img"),
                        limit=int(float(os.environ.get("IMG_CACHE_MB", "500")) * 1024 * 1024))
    threading.Thread(target=library.run_forever, daemon=True, name="catalog-refresh").start()
    threading.Thread(target=sessions.run_forever, daemon=True, name="hls-reaper").start()
    return create_app(library, client, gate_key(token) if token else b"", sessions=sessions, images=images,
                      max_streams=int(os.environ.get("MAX_STREAMS", "32")), forbidden=(api_key,))
