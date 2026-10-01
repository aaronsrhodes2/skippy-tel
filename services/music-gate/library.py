"""The Captain's music library, as the Music app sees it: every MP3 and AAC file under /music.

Only MP3 and AAC, on purpose (the Captain's call): every browser plays them, Safari and the Tesla included, so nothing is
ever transcoded. FLAC, WMA, OGG, WAV and Apple Lossless (ALAC in an .m4a) are left out of the catalog.

Tags come from the files (mutagen). Reading 21k files over Docker Desktop's bind mount takes minutes, so each file's tags
are cached by (size, mtime) in /state and only new or changed files are read again. The catalog is one gzipped JSON the
player downloads once: the browser does browsing, search and the DJ's picks itself.

The catalog's fields are listed in its own payload and only ever grow at the END, so an older player keeps working when
one is added (it reads the names it knows).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("music-gate")

AUDIO = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac"}
# path, title, artist, album, track, disc, genre, seconds, has art, year (0 unknown), file mtime (epoch s: "date added")
FIELDS = ["p", "t", "a", "al", "n", "d", "g", "s", "art", "y", "m"]
CACHE_VERSION = 2          # 2 added the year to each file's tags
READABLE_VERSIONS = (1, 2)  # a v1 cache still makes a playable catalog while the scan reads the years in


def etag_matches(header: str | None, etag: str) -> bool:
    """Does an If-None-Match header name this ETag? It may list several, be weak (W/"x") or be '*'; a proxy may do any of it."""
    if not header or not etag:
        return False
    if header.strip() == "*":
        return True
    return any(part.strip().removeprefix("W/") == etag for part in header.split(","))


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


def _year(value) -> int:
    """'2016', '2016-03-04', '2016-03-04T08:00:00Z' or '20160304' → 2016; no plausible year → 0 (unknown)."""
    s = _first(value)
    m = re.match(r"\s*(\d{4})", s) or re.search(r"(?<!\d)(\d{4})(?!\d)", s)   # 'first 4 digits', else a year inside '03/04/2016'
    y = int(m.group(1)) if m else 0
    return y if 1000 <= y <= 2100 else 0


def _folder_year(album_folder: str) -> int:
    """'Mythologie (2016)' → 2016: the library's album folders end in the year, for the files whose tags don't say."""
    m = re.search(r"\((\d{4})\)\s*$", album_folder)
    y = int(m.group(1)) if m else 0
    return y if 1000 <= y <= 2100 else 0


def _title_from_name(name: str) -> str:
    """'01 - Artist - Title.mp3' → 'Title' (the same rule as flask-api's bilby_local)."""
    stem = re.sub(r"^\d+[-_.\s]+", "", name.rsplit(".", 1)[0]).replace("_", " ").strip()
    for sep in (" - ", " – ", " — "):
        if sep in stem:
            return stem.split(sep, 1)[1].strip() or stem
    return stem or name


def read_tags(path: Path) -> dict | None:
    """Title, artist, album, track, disc, genre, year, seconds and whether there is cover art. None: unreadable, or an .m4a
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
             "n": _number(get("trkn")), "d": _number(get("disk")), "g": _first(get("\xa9gen")),
             "art": any(len(c) for c in get("covr") or []), "y": _year(get("\xa9day"))}
    else:
        get = lambda k: tags.get(k) if hasattr(tags, "get") else None                   # noqa: E731
        t = {"t": _first(get("TIT2")), "a": _first(get("TPE2")) or _first(get("TPE1")), "al": _first(get("TALB")),
             "n": _number(get("TRCK")), "d": _number(get("TPOS")), "g": _first(get("TCON")), "art": bool(_pictures(tags)),
             "y": _year(get("TDRC"))}              # recording time; mutagen has already turned a v2.3 TYER into it
    t["s"] = int(getattr(info, "length", 0) or 0)
    return t


def _pictures(tags) -> list:
    """An ID3 tag's pictures that hold any image data, the front cover first. (An APIC frame with no data happens: 93 of the real
    library's 21,451 tracks have one, and it must not count as a cover.)"""
    frames = [a for a in (tags.getall("APIC") if hasattr(tags, "getall") else []) if getattr(a, "data", b"")]
    return sorted(frames, key=lambda a: int(a.type) != 3)       # 3 = front cover; stable, so otherwise the file's order


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
        for c in tags.get("covr") or []:
            if len(c):
                return bytes(c), "image/png" if getattr(c, "imageformat", 13) == 14 else "image/jpeg"
        return None
    for apic in _pictures(tags):
        mime = apic.mime if apic.mime in ("image/jpeg", "image/png", "image/webp") else "image/jpeg"
        return bytes(apic.data), mime
    return None


class Library:
    def __init__(self, root: str | Path, state: str | Path, reader=read_tags, clock=time.time):
        self.root, self.reader, self.clock = Path(root), reader, clock
        self.state = Path(state)
        self.cache_path = self.state / "tags.json"
        self._lock = threading.Lock()
        self._cache: dict[str, list] = {}          # rel path → [size, mtime, tags or None]
        self.catalog_gz = b""
        self.etag = ""
        self.count = 0
        self.identities: tuple[tuple[str, str, str], ...] = ()   # (path, artist, title) of every catalog track: what the lyric lines are matched to
        self.on_build: list = []                    # called with .identities whenever the catalog is rebuilt (lyrics.py hangs here)
        self.ready = False                          # a full scan has finished
        self.scanning = False
        self.scanned = 0
        self.scanned_at = 0.0
        self._load_cache()

    # ── the tag cache ────────────────────────────────────────────────────
    def _load_cache(self) -> None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if data.get("v") in READABLE_VERSIONS and isinstance(data.get("files"), dict):
                self._cache = data["files"]
        except (OSError, ValueError):
            return
        self._build(self._cache)   # last run's catalog, playable while the new scan runs (a v1 one gets its years from folders)

    @staticmethod
    def _fresh(old: list | None, size: int, mtime: int) -> bool:
        """Is a cached entry still good: same size and mtime, and tags read by a version that knew the year? (An entry that
        failed to read, tags None, stays cached: the failure doesn't depend on the year.)"""
        return bool(old) and old[0] == size and old[1] == mtime and (old[2] is None or "y" in old[2])

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
                    if self._fresh(old, st.st_size, int(st.st_mtime)):
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
        for rel, (_size, mtime, tags) in sorted(files.items(), key=lambda kv: kv[0].lower()):
            if not tags:
                continue
            parts = rel.split("/")
            folder_artist = parts[0] if len(parts) >= 2 else ""
            folder_album = parts[1] if len(parts) >= 3 else ""
            tracks.append([rel, tags.get("t") or _title_from_name(parts[-1]),
                           folder_artist or tags.get("a") or "Unknown artist",
                           folder_album or tags.get("al") or "",
                           tags.get("n") or 0, tags.get("d") or 0, tags.get("g") or "", tags.get("s") or 0,
                           1 if tags.get("art") else 0,
                           tags.get("y") or _folder_year(folder_album),   # the tag's year, else the album folder's '(2016)'
                           int(mtime or 0)])                              # "date added": Syncthing keeps the source's mtime
        body = json.dumps({"v": 1, "fields": FIELDS, "tracks": tracks}, separators=(",", ":")).encode()
        gz = gzip.compress(body, 6, mtime=0)
        with self._lock:
            self.catalog_gz, self.count = gz, len(tracks)
            # A content hash, not hash(): that one is salted per process, so every restart would make each player download it again.
            self.etag = f'"{len(tracks)}-{hashlib.sha1(gz).hexdigest()[:12]}"'
            self.identities = tuple((t[0], t[2], t[1]) for t in tracks)
        for listener in list(self.on_build):
            try:
                listener(self.identities)
            except Exception:  # noqa: BLE001 — a listener's trouble never costs the catalog
                log.exception("music-gate: a catalog listener failed")

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
