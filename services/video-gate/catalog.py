"""catalog.py: the Captain's Jellyfin library as the video player sees it, built once and served from memory.

The player downloads one gzipped JSON (items + collections) and does browsing, search and the shelves itself, so every
route that needs to know an item (does it exist, can it direct-play, which subtitle indexes are real) asks this module,
never Jellyfin: that is also what keeps a hostile id or index from ever reaching the server. If Jellyfin goes away the
last good catalog keeps being served.

`direct` is the conservative question "does every mainstream browser play this file as it is?": MP4/MOV/M4V + 8-bit H.264
(4:2:0, no High 10/4:2:2) + AAC or MP3 as the first audio stream. Anything else (MKV, HEVC, AC3, DTS, MP2, AVI...) is
false and plays through HLS, where Jellyfin remuxes or transcodes.
"""
from __future__ import annotations

import calendar
import gzip
import hashlib
import json
import logging
import re
import threading
import time

from jellyfin import ID_RE, IMAGE_KINDS, Client, JellyfinError

log = logging.getLogger("video-gate")

TICKS = 10_000_000                       # Jellyfin counts time in 100 ns ticks
MIN_RESUME_S = 30                        # a quick peek is not worth "continue watching"
PLAYED_AT = 0.90                         # Jellyfin's own default: past 90 % the film counts as watched
IMAGE_CODECS = {"mjpeg", "png", "bmp", "gif", "webp", "tiff"}      # cover art shows up as a "video" stream in some MP4s
SAFE_PIXFMT = {"yuv420p", "yuvj420p", "nv12"}                      # 8-bit 4:2:0: what browsers decode in hardware
BAD_H264_PROFILE = re.compile(r"high\s*(10|4:2:2|4:4:4)|hi(10|422|444)", re.I)
AAC_PROFILES = {"", "lc", "he-aac", "he-aacv2", "aac-lc"}


def _text(value, limit: int) -> str:
    """A string from Jellyfin, with control characters removed and cut to `limit` characters."""
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value) if isinstance(value, str) else ""
    s = s.strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _int(value, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _epoch(value) -> int:
    """'2023-09-11T09:29:04.0000000Z' → epoch seconds (UTC); 0 when it isn't a date."""
    m = re.match(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})", value) if isinstance(value, str) else None
    if not m:
        return 0
    try:
        return calendar.timegm(tuple(int(g) for g in m.groups()) + (0, 0, 0))
    except (ValueError, OverflowError):
        return 0


def _norm_id(value) -> str:
    """Jellyfin ids without dashes, lower case; '' when it isn't one."""
    s = value.replace("-", "").lower() if isinstance(value, str) else ""
    return s if ID_RE.fullmatch(s) else ""


def container_of(raw) -> str:
    """Jellyfin's Container ('mov,mp4,m4a,3gp,3g2,mj2' is ffprobe's name for the whole MP4 family; 'mkv', 'avi'...) as
    one word: 'mp4' for that family, 'mkv' for Matroska, else the first name."""
    names = [n.strip().lower() for n in str(raw or "").split(",") if n.strip()]
    if any(n in ("mp4", "mov", "m4v") for n in names):
        return "mp4"
    if any(n in ("mkv", "matroska", "webm") for n in names):
        return "mkv"
    return names[0][:16] if names else ""


def _lang(value) -> str:
    s = value.lower() if isinstance(value, str) else ""
    return s if re.fullmatch(r"[a-z]{2,3}", s) else "und"


def play_info(raw: dict) -> dict:
    """The `play` block: how this file can be played. See the module docstring for what `direct` means."""
    streams = [s for s in (raw.get("MediaStreams") or []) if isinstance(s, dict)]
    videos = [s for s in streams if s.get("Type") == "Video" and str(s.get("Codec", "")).lower() not in IMAGE_CODECS]
    audios = [s for s in streams if s.get("Type") == "Audio"]
    subs = [s for s in streams if s.get("Type") == "Subtitle"]
    container = container_of(raw.get("Container"))
    video = videos[0] if videos else {}
    first_audio = audios[0] if audios else {}
    vcodec = str(video.get("Codec", "")).lower()[:16]
    acodec = str(first_audio.get("Codec", "")).lower()[:16]

    direct = bool(video) and container == "mp4" and vcodec == "h264"
    if direct:
        pixfmt = str(video.get("PixelFormat") or "").lower()
        depth = video.get("BitDepth")
        if depth is None and pixfmt:
            depth = 10 if re.search(r"10|12|16", pixfmt) else 8
        direct = (depth == 8 and (not pixfmt or pixfmt in SAFE_PIXFMT)
                  and not BAD_H264_PROFILE.search(str(video.get("Profile") or ""))
                  and 0 < _int(video.get("Height"), 0) <= 2160 and _int(video.get("Level"), 0) <= 52)
    if direct and audios:       # no audio at all plays fine; the FIRST audio stream is what a browser plays
        profile = str(first_audio.get("Profile") or "").lower()
        direct = (acodec == "mp3" and _int(first_audio.get("Channels"), 2) <= 2) or (acodec == "aac" and profile in AAC_PROFILES)

    return {
        "container": container, "vcodec": vcodec, "acodec": acodec, "height": _int(video.get("Height")), "direct": direct,
        "audio": [{"i": _int(a.get("Index")), "lang": _lang(a.get("Language")), "codec": str(a.get("Codec", "")).lower()[:16],
                   "ch": _int(a.get("Channels"))} for a in audios[:16]],
        "subs": [{"i": _int(s.get("Index")), "lang": _lang(s.get("Language")), "codec": str(s.get("Codec", "")).lower()[:16],
                  "external": bool(s.get("IsExternal")), "vtt": bool(s.get("IsTextSubtitleStream"))} for s in subs[:32]],
    }


def build_item(raw: dict) -> tuple[dict, dict] | None:
    """(catalog item, image tags) for one Jellyfin item, or None when it has no usable id."""
    item_id = _norm_id(raw.get("Id"))
    kind = str(raw.get("Type", "")).lower()
    if not item_id or kind not in ("movie", "series", "episode"):
        return None
    data = raw.get("UserData") if isinstance(raw.get("UserData"), dict) else {}
    ticks = _int(raw.get("RunTimeTicks"))
    tags = {}
    for name, jf_kind in IMAGE_KINDS.items():
        if jf_kind == "Backdrop":
            found = (raw.get("BackdropImageTags") or [None])[0]
        else:
            found = (raw.get("ImageTags") or {}).get(jf_kind) if isinstance(raw.get("ImageTags"), dict) else None
        if isinstance(found, str) and re.fullmatch(r"[0-9a-zA-Z]{1,64}", found):
            tags[name] = found
    rating = raw.get("CommunityRating")
    title = _text(raw.get("Name"), 200) or "Untitled"
    item = {
        "id": item_id, "type": kind, "title": title, "sort": _text(raw.get("SortName"), 200).lower() or title.lower(),
        "year": _int(raw.get("ProductionYear")), "runtime": ticks // TICKS,
        "overview": _text(raw.get("Overview"), 400),
        "genres": [_text(g, 40) for g in (raw.get("Genres") or [])[:8] if isinstance(g, str)],
        "rating": round(float(rating), 1) if isinstance(rating, (int, float)) and not isinstance(rating, bool) else None,
        "official": _text(raw.get("OfficialRating"), 12), "added": _epoch(raw.get("DateCreated")),
        "collectionIds": [], "hasPoster": "poster" in tags, "hasBackdrop": "backdrop" in tags,
        "resume": _int(data.get("PlaybackPositionTicks")) // TICKS, "played": bool(data.get("Played")),
        "play": play_info(raw) if kind != "series" else None,        # a series is a shelf, not a file
    }
    if kind == "episode":
        item.update(seriesId=_norm_id(raw.get("SeriesId")), season=_int(raw.get("ParentIndexNumber")),
                    ep=_int(raw.get("IndexNumber")))
    return item, tags


def progress_outcome(pos: float, runtime: int, state: str, played_flag: bool | None) -> tuple[int, bool | None]:
    """What a progress report means for Continue Watching: (resume seconds, played) where played is None for 'leave as is'.
    The thresholds are Jellyfin's own: 90 % through, on stop, is watched; a few seconds in is not worth resuming."""
    pos = max(0.0, float(pos))
    if played_flag is True:
        return 0, True
    resume = int(pos) if pos >= MIN_RESUME_S else 0
    if state == "stop" and runtime > 0 and pos >= PLAYED_AT * runtime:
        return 0, True
    return resume, (False if played_flag is False else None)


class Library:
    """The catalog: items, collections and image tags, replaced whole by refresh() and patched by apply_progress()."""

    def __init__(self, client: Client, user: str = "", clock=time.time, refresh_s: float = 300.0, min_gap_s: float = 60.0):
        self.client, self.user, self.clock = client, user.strip(), clock
        self.refresh_s, self.min_gap_s = refresh_s, min_gap_s
        self.user_id = ""
        self.built_at = 0.0               # a full build has succeeded (0: never)
        self.tried_at = 0.0
        self.error = ""                   # why the last refresh failed ('' when it didn't)
        self._lock = threading.RLock()
        self._busy = threading.Lock()
        self._items: dict[str, dict] = {}
        self._collections: list[dict] = []
        self._tags: dict[str, dict] = {}
        self._gz, self._etag, self._dirty = b"", "", False
        self._up_at, self._up = -1e9, False

    # ---- reading -----------------------------------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.built_at > 0

    @property
    def count(self) -> int:
        return len(self._items)

    def get(self, item_id: str) -> dict | None:
        return self._items.get(item_id)

    def tags(self, item_id: str) -> dict:
        return self._tags.get(item_id, {})

    def collection_tags(self, coll_id: str) -> dict | None:
        return self._tags.get(coll_id)

    def snapshot(self) -> tuple[bytes, str]:
        """(gzipped JSON, ETag): rebuilt only when something changed, so a 304 costs nothing."""
        with self._lock:
            if self._dirty:
                items = sorted(self._items.values(), key=lambda i: (i["sort"], i["id"]))
                body = json.dumps({"v": 1, "items": items, "collections": self._collections},
                                  separators=(",", ":")).encode()
                self._gz = gzip.compress(body, 6, mtime=0)
                self._etag = '"' + hashlib.sha1(body).hexdigest()[:20] + '"'
                self._dirty = False
            return self._gz, self._etag

    def online(self) -> bool:
        """Is Jellyfin answering right now? Asked at most every 5 s however often /health is hit."""
        now = self.clock()
        if now - self._up_at >= 5.0:
            self._up, self._up_at = self.client.ping(), now
        return self._up

    # ---- writing -----------------------------------------------------------------------------------------------
    def apply_progress(self, item_id: str, resume: int, played: bool | None) -> None:
        """Mirror a successful progress write into the catalog so the next catalog download already shows it."""
        with self._lock:
            item = self._items.get(item_id)
            if item is not None:
                item["resume"] = resume
                if played is not None:
                    item["played"] = played
                self._dirty = True

    def ensure_user(self) -> str:
        """The Jellyfin user whose library and watch state the player shows: JELLYFIN_USER (name or id), else the first
        administrator. Resolved when Jellyfin first answers, then kept."""
        if self.user_id:
            return self.user_id
        users = [u for u in self.client.users() if isinstance(u, dict) and _norm_id(u.get("Id"))]
        pick = None
        if self.user:
            pick = next((u for u in users if _norm_id(u.get("Id")) == self.user.replace("-", "").lower()
                         or str(u.get("Name", "")).lower() == self.user.lower()), None)
        else:
            pick = next((u for u in users if (u.get("Policy") or {}).get("IsAdministrator")), None) or (users[0] if users else None)
        if not pick:
            raise JellyfinError("bad")
        self.user_id = _norm_id(pick["Id"])
        return self.user_id

    def refresh(self) -> bool:
        """Read the whole library from Jellyfin. On any failure the last good catalog stays; returns whether it worked."""
        if not self._busy.acquire(blocking=False):
            return False                       # someone is already doing it
        self.tried_at = self.clock()
        try:
            uid = self.ensure_user()
            items, tags = {}, {}
            for raw in self.client.items(uid):
                built = build_item(raw) if isinstance(raw, dict) else None
                if built:
                    items[built[0]["id"]] = built[0]
                    tags[built[0]["id"]] = built[1]
            collections = []
            for box in self.client.collections(uid):
                coll_id = _norm_id(box.get("Id")) if isinstance(box, dict) else ""
                if not coll_id:
                    continue
                members = [m for m in (_norm_id(c.get("Id")) for c in self.client.children(uid, coll_id) if isinstance(c, dict))
                           if m in items]
                members.sort(key=lambda m: (items[m]["year"] or 9999, items[m]["sort"]))
                built = build_item({**box, "Type": "Movie"})        # reuse the image-tag reading; a box set has no stream
                ctags = built[1] if built else {}
                tags[coll_id] = ctags
                collections.append({"id": coll_id, "title": _text(box.get("Name"), 200) or "Collection",
                                    "sort": _text(box.get("SortName"), 200).lower() or _text(box.get("Name"), 200).lower(),
                                    "itemIds": members, "hasPoster": "poster" in ctags, "hasBackdrop": "backdrop" in ctags})
                for m in members:
                    items[m]["collectionIds"].append(coll_id)
            collections.sort(key=lambda c: (c["sort"], c["id"]))
            with self._lock:
                self._items, self._collections, self._tags = items, collections, tags
                self._dirty = True
                self.built_at, self.error = self.clock(), ""
            log.info("video-gate: catalog built, %d items, %d collections", len(items), len(collections))
            return True
        except JellyfinError as e:
            self.error = e.kind
            log.warning("video-gate: catalog refresh failed (%s); serving the last good one", e.kind)
            return False
        except Exception as e:  # noqa: BLE001 — a bug in a Jellyfin answer must not kill the refresh thread
            self.error = "bug"
            log.error("video-gate: catalog refresh crashed: %s", type(e).__name__)
            return False
        finally:
            self._busy.release()

    def poke(self) -> None:
        """A player asked for the catalog: refresh in the background if the last attempt is over a minute old."""
        if self.clock() - self.tried_at >= self.min_gap_s and not self._busy.locked():
            self.tried_at = self.clock()
            threading.Thread(target=self.refresh, daemon=True, name="catalog-poke").start()

    def run_forever(self) -> None:
        while True:
            ok = self.refresh()
            time.sleep(self.refresh_s if ok else min(30.0, self.refresh_s))
