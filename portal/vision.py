"""Skippy-vision: one flash of everything (the Captain, 2026-11-02).

One key (or the word "snap") saves a MOMENT: the screens and cameras of everything involved, at that instant, kept together:

  the page's own screenshot and the phone's cameras      the page sends them with the flash (small stills)
  the glasses' three cameras                             the OpenViture host hands them to the page (window.OpenViture.flash)
  the pods' screens and skippy-pc's screens              a `flash` job for the home reporter, which comes back one file at a time
  the Tesla's Drive page                                 Drive sees a young moment and adds its own page
  a clip: 7 s before and 3 s after, from the glasses     a hold (a long press, "snap it big time"), sent as one zip a moment later

A moment is a folder of files in the Tabletop bucket, `moments/<YYYYMMDD-HHMMSS>/` (UTC, so ids sort by time), with an `index.json`.
Stills are kept for ever; the clip for 90 days unless the Captain pressed Keep (`sweep`, run nightly from skippy-pc). Everything here
is the Captain's alone (the screens of his house): admin routes for the browser, the home token for skippy-pc's two doors.

The files carry only what the page, the host or skippy-pc made: names are checked against a list, bytes against their magic number.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from functools import wraps

from flask import Blueprint, Response, abort, jsonify, request

import auth
import eyes_client

PREFIX = "moments/"
ID = re.compile(r"\d{8}-\d{6}(?:-\d{1,2})?")
PAGE_NAME = re.compile(r"hud-page\.jpg|drive-page\.jpg|phone-cam-\d\.jpg|glasses-(?:rgb|left|right)\.jpg")   # what a browser or the host may add
CLIP = "glasses-rgb-7s.zip"
HOME_NAME = re.compile(r"pod-[a-z0-9-]{1,30}\.png|pc-screen-\d\.png")                                      # what skippy-pc may add
BY = ("key", "word", "tool", "drive")
MAX_IMAGE = 3 * 1024 * 1024               # one still (a phone camera's full frame is about 1.5 MB)
MAX_STILLS = 12                           # in one flash request
MAX_FLASH_BYTES = 14 * 1024 * 1024        # all of a flash request's stills together (Cloud Run's door is 32 MB)
MAX_PNG = 12 * 1024 * 1024                # a pc screen, a pod's
MAX_CLIP = 30 * 1024 * 1024               # the host thins a clip to 24 MB of JPEG
PENDING_FOR_S = 120                       # a moment still "waiting for the pods and pc" after this long is not waiting any more
KEEP_CLIP_DAYS = 90
LISTED = 30
MAGIC = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"PK\x03\x04", "application/zip"))


READ_PROMPT = ("Look for writing in this picture that is NOT English: signs, labels, menus, screens, documents. Answer with ONLY a JSON array, at most "
               "5 items, each {\"text\": the words as printed, \"lang\": the language's two-letter code, \"en\": an English translation}. If there is "
               "no writing that is not English, answer []. No other words.")
LOOK_PROMPT = ("Someone wearing camera glasses is looking at this. Say in ONE short plain sentence what they are looking at: the main thing, and "
               "any readable name or label on it. Never say who a person is.")
MAX_SEEN = 5


def parse_seen(raw) -> list[dict]:
    """The vision model's JSON reading (fenced or not, with or without words around it) as at most MAX_SEEN {text, lang, en}; anything else is []."""
    text = str(raw or "")
    i, j = text.find("["), text.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        items = json.loads(text[i:j + 1])
    except ValueError:
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        said, en = " ".join(str(item.get("text") or "").split())[:200], " ".join(str(item.get("en") or "").split())[:200]
        lang = re.sub(r"[^a-z-]", "", str(item.get("lang") or "").lower())[:8]
        if said and en and lang != "en" and said.lower() != en.lower():          # English already, or not translated: nothing to show
            out.append({"text": said, "lang": lang, "en": en})
        if len(out) == MAX_SEEN:
            break
    return out


def sniff(data: bytes) -> str | None:
    """The content type of what these bytes are, by magic number (never by what the sender said), or None."""
    return next((ctype for magic, ctype in MAGIC if data[:len(magic)] == magic), None)


def decode_data_url(url) -> bytes:
    """The bytes of a `data:image/...;base64,...` URL; ValueError for anything else."""
    if not isinstance(url, str) or not url.startswith("data:") or ";base64," not in url[:80]:
        raise ValueError("not a base64 data URL")
    try:
        return base64.b64decode(url.split(";base64,", 1)[1], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("bad base64") from exc


class Moments:
    """The moments of one bucket. The portal runs one instance, so a lock and an in-memory copy of each index are enough."""

    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self._lock = threading.RLock()
        self._index: dict[str, dict] = {}        # id -> index.json as last read or written
        self._ids: list[str] | None = None       # every id in the bucket, newest first (read once, kept up to date)

    @property
    def ready(self) -> bool:
        return self.store is not None

    # ── reading ──
    def _all_ids(self) -> list[str]:
        if self._ids is None:
            names = self.store.list(PREFIX)
            self._ids = sorted({n[len(PREFIX):].split("/", 1)[0] for n in names if ID.fullmatch(n[len(PREFIX):].split("/", 1)[0])}, reverse=True)
        return self._ids

    def get(self, mid: str) -> dict | None:
        if not ID.fullmatch(mid or ""):
            return None
        with self._lock:
            if mid not in self._index:
                raw = self.store.get(f"{PREFIX}{mid}/index.json")
                if not raw:
                    return None
                try:
                    self._index[mid] = json.loads(raw)
                except ValueError:
                    return None
            return self._index[mid]

    def list(self, limit: int = LISTED, since: float = 0.0) -> list[dict]:
        """The newest moments first (each as `view` shows it). `since`: only those made after that time (Drive's look-in)."""
        out = []
        with self._lock:
            for mid in self._all_ids():
                if len(out) >= max(1, min(int(limit), LISTED)):
                    break
                ix = self.get(mid)
                if ix and ix.get("at", 0) > since:
                    out.append(self.view(ix))
        return out

    def view(self, ix: dict) -> dict:
        """What the page is told: the index, with `pending` only while the pods and pc could still be on their way."""
        v = dict(ix)
        if self.clock() - float(ix.get("at") or 0) > PENDING_FOR_S:
            v["pending"] = []
        return v

    def file(self, mid: str, name: str):
        """(bytes, content type) of one file of a moment, or None."""
        ix = self.get(mid)
        entry = next((f for f in (ix or {}).get("files", []) if f.get("name") == name), None)
        if not entry:
            return None
        data = self.store.get(f"{PREFIX}{mid}/{name}")
        return (data, entry.get("type") or "application/octet-stream") if data is not None else None

    # ── writing ──
    def _save(self, ix: dict) -> None:
        self.store.put(f"{PREFIX}{ix['id']}/index.json", json.dumps(ix, separators=(",", ":")).encode(), "application/json")
        self._index[ix["id"]] = ix

    def new(self, by: str, hold: bool, pending: list[str] | None = None) -> dict:
        with self._lock:
            now = self.clock()
            mid = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now))
            n = 0
            while mid in self._all_ids():                    # (the portal is the only writer: the ids it holds are all there are)
                n += 1
                mid = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now)) + f"-{n}"
            ix = {"id": mid, "at": int(now), "by": by if by in BY else "key", "hold": bool(hold), "kept": False,
                  "files": [], "seen": [], "said": "", "pending": list(pending or [])}
            self._save(ix)
            self._all_ids().insert(0, mid)
            self._ids.sort(reverse=True)
            return ix

    def add(self, mid: str, name: str, data: bytes, ctype: str, frm: str) -> dict | None:
        """Put one file into a moment (a file of the same name is replaced). None when there is no such moment."""
        with self._lock:
            ix = self.get(mid)
            if ix is None:
                return None
            self.store.put(f"{PREFIX}{mid}/{name}", data, ctype)
            ix["files"] = [f for f in ix["files"] if f.get("name") != name] + [{"name": name, "type": ctype, "bytes": len(data), "from": frm}]
            ix["files"].sort(key=lambda f: f["name"])
            self._save(ix)
            return ix

    def update(self, mid: str, **fields) -> dict | None:
        with self._lock:
            ix = self.get(mid)
            if ix is None:
                return None
            ix.update(fields)
            self._save(ix)
            return ix

    def delete(self, mid: str) -> bool:
        with self._lock:
            ix = self.get(mid)
            if ix is None:
                return False
            for f in ix.get("files", []):
                self.store.delete(f"{PREFIX}{mid}/{f['name']}")
            self.store.delete(f"{PREFIX}{mid}/index.json")
            self._index.pop(mid, None)
            if self._ids is not None and mid in self._ids:
                self._ids.remove(mid)
            return True

    def sweep(self, days: int = KEEP_CLIP_DAYS) -> int:
        """Clips older than `days`, except the ones he kept, go; the stills stay. Returns how many clips were deleted."""
        gone = 0
        with self._lock:
            for mid in list(self._all_ids()):
                ix = self.get(mid)
                if not ix or ix.get("kept") or self.clock() - float(ix.get("at") or 0) < days * 86400:
                    continue
                if any(f.get("name") == CLIP for f in ix.get("files", [])):
                    self.store.delete(f"{PREFIX}{mid}/{CLIP}")
                    ix["files"] = [f for f in ix["files"] if f.get("name") != CLIP]
                    self._save(ix)
                    gone += 1
        return gone


def core_door():
    """(url, key) of Skippy Core's Messages API, or None when it isn't configured (read the glasses' writing, say what is in view)."""
    import brains
    return (brains.CORE.base_url(), brains.CORE.key()) if brains.CORE.ready() else None


def init_app(app, home, notifier, store, clock=time.time) -> Moments:
    moments = Moments(store, clock)
    app.extensions["vision"] = moments
    bp = Blueprint("vision", __name__)

    def needs_store(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not moments.ready:
                return jsonify(error="Moments have no storage yet (TABLETOP_BUCKET)."), 503
            return view(*args, **kwargs)
        return wrapped

    def home_door(view):
        """skippy-pc's doors: the home token first, then the storage."""
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not home.authorized(request.headers.get("Authorization")):
                abort(401)
            return needs_store(view)(*args, **kwargs)
        return wrapped

    def stills(images) -> list[tuple[str, bytes, str]]:
        """The flash request's stills as (name, bytes, type); ValueError when anything is off (one bad one refuses the lot)."""
        if not isinstance(images, list) or len(images) > MAX_STILLS:
            raise ValueError("too many pictures")
        out, total = [], 0
        for item in images:
            name = str((item or {}).get("name") or "") if isinstance(item, dict) else ""
            if not PAGE_NAME.fullmatch(name):
                raise ValueError(f"{name!r} isn't a name a moment takes")
            data = decode_data_url(item.get("data"))
            ctype = sniff(data)
            if ctype != "image/jpeg" or len(data) > MAX_IMAGE:
                raise ValueError(f"{name} isn't a JPEG under {MAX_IMAGE // 1048576} MB")
            total += len(data)
            out.append((name, data, ctype))
        if total > MAX_FLASH_BYTES:
            raise ValueError("the pictures together are too big")
        return out

    def start_job(mid: str, owner: str) -> list[str]:
        """Ask skippy-pc for the pods' and the PC's screens (a `flash` job): what is still to come, or nothing when it isn't there."""
        if not home.online():
            return []
        home.start("flash", owner=owner, ttl=90, moment=mid)
        return ["pods", "pc"]

    @bp.post("/vision/flash")
    @auth.admin_required
    @needs_store
    def flash():
        """Save a moment now. Body: {hold, by, images: [{name, data: a JPEG data URL}]}: the page's own stills. The rest arrives
        later, file by file: the pods and the PC from skippy-pc, a hold's clip from the host, the Tesla's page from Drive."""
        if (request.content_length or 0) > MAX_FLASH_BYTES * 2:
            abort(413)
        body = request.get_json(silent=True) or {}
        try:
            got = stills(body.get("images") or [])
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        ix = moments.new(str(body.get("by") or "key"), bool(body.get("hold")))
        for name, data, ctype in got:
            moments.add(ix["id"], name, data, ctype, "glasses" if name.startswith("glasses") else "page")
        ix = moments.update(ix["id"], pending=start_job(ix["id"], auth.current_user()["email"])) or ix
        lead = next((d for n, d, _ in got if n in ("hud-page.jpg", "drive-page.jpg")), got[0][1] if got else None)
        notifier.alert("Your moment is saved.", lead)
        return jsonify(moments.view(ix))

    @bp.post("/vision/moment/<mid>/file")
    @auth.admin_required
    @needs_store
    def add_file(mid):
        """One more file for a moment (a raw body; ?name=): a hold's clip from the host, the Tesla's page from Drive."""
        name = request.args.get("name", "")
        limit = MAX_CLIP if name == CLIP else MAX_IMAGE
        if not (name == CLIP or PAGE_NAME.fullmatch(name)) or (request.content_length or 0) > limit:
            abort(400)
        data = request.get_data(cache=False)
        want = "application/zip" if name == CLIP else "image/jpeg"
        if len(data) > limit or sniff(data) != want:
            abort(400)
        ix = moments.add(mid, name, data, want, "glasses" if name.startswith("glasses") else "drive" if name == "drive-page.jpg" else "page")
        if ix is None:
            abort(404)
        return jsonify(moments.view(ix))

    def one_picture() -> bytes:
        """The JPEG of a read/look request (`image`: a data URL), or a 400."""
        if (request.content_length or 0) > MAX_IMAGE * 2:
            abort(413)
        try:
            data = decode_data_url((request.get_json(silent=True) or {}).get("image"))
        except ValueError:
            abort(400)
        if sniff(data) != "image/jpeg" or len(data) > MAX_IMAGE:
            abort(400)
        return data

    def ask_core(data: bytes, prompt: str, tokens: int):
        """(text, None) from Skippy Core's vision slot, or (None, a 503 reply)."""
        door = core_door()
        if door is None:
            return None, (jsonify(error="Skippy Core isn't there, so nothing can be read right now."), 503)
        try:
            return eyes_client.ask(door[0], door[1], data, "image/jpeg", prompt, max_tokens=tokens), None
        except RuntimeError as exc:
            return None, (jsonify(error=str(exc)), 503)

    @bp.post("/vision/read")
    @auth.admin_required
    def read_signs():
        """The writing in a picture that is not English, each with its language and an English reading: {seen: [{text, lang, en}]}.
        With ?moment=<id> the reading is kept in that moment."""
        text, refused = ask_core(one_picture(), READ_PROMPT, 500)
        if refused:
            return refused
        seen = parse_seen(text)
        mid = request.args.get("moment", "")
        if mid and moments.ready:
            moments.update(mid, seen=seen)
        return jsonify(seen=seen)

    @bp.post("/vision/look")
    @auth.admin_required
    def look():
        """One sentence on what the Captain is looking at: {said}. With ?moment=<id> it is kept in that moment."""
        text, refused = ask_core(one_picture(), LOOK_PROMPT, 120)
        if refused:
            return refused
        said = " ".join(text.split())[:300]
        mid = request.args.get("moment", "")
        if mid and moments.ready:
            moments.update(mid, said=said)
        return jsonify(said=said)

    @bp.get("/vision/moments")
    @auth.admin_required
    @needs_store
    def listing():
        return jsonify(moments=moments.list(request.args.get("limit", type=int, default=LISTED),
                                            request.args.get("since", type=float, default=0.0)))

    @bp.get("/vision/moment/<mid>/<name>")
    @auth.admin_required
    @needs_store
    def moment_file(mid, name):
        got = moments.file(mid, name) if (PAGE_NAME.fullmatch(name) or HOME_NAME.fullmatch(name) or name == CLIP) else None
        if got is None:
            abort(404)
        data, ctype = got
        resp = Response(data, mimetype=ctype)
        resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"        # a file of a moment never changes
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Content-Security-Policy"] = "sandbox"
        return resp

    @bp.post("/vision/moment/<mid>/delete")
    @auth.admin_required
    @needs_store
    def moment_delete(mid):
        if not moments.delete(mid):
            abort(404)
        return jsonify(ok=True)

    @bp.post("/vision/moment/<mid>/keep")
    @auth.admin_required
    @needs_store
    def moment_keep(mid):
        ix = moments.update(mid, kept=bool((request.get_json(silent=True) or {}).get("on")))
        if ix is None:
            abort(404)
        return jsonify(moments.view(ix))

    # ── skippy-pc's doors (the home token) ──
    @bp.post("/home/vision/flash")
    @home_door
    def home_flash():
        """Purple Skippy's `vision_flash` tool: a moment of what skippy-pc can see (the pods' and the PC's screens)."""
        ix = moments.new("tool", False)
        ix = moments.update(ix["id"], pending=start_job(ix["id"], "")) or ix
        return jsonify(moments.view(ix))

    @bp.get("/home/vision/moments")
    @home_door
    def home_moments():
        """Purple Skippy's `vision_moment` tool: the latest moments' indexes."""
        return jsonify(moments=moments.list(request.args.get("limit", type=int, default=5)))

    @bp.get("/home/vision/moment/<mid>/<name>")
    @home_door
    def home_moment_file(mid, name):
        got = moments.file(mid, name) if (PAGE_NAME.fullmatch(name) or HOME_NAME.fullmatch(name) or name == CLIP) else None
        if got is None:
            abort(404)
        return Response(got[0], mimetype=got[1], headers={"X-Content-Type-Options": "nosniff"})

    @bp.post("/home/vision/sweep")
    @home_door
    def home_sweep():
        """skippy-pc's nightly run: clips older than 90 days go (the stills never do)."""
        return jsonify(clips_deleted=moments.sweep())

    app.register_blueprint(bp)
    return moments


def take_from_home(moments: Moments, job: dict, name: str, data: bytes, ctype: str):
    """One screen skippy-pc made for a `flash` job (PUT /home/media/<job>?name=): into the moment the job names."""
    mid = str((job or {}).get("moment") or "")
    if not ID.fullmatch(mid) or not HOME_NAME.fullmatch(name or "") or len(data) > MAX_PNG or sniff(data) not in ("image/png", "image/jpeg"):
        return None
    frm = "pc" if name.startswith("pc-") else "pod:" + name[4:-4]
    return moments.add(mid, name, data, sniff(data), frm)


def finished(moments: Moments, job: dict) -> None:
    """skippy-pc says the `flash` job is over: nothing is pending any more."""
    mid = str((job or {}).get("moment") or "")
    if ID.fullmatch(mid):
        moments.update(mid, pending=[])
