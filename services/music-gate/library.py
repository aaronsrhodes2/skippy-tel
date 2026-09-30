"""The Captain's music library, as the Music app sees it: every MP3 and AAC file under /music.

Only MP3 and AAC, on purpose (the Captain's call): every browser plays them, Safari and the Tesla included, so nothing is
ever transcoded. FLAC, WMA, OGG, WAV and Apple Lossless (ALAC in an .m4a) are left out of the catalog.

Tags come from the files (mutagen). Reading 21k files over Docker Desktop's bind mount takes minutes, so each file's tags
are cached by (size, mtime) in /state and only new or changed files are read again. The catalog is one gzipped JSON the
player downloads once: the browser does browsing, search and the DJ's picks itself.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("music-gate")

AUDIO = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac"}
FIELDS = ["p", "t", "a", "al", "n", "d", "g", "s", "art"]   # path, title, artist, album, track, disc, genre, seconds, has art
CACHE_VERSION = 1


def _first(value) -> str:
    """The first text of a tag value (ID3 frame, MP4 list or plain), trimmed; '' if none."""
    if value is None:
        return ""
    if hasattr(value, "text"):
        value = value.text
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, tuple):
        value = value[0]
    return str(value).replace("\x00", " ").strip()


def _number(value) -> int:
    """'3', '3/12' or MP4's [(3, 12)] → 3; anything else → 0."""
    if isinstance(value, list) and value and isinstance(value[0], tuple):
        n = value[0][0]
        return int(n) if isinstance(n, int) and 0 <= n < 10000 else 0
    m = re.match(r"\s*(\d{1,4})", _first(value))
    return int(m.group(1)) if m else 0


def _title_from_name(name: str) -> str:
    """'01 - Artist - Title.mp3' → 'Title' (the same rule as flask-api's bilby_local)."""
    stem = re.sub(r"^\d+[-_.\s]+", "", name.rsplit(".", 1)[0]).replace("_", " ").strip()
    for sep in (" - ", " – ", " — "):
        if sep in stem:
            return stem.split(sep, 1)[1].strip() or stem
    return stem or name


def read_tags(path: Path) -> dict | None:
    """Title, artist, album, track, disc, genre, seconds and whether there is cover art. None: unreadable, or an .m4a
    that isn't AAC (Apple Lossless), which the Music app doesn't play."""
    import mutagen   # imported here so the tests of everything else don't need it
    try:
        f = mutagen.File(path)
    except Exception:  # noqa: BLE001 — a damaged or locked file is left out, not fatal
        return None
    if f is None:
        return None
    info, tags = getattr(f, "info", None), f.tags or {}
    codec = str(getattr(info, "codec", "") or "")
    if path.suffix.lower() == ".m4a" and codec and not codec.startswith("mp4a"):
        return None
    if path.suffix.lower() == ".m4a":
        get = lambda k: tags.get(k)                                                    # noqa: E731
        t = {"t": _first(get("\xa9nam")), "a": _first(get("aART")) or _first(get("\xa9ART")), "al": _first(get("\xa9alb")),
             "n": _number(get("trkn")), "d": _number(get("disk")), "g": _first(get("\xa9gen")), "art": bool(get("covr"))}
    else:
        get = lambda k: tags.get(k) if hasattr(tags, "get") else None                   # noqa: E731
        apic = tags.getall("APIC") if hasattr(tags, "getall") else []
        t = {"t": _first(get("TIT2")), "a": _first(get("TPE2")) or _first(get("TPE1")), "al": _first(get("TALB")),
             "n": _number(get("TRCK")), "d": _number(get("TPOS")), "g": _first(get("TCON")), "art": bool(apic)}
    t["s"] = int(getattr(info, "length", 0) or 0)
    return t


def read_art(path: Path) -> tuple[bytes, str] | None:
    """The file's embedded cover (bytes, mime), or None."""
    import mutagen
    try:
        f = mutagen.File(path)
    except Exception:  # noqa: BLE001
        return None
    tags = getattr(f, "tags", None)
    if not tags:
        return None
    if path.suffix.lower() == ".m4a":
        covers = tags.get("covr") or []
        if covers:
            c = covers[0]
            return bytes(c), "image/png" if getattr(c, "imageformat", 13) == 14 else "image/jpeg"
        return None
    for apic in tags.getall("APIC") if hasattr(tags, "getall") else []:
        mime = apic.mime if apic.mime in ("image/jpeg", "image/png", "image/webp") else "image/jpeg"
        return bytes(apic.data), mime
    return None


class Library:
    def __init__(self, root: str | Path, state: str | Path, reader=read_tags, clock=time.time):
        self.root, self.reader, self.clock = Path(root), reader, clock
        self.cache_path = Path(state) / "tags.json"
        self._lock = threading.Lock()
        self._cache: dict[str, list] = {}          # rel path → [size, mtime, tags or None]
        self.catalog_gz = b""
        self.etag = ""
        self.count = 0
        self.ready = False                          # a full scan has finished
        self.scanning = False
        self.scanned = 0
        self.scanned_at = 0.0
        self._load_cache()

    # ── the tag cache ────────────────────────────────────────────────────
    def _load_cache(self) -> None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if data.get("v") == CACHE_VERSION and isinstance(data.get("files"), dict):
                self._cache = data["files"]
        except (OSError, ValueError):
            return
        self._build(self._cache)   # last run's catalog, playable while the new scan runs

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": CACHE_VERSION, "files": self._cache}), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError as exc:
            log.warning("music-gate: couldn't save the tag cache: %s", exc)

    # ── scanning ─────────────────────────────────────────────────────────
    def scan(self) -> int:
        """Walk the library, read new or changed files' tags, and rebuild the catalog. Returns the track count."""
        if not self.root.is_dir():
            log.warning("music-gate: %s isn't mounted", self.root)
            return 0
        self.scanning, self.scanned = True, 0
        seen: dict[str, list] = {}
        try:
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                for name in sorted(filenames):
                    if name.startswith(".") or Path(name).suffix.lower() not in AUDIO:
                        continue
                    full = Path(dirpath) / name
                    rel = full.relative_to(self.root).as_posix()
                    try:
                        st = full.stat()
                    except OSError:
                        continue
                    old = self._cache.get(rel)
                    if old and old[0] == st.st_size and old[1] == int(st.st_mtime):
                        seen[rel] = old
                    else:
                        seen[rel] = [st.st_size, int(st.st_mtime), self.reader(full)]
                    self.scanned += 1
                    if self.scanned % 2000 == 0:
                        log.info("music-gate: %d files looked at", self.scanned)
                        self._cache.update(seen)
                        self._save_cache()
        finally:
            self.scanning = False
        self._cache = seen
        self._save_cache()
        self._build(seen)
        self.ready, self.scanned_at = True, self.clock()
        log.info("music-gate: catalog has %d tracks", self.count)
        return self.count

    def _build(self, files: dict[str, list]) -> None:
        tracks = []
        for rel, (_size, _mtime, tags) in sorted(files.items(), key=lambda kv: kv[0].lower()):
            if not tags:
                continue
            parts = rel.split("/")
            folder_artist = parts[0] if len(parts) >= 2 else ""
            folder_album = parts[1] if len(parts) >= 3 else ""
            tracks.append([rel, tags.get("t") or _title_from_name(parts[-1]),
                           folder_artist or tags.get("a") or "Unknown artist",
                           folder_album or tags.get("al") or "",
                           tags.get("n") or 0, tags.get("d") or 0, tags.get("g") or "", tags.get("s") or 0,
                           1 if tags.get("art") else 0])
        body = json.dumps({"v": 1, "fields": FIELDS, "tracks": tracks}, separators=(",", ":")).encode()
        gz = gzip.compress(body, 6, mtime=0)
        with self._lock:
            self.catalog_gz, self.count = gz, len(tracks)
            self.etag = f'"{len(tracks)}-{hash(gz) & 0xffffffff:08x}"'

    def run_forever(self, every_s: float = 6 * 3600) -> None:
        while True:
            try:
                self.scan()
            except Exception:  # noqa: BLE001 — a bad scan keeps the old catalog; try again later
                log.exception("music-gate: scan failed")
            time.sleep(every_s)

    # ── files ────────────────────────────────────────────────────────────
    def resolve(self, rel: str) -> Path | None:
        """The file for a catalog path: inside the library, an MP3/AAC file, existing. Anything else → None."""
        if not rel or "\x00" in rel or "\\" in rel or Path(rel).suffix.lower() not in AUDIO:
            return None
        root = self.root.resolve()
        try:
            full = (root / rel).resolve()
            full.relative_to(root)
        except (ValueError, OSError):
            return None
        return full if full.is_file() else None
