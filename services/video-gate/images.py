"""images.py: posters and backdrops, scaled by Jellyfin once and kept on disk (/state/img), bounded, oldest evicted first.

A poster wall asks for hundreds of thumbnails at once; Jellyfin resizes each on first request, and this cache means it
only ever does so once per (item, kind, width, image tag). The tag is part of the key: new art in Jellyfin is a new entry.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("video-gate")

WIDTHS = (160, 320, 640, 1280)
DEFAULT_WIDTH = 640
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MIME = {v: k for k, v in EXT.items()}


def nearest_width(w: int) -> int:
    return min(WIDTHS, key=lambda x: (abs(x - w), x))


class ImageCache:
    def __init__(self, root: str | Path, limit: int = 500 * 1024 * 1024):
        self.root, self.limit = Path(root), limit
        self._lock = threading.Lock()
        self._total = 0
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._total = sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())
        except OSError:
            log.warning("video-gate: image cache directory is not usable; images will be fetched every time")

    @staticmethod
    def _key(item_id: str, kind: str, width: int, tag: str) -> str:
        return hashlib.sha1(f"{item_id}|{kind}|{width}|{tag}".encode()).hexdigest()

    def _path(self, key: str, ext: str) -> Path:
        return self.root / key[:2] / (key + ext)

    def get(self, item_id: str, kind: str, width: int, tag: str) -> tuple[Path, str] | None:
        key = self._key(item_id, kind, width, tag)
        for ext, mime in MIME.items():
            p = self._path(key, ext)
            try:
                if p.is_file():
                    os.utime(p)                       # a use is a refresh: the eviction below drops what nobody asks for
                    return p, mime
            except OSError:
                return None
        return None

    def put(self, item_id: str, kind: str, width: int, tag: str, data: bytes, mime: str) -> tuple[Path, str] | None:
        ext = EXT.get(mime)
        if not ext or not data:
            return None
        p = self._path(self._key(item_id, kind, width, tag), ext)
        tmp = p.with_name(p.name + f".{threading.get_ident()}.tmp")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            os.replace(tmp, p)                        # a reader never sees half a file
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            return None
        with self._lock:
            self._total += len(data)
            if self._total > self.limit:
                self._evict()
        return p, mime

    def _evict(self) -> None:
        """Drop the oldest files until the cache is at 90 % of its limit (the lock is held)."""
        try:
            files = sorted((f for f in self.root.rglob("*") if f.is_file()), key=lambda f: f.stat().st_mtime)
        except OSError:
            return
        sizes = {}
        for f in files:
            try:
                sizes[f] = f.stat().st_size
            except OSError:
                pass
        total = sum(sizes.values())
        for f in files:
            if total <= self.limit * 0.9:
                break
            try:
                f.unlink()
                total -= sizes.get(f, 0)
            except OSError:
                pass
        self._total = total
