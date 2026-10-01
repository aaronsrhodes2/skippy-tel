"""The one-sentence "what this song is about" lines, keyed by the catalog's own paths.

They come from Bilby's two stores on the Mac, which skippy-core's flask-api serves at GET /music/lyric-summaries
(`by`: one line per FILE, `lyrics_index.json`, keys already turned into library-relative paths like
`Artist/Album (2016)/Song.mp3`; `songs`: one line per SONG, `lyrics_dedup.json`, keyed "artist<TAB>title"). The gate fetches
them, keeps a copy in /state (flask-api being down must never cost the player its lines, or playback anything), and works out
which track each line belongs to, because the stores and the library have drifted apart. What the player downloads is
`GET /t/<pass>/lyrics.json`: `{"v": 1, "by": {"<catalog path>": "<summary>"}}`, gzipped, with an ETag.

Why two stores: the per-file one was written once from the Mac's Traktor collection and covers ~77 % of the library's tracks;
the per-song one, which the per-file one is derived from, is newer, and covers ~98 % of them, because a line is about the
SONG: every file of "Artist, Title" (another album, a compilation, a `_3` duplicate) can show it.

Matching, strictest first; a looser tier only fills tracks a stricter one left without a line:
  exact   the entry's path is a catalog path
  folded  the same path once Unicode form (NFC/NFD) and case are ignored, when only one catalog path fits
  song    the track's artist and title are the song's (title as the Mac's own base_title: without a trailing "(Radio Edit)"
          or "[Remaster]"), unless the title is a generic one ("Intro", "Track 13") that names a kind of track, not a song
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
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

log = logging.getLogger("music-gate")

DEFAULT_URL = "http://flask-api:5000/music/lyric-summaries"
REFRESH_S = 3 * 3600
MAX_BODY = 32 * 1024 * 1024        # what flask-api sends is ~5 MB; this only stops a runaway
MAX_SUMMARY = 400                  # the real ones top out near 160 characters

# LYRICS_MATCH: "path" = only the per-file store's entries; "song" (the default) = and the per-song store for the rest
LEVELS = ("path", "song")

# Titles that name a kind of track, not a song: two "Intro"s by one artist are different tracks.
GENERIC_TITLES = frozenset({
    "intro", "outro", "interlude", "prelude", "prologue", "epilogue", "reprise", "skit", "untitled", "instrumental",
    "bonus track", "hidden track", "silence", "track", "unknown", "live", "demo", "remix", "unknown title", "no title",
})
_NUMBERED = re.compile(r"^(?:track|disc|cd|part|side|pista|titel)\s*\d+$")     # "track 13", "cd 2"
_VERSION_SUFFIX = re.compile(r"\s*[\(\[].{0,40}[\)\]]\s*$")                      # the Mac's own rule (stage9_lyrics.base_title)


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _squash(text: str) -> str:
    return " ".join(fold(text).split())


def song_key(artist: str, title: str) -> tuple[str, str] | None:
    """The Mac's dedup identity of a track: (artist, title without a trailing parenthesis), compared without case, Unicode
    form or stray spaces. None when the title is too generic to say which song it is."""
    base = _squash(_VERSION_SUFFIX.sub("", title or "").strip())
    who = _squash(artist or "")
    if not who or len(base) < 2 or base in GENERIC_TITLES or _NUMBERED.match(base):
        return None
    return who, base


def match(by_path: dict[str, str], songs: dict[str, str], tracks, with_songs: bool = True) -> tuple[dict[str, str], dict[str, int]]:
    """{catalog path: line} for the tracks given as (path, artist, title), plus how many lines each tier placed."""
    tracks = list(tracks)
    exact = {t[0] for t in tracks}
    folded: dict[str, list[str]] = defaultdict(list)
    for path in exact:
        folded[fold(path)].append(path)

    stats = {"exact": 0, "folded": 0, "song": 0, "unmatched": 0, "superseded": 0}
    claims: dict[str, tuple[int, int, str]] = {}                  # catalog path → (tier, index order, line)
    for order, (key, text) in enumerate(by_path.items()):
        if key in exact:
            hit = key, 0
        else:
            c = folded.get(fold(key), ())
            hit = (c[0], 1) if len(c) == 1 else None             # two files differing only by case: can't tell which
        if hit is None:
            stats["unmatched"] += 1
            continue
        target, tier = hit
        if target in claims:                                      # two entries on one file: the stricter, then the earlier
            stats["superseded"] += 1
            if (tier, order) >= claims[target][:2]:
                continue
        claims[target] = (tier, order, text)
    by = {path: line for path, (_tier, _order, line) in claims.items()}
    for tier, name in ((0, "exact"), (1, "folded")):
        stats[name] = sum(1 for c in claims.values() if c[0] == tier)

    if with_songs and songs:
        keyed = {}
        for raw_key, text in songs.items():
            artist, _, title = raw_key.partition("\t")
            keyed.setdefault((_squash(artist), _squash(title)), text)   # already base titles: not stripped a second time
        for path, artist, title in tracks:
            if path in by:
                continue
            key = song_key(artist, title)
            if key and key in keyed:
                by[path] = keyed[key]
                stats["song"] += 1
    return dict(sorted(by.items())), stats


def _http_fetch(url: str, etag: str) -> tuple[int, str, bytes]:
    """(status, etag, body) from flask-api; a 304 comes back as (304, etag, b'')."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("LYRICS_URL must be an http(s) address")
    req = urllib.request.Request(url, headers={"Accept": "application/json", **({"If-None-Match": etag} if etag else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:        # noqa: S310 — scheme checked above, the URL is ours
            body = resp.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ValueError("lyric summaries: the answer is too big")
            return resp.status, resp.headers.get("ETag", ""), body
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, etag, b""
        raise


def _lines(value) -> dict[str, str]:
    out = {}
    if isinstance(value, dict):
        for key, text in value.items():
            if isinstance(key, str) and isinstance(text, str):
                text = " ".join(text.split())
                if key and text and len(text) <= MAX_SUMMARY:
                    out[key] = text
    return out


def clean(payload) -> tuple[dict[str, str], dict[str, str]] | None:
    """flask-api's answer → (per-file lines, per-song lines), or None when it isn't the shape we expect. `songs` is optional
    (an older flask-api sends only `by`)."""
    if not isinstance(payload, dict) or payload.get("v") != 1 or not isinstance(payload.get("by"), dict):
        return None
    return _lines(payload["by"]), _lines(payload.get("songs"))


class LyricsStore:
    def __init__(self, state: str | Path | None, url: str = DEFAULT_URL, with_songs: bool = True, fetch=_http_fetch,
                 clock=time.time):
        self.cache_path = Path(state) / "lyrics.json" if state else None
        self.url, self.with_songs, self.fetch, self.clock = (url or "").strip(), with_songs, fetch, clock
        self._lock = threading.Lock()
        self.by_path: dict[str, str] = {}          # as flask-api gave them
        self.songs: dict[str, str] = {}
        self.source_etag = ""
        self.fetched_at = 0.0
        self.tracks: tuple = ()                    # the catalog: (path, artist, title)
        self.stats: dict[str, int] = {}
        self.served: tuple[bytes, str] = (b"", "")  # (gzipped JSON, ETag): one tuple, so a reader never sees half an update
        self.count = 0
        self._load_cache()
        self._publish()

    # ── the copy in /state ───────────────────────────────────────────────
    def _load_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            got = clean({"v": data.get("v"), "by": data.get("by"), "songs": data.get("songs")})
        except (OSError, ValueError, AttributeError):
            return
        if got and (got[0] or got[1]):
            self.by_path, self.songs = got
            self.source_etag = str(data.get("etag") or "")
            self.fetched_at = float(data.get("fetched_at") or 0)

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": 1, "etag": self.source_etag, "fetched_at": self.fetched_at, "by": self.by_path,
                                       "songs": self.songs}, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError as exc:
            log.warning("music-gate: couldn't save the lyric summaries: %s", exc)

    # ── what is served ───────────────────────────────────────────────────
    def _publish(self) -> None:
        by, self.stats = match(self.by_path, self.songs, self.tracks, self.with_songs)
        body = json.dumps({"v": 1, "by": by}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        gz = gzip.compress(body, 6, mtime=0)
        self.count = len(by)
        self.served = (gz, f'"{len(by)}-{hashlib.sha1(gz).hexdigest()[:12]}"')

    def _summary(self) -> str:
        return f"{self.count} of {len(self.tracks)} tracks: " + ", ".join(f"{k} {v}" for k, v in self.stats.items())

    def rebuild(self, tracks) -> None:
        """The catalog changed (a scan finished): place the lines on its tracks again. `tracks`: (path, artist, title)."""
        with self._lock:
            self.tracks = tuple(tracks)
            self._publish()
        log.info("music-gate: lyric lines on %s", self._summary())

    # ── fetching ─────────────────────────────────────────────────────────
    def refresh(self) -> bool:
        """Ask flask-api for the lines. True when we now hold a good copy (new or confirmed unchanged); False leaves the old
        one in place."""
        if not self.url:
            return False
        try:
            status, etag, body = self.fetch(self.url, self.source_etag)
            if status == 304:
                with self._lock:
                    self.fetched_at = self.clock()
                return True
            got = clean(json.loads(body))
        except Exception as exc:  # noqa: BLE001 — flask-api down, mid-restart, or not what we expect: keep what we have
            log.warning("music-gate: lyric summaries unavailable (%s: %s); keeping the copy we have", type(exc).__name__,
                        str(exc)[:120])
            return False
        if not got or not (got[0] or got[1]):
            log.warning("music-gate: lyric summaries came back empty or malformed; keeping the copy we have")
            return False
        with self._lock:
            (self.by_path, self.songs), self.source_etag, self.fetched_at = got, etag, self.clock()
            self._publish()
            self._save_cache()
        log.info("music-gate: fetched %d per-file and %d per-song lyric lines; %s", len(got[0]), len(got[1]), self._summary())
        return True

    def run_forever(self, every_s: float = REFRESH_S) -> None:
        if not self.url:
            return
        fails = 0
        while True:
            try:
                ok = self.refresh()
            except Exception:  # noqa: BLE001
                log.exception("music-gate: lyric refresh failed")
                ok = False
            fails = 0 if ok else fails + 1
            # flask-api may simply not be up yet (both start together): look again soon, then settle to every 10 minutes
            time.sleep(every_s if ok else min(every_s, 30 * 2 ** min(fails - 1, 5), 600))
