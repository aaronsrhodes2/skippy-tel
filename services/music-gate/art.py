"""Cover-art thumbnails: the album grid shows hundreds of covers at once, and the embedded originals run to megabytes.

`GET /t/<pass>/art/<path>?w=192` asks for the cover fitted into a 192 px square. Only four widths exist (the cache stays
small and every phone and the Tesla hit the same few files); any other number snaps to the nearest one. The thumbnail is
made once, from the cover embedded in the track, then lives on disk under /state/art-cache, keyed by (path, file mtime,
width), so a retagged file gets a fresh one. The cache is bounded: past the limit the oldest thumbnails go first.

A cover that Pillow can't read (damaged, a format it doesn't know, absurdly large) is just "no cover" (404), never a 500.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("music-gate")

SIZES = (96, 192, 384, 768)
MAX_ASKED = 4096                     # ?w= beyond this is nonsense (400), not something to snap
MAX_PIXELS = 48_000_000              # a "cover" bigger than this is not one; refuse it before decoding (≈ 150 MB as RGB)
QUALITY = 80
DEFAULT_MAX_BYTES = 600 * 1024 * 1024
MATTE = (24, 24, 24)                 # what a transparent cover is laid on (the portal is dark)
MAX_BAD = 4096                       # covers known to be unreadable, so a grid doesn't re-open them on every scroll


class Busy(Exception):
    """Too many thumbnails are being made at once; the caller answers 503 and the player asks again."""


def pick_size(raw) -> int | None:
    """?w= → the nearest allowed width (a tie goes to the bigger one); None when it isn't a sensible whole number (→ 400)."""
    text = str(raw).strip()
    if not (text.isascii() and text.isdigit()):
        return None
    w = int(text)
    if not 1 <= w <= MAX_ASKED:
        return None
    return min(SIZES, key=lambda s: (abs(s - w), -s))


def render(cover: bytes, w: int) -> bytes | None:
    """The cover as a JPEG fitted into w×w (never enlarged); None when it can't be read."""
    from PIL import Image   # imported here so the tests of everything else don't need it
    try:
        with Image.open(io.BytesIO(cover)) as im:
            if im.width * im.height > MAX_PIXELS:
                return None
            im.draft("RGB", (w, w))   # a JPEG decodes at a fraction of its size: fast, and little memory for a 4000 px original
            transparent = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            if transparent:
                rgba = im.convert("RGBA")
                flat = Image.new("RGB", rgba.size, MATTE)
                flat.paste(rgba, mask=rgba.getchannel("A"))
                im = flat
            elif im.mode != "RGB":
                im = im.convert("RGB")
            im.thumbnail((w, w), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=QUALITY, optimize=True, progressive=True)
            return out.getvalue()
    except Exception:  # noqa: BLE001 — Pillow raises a dozen kinds for a bad image; every one means "no cover"
        return None


class ThumbCache:
    def __init__(self, directory: str | Path, max_bytes: int = DEFAULT_MAX_BYTES, workers: int = 4, backlog: int = 8,
                 clock=time.time):
        self.dir, self.max_bytes, self.clock = Path(directory), int(max_bytes), clock
        self._used: int | None = None                    # bytes on disk, counted at the first write
        self._busy = threading.BoundedSemaphore(workers)  # decoding is CPU and memory: at most this many at once...
        # ...and only this many requests may be making thumbnails or waiting to: the rest are told to come back (Busy), not
        # parked on a thread. The gate has 32 threads and a track that streams holds one for its whole length, so a grid of 40
        # covers that all missed the cache must not be able to take them all.
        self._pending = threading.BoundedSemaphore(workers + backlog)
        self._stripes = [threading.Lock() for _ in range(32)]   # two requests for one thumbnail make it once
        self._evicting = threading.Lock()
        self._mutex = threading.Lock()
        self._bad: dict[str, None] = {}

    def key(self, rel: str, mtime_ns: int, w: int) -> str:
        return hashlib.sha1(f"{rel}\0{mtime_ns}\0{w}".encode("utf-8", "surrogatepass")).hexdigest()

    def get(self, rel: str, full: Path, w: int, load_cover) -> tuple[str, bytes] | None:
        """(etag, JPEG bytes) for this track's cover at width w, or None if it has none or can't be read. Raises Busy when
        too many thumbnails are being made already. load_cover() → (bytes, mime) | None is only called when the thumbnail
        doesn't exist yet."""
        try:
            mtime_ns = full.stat().st_mtime_ns
        except OSError:
            return None
        key = self.key(rel, mtime_ns, w)
        path = self.dir / key[:2] / f"{key}.jpg"
        hit = self._read(path)
        if hit is not None:
            return key, hit
        if key in self._bad:
            return None
        if not self._pending.acquire(blocking=False):
            raise Busy
        try:
            with self._stripes[int(key[:4], 16) % len(self._stripes)]:
                hit = self._read(path)                   # made by whoever held the stripe before us
                if hit is not None:
                    return key, hit
                with self._busy:
                    found = load_cover()
                    data = render(found[0], w) if found else None
                if data is None:
                    self._remember_bad(key)
                    return None
                self._store(path, data)
                return key, data
        finally:
            self._pending.release()

    # ── disk ─────────────────────────────────────────────────────────────
    def _read(self, path: Path) -> bytes | None:
        try:
            st = path.stat()
            data = path.read_bytes()
        except OSError:
            return None
        if self.clock() - st.st_mtime > 12 * 3600:       # a read keeps a thumbnail young, so eviction drops what nobody looks at
            try:
                os.utime(path, None)
            except OSError:
                pass
        return data

    def _store(self, path: Path, data: bytes) -> None:
        with self._mutex:
            if self._used is None:                       # first write of this process: count what an earlier one left
                self._used = sum(size for _m, size, _p in self._survey())
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except OSError as exc:                           # a full or read-only disk costs the cache, not the cover
            log.warning("music-gate: couldn't cache a thumbnail: %s", exc)
            return
        with self._mutex:
            self._used += len(data)
            over = self._used > self.max_bytes
        if over:
            self.evict()

    def _survey(self) -> list[tuple[float, int, str]]:
        """Every thumbnail on disk as (mtime, size, path); a temp file a crash left behind is cleared away."""
        found, now = [], self.clock()
        try:
            subdirs = [d.path for d in os.scandir(self.dir) if d.is_dir()]
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
        """Delete the oldest thumbnails until the cache is under 90 % of its limit (so it isn't done on every write). Returns
        the bytes left. One pass at a time: a second caller just carries on."""
        if not self._evicting.acquire(blocking=False):
            return self._used or 0
        try:
            entries = self._survey()
            total = sum(size for _m, size, _p in entries)
            if total > self.max_bytes:
                for _mtime, size, path in sorted(entries):
                    if total <= int(self.max_bytes * 0.9):
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
