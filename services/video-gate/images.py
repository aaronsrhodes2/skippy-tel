"""images.py: posters and backdrops, scaled by Jellyfin once and kept on disk (/state/img), bounded, oldest evicted first.

`GET /t/<pass>/img/<id>/<kind>?w=320` asks for the picture fitted into 320 px of width. Only four widths exist (the cache
stays small and every phone and the Tesla hit the same few files); any other whole number snaps to the nearest one. A poster
wall asks for hundreds of thumbnails at once, and Jellyfin resizes each on first request, so each (item, kind, width, image
tag) is fetched from Jellyfin once and then lives on disk. The tag is part of the key: new art in Jellyfin is a new entry, and
the key doubles as the ETag.

The same rules as the music gate's cover thumbnails (services/music-gate/art.py), for the same reason: a streaming film holds
one of the gate's threads for its whole length, so a grid of 40 uncached posters must not be able to take them all. At most
`workers` fetches run at once and `backlog` more may wait; past that the answer is Busy (the route says 503 + Retry-After) and
the player asks again a moment later. A cached image never waits and is never turned away. The cache is bounded: past the limit
the oldest files go first. A picture Jellyfin says it hasn't got is remembered (bounded), so a grid doesn't ask again on every
scroll.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("video-gate")

WIDTHS = (160, 320, 640, 1280)
DEFAULT_WIDTH = 640
MAX_ASKED = 4096                      # ?w= beyond this is nonsense (400), not something to snap
DEFAULT_MAX_BYTES = 500 * 1024 * 1024
MAX_BAD = 4096                        # pictures known to be missing, so a grid doesn't re-ask Jellyfin on every scroll
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MIME = {v: k for k, v in EXT.items()}


class Busy(Exception):
    """Too many images are being fetched at once; the caller answers 503 and the player asks again."""


def pick_width(raw) -> int | None:
    """?w= → the nearest allowed width (a tie goes to the bigger one); None when it isn't a sensible whole number (→ 400)."""
    text = str(raw).strip()
    if not (text.isascii() and text.isdigit()):
        return None
    w = int(text)
    if not 1 <= w <= MAX_ASKED:
        return None
    return min(WIDTHS, key=lambda s: (abs(s - w), -s))


class ImageCache:
    def __init__(self, root: str | Path, limit: int = DEFAULT_MAX_BYTES, workers: int = 4, backlog: int = 8,
                 clock=time.time):
        self.root, self.limit, self.clock = Path(root), int(limit), clock
        self._used: int | None = None                     # bytes on disk, counted at the first write
        self._busy = threading.BoundedSemaphore(workers)   # fetches in flight at once...
        # ...and only this many requests may be fetching or waiting to: the rest are told to come back (Busy), not parked on a
        # thread. The gate has a fixed number of threads and a film that streams holds one for its whole length.
        self._pending = threading.BoundedSemaphore(workers + backlog)
        self._stripes = [threading.Lock() for _ in range(32)]   # two requests for one image fetch it once
        self._evicting = threading.Lock()
        self._mutex = threading.Lock()
        self._bad: dict[str, None] = {}

    @staticmethod
    def key(item_id: str, kind: str, width: int, tag: str) -> str:
        return hashlib.sha1(f"{item_id}|{kind}|{width}|{tag}".encode()).hexdigest()

    def get(self, item_id: str, kind: str, width: int, tag: str, load) -> tuple[str, str, bytes] | None:
        """(etag, mime, bytes) of this image, from disk or fetched. None when Jellyfin has no such image. Raises Busy when too
        many are being fetched already. load() → (bytes, mime) | None is only called when the image isn't on disk yet, and
        whatever it raises (the route's JellyfinError) goes to the caller untouched."""
        key = self.key(item_id, kind, width, tag)
        hit = self._read(key)
        if hit is not None:
            return key, hit[1], hit[0]
        if key in self._bad:
            return None
        if not self._pending.acquire(blocking=False):
            raise Busy
        try:
            with self._stripes[int(key[:4], 16) % len(self._stripes)]:
                hit = self._read(key)                       # fetched by whoever held the stripe before us
                if hit is not None:
                    return key, hit[1], hit[0]
                with self._busy:
                    found = load()
                if not found or not found[0]:
                    self._remember_bad(key)
                    return None
                data, mime = found
                self._store(key, data, mime)
                return key, mime, data
        finally:
            self._pending.release()

    # ---- disk -------------------------------------------------------------
    def _path(self, key: str, ext: str) -> Path:
        return self.root / key[:2] / (key + ext)

    def _read(self, key: str) -> tuple[bytes, str] | None:
        for ext, mime in MIME.items():
            path = self._path(key, ext)
            try:
                st = path.stat()
                data = path.read_bytes()
            except OSError:
                continue
            if self.clock() - st.st_mtime > 12 * 3600:      # a read keeps an image young, so eviction drops what nobody looks at
                try:                                        # (and only now and then: a touch on every hit would be a write per poster)
                    os.utime(path, None)
                except OSError:
                    pass
            return data, mime
        return None

    def _store(self, key: str, data: bytes, mime: str) -> None:
        ext = EXT.get(mime)
        if not ext:
            return                                          # served this once, not kept
        with self._mutex:
            if self._used is None:                          # first write of this process: count what an earlier one left
                self._used = sum(size for _m, size, _p in self._survey())
        path = self._path(key, ext)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)                           # a reader never sees half a file
        except OSError as exc:                              # a full or read-only disk costs the cache, not the picture
            log.warning("video-gate: couldn't cache an image: %s", type(exc).__name__)
            return
        with self._mutex:
            self._used += len(data)
            over = self._used > self.limit
        if over:
            self.evict()

    def _survey(self) -> list[tuple[float, int, str]]:
        """Every cached image as (mtime, size, path); a temp file a crash left behind is cleared away once it is old."""
        found, now = [], self.clock()
        try:
            subdirs = [d.path for d in os.scandir(self.root) if d.is_dir()]
        except OSError:
            return found
        for sub in subdirs:
            try:
                files = list(os.scandir(sub))
            except OSError:
                continue
            for f in files:
                try:
                    st = f.stat()
                except OSError:
                    continue
                if f.name.endswith(".tmp"):
                    if now - st.st_mtime > 3600:
                        _unlink(f.path)
                    continue
                found.append((st.st_mtime, st.st_size, f.path))
        return found

    def evict(self) -> int:
        """Delete the oldest images until the cache is under 90 % of its limit (so it isn't done on every write). Returns the
        bytes left. One pass at a time: a second caller just carries on."""
        if not self._evicting.acquire(blocking=False):
            return self._used or 0
        try:
            entries = self._survey()
            total = sum(size for _m, size, _p in entries)
            if total > self.limit:
                for _mtime, size, path in sorted(entries):
                    if total <= int(self.limit * 0.9):
                        break
                    if _unlink(path):
                        total -= size
            with self._mutex:
                self._used = total
            return total
        finally:
            self._evicting.release()

    def _remember_bad(self, key: str) -> None:
        with self._mutex:
            if len(self._bad) >= MAX_BAD:
                self._bad.pop(next(iter(self._bad)))
            self._bad[key] = None


def _unlink(path: str) -> bool:
    try:
        os.unlink(path)
        return True
    except OSError:
        return False
