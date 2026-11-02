"""audiobook-gate: the Captain's audiobooks to his own browser, and nothing else of skippy-pc on the internet.

Tailscale Funnel carries https://skippy-pc.<tailnet>.ts.net:10000/books/ to this container (published on 127.0.0.1:3009 only; the rest of
:10000 is the voice gate and the popup-gate's /in), so the Audiobooks app on the portal (portal/audiobooks.py) plays his library in a
browser and the files never pass through the portal. Personal-use conversions of books he bought: the pass is the Captain's alone.

Nothing works without a pass: every path but /health starts with /t/<exp>.<sig>/, a signature only the portal can make. The key comes from
the home token's sha256 under its own label ("audiobook-gate-v1"), so a music, video or voice pass never opens this gate.

    GET /health                       the readiness contract (ready.py), with how many books there are            (no pass: the reporter's probe)
    GET /t/<pass>/catalog.json        every book: title, author, narrator, series (sr) and its number (sq), release date, summary, chapters, length; gzipped, with an ETag
    GET /t/<pass>/places.json         {"current", "books": {id: {pos, chapter, speed, last_played, finished}}}: the hub's places and the ones saved here, newest wins
    PUT /t/<pass>/place/<id>          {"pos", "chapter", "speed", "finished"}: a place saved from the browser, for the hub (the voice player) to pick up
    GET /t/<pass>/a/<id>              the book's file, with Range (seeking; Safari insists on it)
    GET /t/<pass>/art/<id>?w=         its cover; w is 120, 240 or 500 (anything else snaps to the nearest)

The library: OpenAudible's data (books.json, art/) beside the books/ folder it converted into. A book's id is the hub's own (services/satellite/
audiobooks.py slug of the file name), so a place means the same thing to the page and to "Skippy, read my book". The hub reads the places saved here
from the inbox folder; this gate only ever writes there. It never opens an .aax or OpenAudible's credentials.
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file
from flask import g as _g

import ready as ready_mod

MAX_PASS_S = 2 * 24 * 3600     # a pass claiming to last longer than this is refused, whoever signed it
AUDIO = {".m4b": "audio/mp4", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac"}
THUMB_WIDTHS = (120, 240, 500)
RESCAN_S = 300
MAX_BODY = 4096


def gate_key(home_token: str) -> bytes:
    """Must match portal/audiobooks.py gate_key(): its own label, so no other gate's pass opens this one."""
    hashed = hashlib.sha256(home_token.encode()).hexdigest()
    return hmac.new(hashed.encode(), b"audiobook-gate-v1", hashlib.sha256).digest()


def sign(key: bytes, exp: int) -> str:
    return f"{exp}.{hmac.new(key, f'audiobooks:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]}"


def valid(key: bytes, token: str, now: float) -> bool:
    m = re.fullmatch(r"(\d{9,11})\.([0-9a-f]{32})", token or "")
    if not m or not key:
        return False
    exp = int(m.group(1))
    if not now <= exp <= now + MAX_PASS_S:
        return False
    return hmac.compare_digest(sign(key, exp), token)


def slug(s: str) -> str:
    """The hub's book id (services/satellite/audiobooks.py slug): kept identical so both mean the same book."""
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "book"


def plain(html: str) -> str:
    text = re.sub(r"<\s*(br|/p)\s*/?>", "\n", html or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(a, b)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def clean(text: str) -> str:
    """OpenAudible's data lost its apostrophes (they arrive as U+FFFD): put them back, never show the replacement mark."""
    return re.sub(r"(?<=\w)�(?=\w)", "'", text or "").replace("�", "'")


def num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class Library:
    """books/ + OpenAudible's books.json -> the catalog. Read-only."""

    def __init__(self, root: Path, clock=time.time):
        self.root, self.clock = Path(root), clock
        self.books: dict[str, dict] = {}
        self.catalog_gz: bytes = b""
        self.etag = ""
        self.scanned_at = 0.0
        self._sig = None
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        return len(self.books)

    @property
    def ready(self) -> bool:
        return bool(self.books)

    def _meta(self) -> dict[str, dict]:
        try:
            raw = json.loads((self.root / "books.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(b.get("filename") or ""): b for b in raw if isinstance(b, dict) and b.get("filename")}

    def refresh(self, force: bool = False) -> bool:
        """Rescan when the folder or books.json changed (a listing and one stat: cheap enough to ask on every request, at most every
        RESCAN_S). True if the catalog changed."""
        if not force and self.books and self.clock() - self.scanned_at < RESCAN_S:
            return False
        folder = self.root / "books"
        try:
            names = sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO)
            sig = (tuple(names), (self.root / "books.json").stat().st_mtime_ns)
        except OSError:
            return False
        with self._lock:
            self.scanned_at = self.clock()
            if sig == self._sig and not force:
                return False
            meta = self._meta()
            books = {}
            for name in names:
                stem = Path(name).stem
                m = meta.get(stem) or {}
                bid = slug(name)
                chapters = []
                for c in m.get("chapters") or []:
                    if isinstance(c, dict):
                        chapters.append([str(c.get("title") or "").strip() or f"Chapter {len(chapters) + 1}", round(num(c.get("start_offset_ms")) / 1000, 1)])
                art = self.root / "art" / (stem + ".jpg")
                books[bid] = {
                    "id": bid, "t": clean(str(m.get("title") or stem)), "a": clean(str(m.get("author") or "")), "n": clean(str(m.get("narrated_by") or "")),
                    "s": plain(str(m.get("summary") or m.get("description") or ""))[:1500], "d": round(num(m.get("seconds")), 1),
                    "g": str(m.get("genre") or "").split(":")[0], "y": str(m.get("release_date") or "")[:4], "ch": chapters,
                    "sr": clean(str(m.get("series_name") or "")).strip(), "sq": num(m.get("series_sequence"), None), "rd": str(m.get("release_date") or "")[:10],
                    "c": art.is_file(), "oa": str(m.get("finished") or "").lower() == "true", "_file": folder / name, "_art": art}
            self.books, self._sig = books, sig
            body = json.dumps({"v": 1, "books": [{k: v for k, v in b.items() if not k.startswith("_")} for b in books.values()]},
                              separators=(",", ":")).encode()
            self.catalog_gz = gzip.compress(body, 6)
            self.etag = '"' + hashlib.sha1(body).hexdigest()[:20] + '"'
            return True


class Places:
    """The hub's state/audiobooks.json (read-only here) merged with the inbox: one small file per book saved from the browser. The newer
    last_played wins, so a book played by voice after it was last heard here keeps the voice's place, and the other way round."""

    FIELDS = ("pos", "chapter", "speed", "last_played", "finished")
    # where a browser's place was saved ("web", or "car": the portal's Drive page) and whether it was playing there, so the house can pause its copy
    MARKS = ("src", "playing")

    def __init__(self, hub_file: Path, inbox: Path, clock=time.time):
        self.hub_file, self.inbox, self.clock = Path(hub_file), Path(inbox), clock
        self._lock = threading.Lock()

    @staticmethod
    def _read(path: Path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def merged(self) -> dict:
        hub = self._read(self.hub_file) or {}
        books = {k: dict(v) for k, v in (hub.get("books") or {}).items() if isinstance(v, dict)}
        current = hub.get("current")
        newest = num((books.get(current) or {}).get("last_played"))
        for p in self.inbox.glob("*.json") if self.inbox.is_dir() else ():
            d = self._read(p)
            if not d or not isinstance(d.get("id"), str):
                continue
            seen = num(d.get("last_played"))
            if seen > num((books.get(d["id"]) or {}).get("last_played")):
                books[d["id"]] = {k: d.get(k) for k in self.FIELDS + self.MARKS}
            if seen > newest:
                current, newest = d["id"], seen
        return {"current": current, "books": books}

    def put(self, book: dict, body: dict) -> dict:
        dur = book["d"] or 10 * 24 * 3600
        pos = min(max(0.0, num(body.get("pos"))), dur)
        chapter = body.get("chapter")
        if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 0:
            chapter = 0
        place = {"id": book["id"], "pos": round(pos, 1), "chapter": min(chapter, 9999), "speed": round(min(3.0, max(0.5, num(body.get("speed"), 1.0))), 2),
                 "last_played": int(self.clock()), "finished": body.get("finished") is True,
                 "src": "car" if body.get("src") == "car" else "web", "playing": body.get("playing") is True}
        with self._lock:
            self.inbox.mkdir(parents=True, exist_ok=True)
            name = hashlib.sha1(book["id"].encode()).hexdigest()[:24] + ".json"
            tmp = self.inbox / (name + ".tmp")
            tmp.write_text(json.dumps(place), encoding="utf-8")
            os.replace(tmp, self.inbox / name)
        return place


class Thumbs:
    """Covers cut down for the grid, kept in memory (a few KB each; about 400 books)."""

    def __init__(self):
        self._cache: dict[tuple, bytes] = {}
        self._lock = threading.Lock()

    def get(self, path: Path, width: int) -> bytes | None:
        try:
            key = (str(path), width, path.stat().st_mtime_ns)
        except OSError:
            return None
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        from PIL import Image                                   # noqa: PLC0415
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((width, width))
                out = io.BytesIO()
                im.save(out, "JPEG", quality=82)
        except Exception:                                       # noqa: BLE001 — a broken cover is no cover
            return None
        with self._lock:
            if len(self._cache) > 1200:
                self._cache.clear()
            self._cache[key] = out.getvalue()
        return self._cache[key]


def pick_width(raw) -> int:
    try:
        w = int(raw)
    except (TypeError, ValueError):
        abort(400)
    return min(THUMB_WIDTHS, key=lambda t: abs(t - w))


def create_app(library: Library, places: Places, key: bytes, clock=time.time, thumbs: Thumbs | None = None) -> Flask:
    app = Flask(__name__)
    thumbs = thumbs or Thumbs()
    app.config.update(LIBRARY=library, PLACES=places)

    @app.after_request
    def headers(resp: Response):
        # The app lives on the portal's origin; a pass is the credential, never a cookie, so any origin may read.
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, ETag"
        resp.headers["Access-Control-Allow-Methods"] = "GET, HEAD, PUT, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "If-None-Match, Range, If-Range, Content-Type"
        resp.headers["Access-Control-Max-Age"] = "86400"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    def check(token: str) -> None:
        if not valid(key, token, clock()):
            abort(403)

    def book_of(bid: str) -> dict:
        library.refresh()
        b = library.books.get(bid)
        if not b:
            abort(404)
        return b

    ready_state = ready_mod.Readiness("audiobook-gate")

    @app.before_request
    def _job_begins():
        if request.path != "/health" and request.method != "OPTIONS":      # a call that does work is a job while it runs (PLATINUM section 24)
            _g._job = ready_state.begin("request")

    @app.teardown_request
    def _job_ends(_exc):
        token = _g.pop("_job", None)
        if token:
            ready_state.end(token)

    @app.get("/health")
    def health():
        library.refresh()
        rep = ready_state.report(ready=library.ready, detail="" if library.ready else "no audiobooks found in the library folder", books=library.count)
        return Response(json.dumps(rep), status=ready_mod.status_code(rep), mimetype="application/json")

    @app.get("/t/<token>/catalog.json")
    def catalog(token):
        check(token)
        library.refresh()
        if not library.catalog_gz:
            return jsonify(error="the library is still being read; try again in a minute"), 503
        if library.etag in (request.headers.get("If-None-Match") or ""):
            return Response(status=304, headers={"ETag": library.etag, "Cache-Control": "private, no-cache"})
        return Response(library.catalog_gz, mimetype="application/json",
                        headers={"Content-Encoding": "gzip", "ETag": library.etag, "Cache-Control": "private, no-cache", "Vary": "Accept-Encoding"})

    @app.get("/t/<token>/places.json")
    def get_places(token):
        check(token)
        return Response(json.dumps(places.merged()), mimetype="application/json", headers={"Cache-Control": "no-store"})

    @app.put("/t/<token>/place/<bid>")
    def put_place(token, bid):
        check(token)
        if (request.content_length or 0) > MAX_BODY:
            abort(413)
        body = request.get_json(silent=True, force=True)
        if not isinstance(body, dict):
            abort(400)
        return Response(json.dumps(places.put(book_of(bid), body)), mimetype="application/json", headers={"Cache-Control": "no-store"})

    @app.get("/t/<token>/a/<bid>")
    def audio(token, bid):
        check(token)
        path = book_of(bid)["_file"]
        if not path.is_file():
            abort(404)
        resp = send_file(path, mimetype=AUDIO.get(path.suffix.lower(), "application/octet-stream"), conditional=True)
        resp.headers["Cache-Control"] = "private, max-age=3600"
        return resp

    @app.get("/t/<token>/art/<bid>")
    def art(token, bid):
        check(token)
        b = book_of(bid)
        if not b["c"]:
            abort(404)
        w = pick_width(request.args.get("w", "240"))
        data = thumbs.get(b["_art"], w) if w < 500 else None
        resp = Response(data, mimetype="image/jpeg") if data is not None else send_file(b["_art"], mimetype="image/jpeg", conditional=True)
        resp.headers["Cache-Control"] = "private, max-age=604800"
        return resp

    inner = app.wsgi_app

    def strip_mount(environ, start_response):          # Funnel's /books mount, should it pass the prefix on: routed as if it hadn't
        if environ.get("PATH_INFO", "").startswith("/books/"):
            environ["PATH_INFO"] = environ["PATH_INFO"][6:]
        return inner(environ, start_response)
    app.wsgi_app = strip_mount
    return app


def from_env() -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    token = os.environ.get("SKIPPY_HOME_TOKEN", "").strip()
    if not token:
        logging.warning("audiobook-gate: SKIPPY_HOME_TOKEN isn't set; every pass will be refused")
    library = Library(Path(os.environ.get("AUDIOBOOKS_DIR", "/audiobooks")))
    library.refresh(force=True)
    places = Places(Path(os.environ.get("HUB_PLACES", "/state/audiobooks.json")), Path(os.environ.get("PLACES_INBOX", "/inbox")))
    return create_app(library, places, gate_key(token) if token else b"")
