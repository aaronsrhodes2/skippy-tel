"""
GET /music/lyric-summaries — one sentence per song about what it is about, for Bilby's Audio Service (the music-gate
fetches this over the compose network and serves it to the browser player; nothing else calls it).

The data is Bilby's two lyrics stores, written on the Mac by a local model and synced into music-collection/state, which
this container mounts as /drive-state:

  lyrics_index.json   one entry per FILE (~19k), keyed by MAC paths such as
                      /:Users/aaronrhodes/development/music organize/corrected_music/Delerium feat. Phildel/Mythologie (2016)/Zero.m4a
  lyrics_dedup.json   one entry per SONG (~20k), keyed "artist<TAB>title" in the Mac's own dedup form (lower case, without a
                      trailing "(Radio Edit)"): the store the per-file one is derived from, and newer. It covers far more of
                      the library: a song's line fits every file of it, wherever the file lives.

This route does the part that needs no library. For the per-file store it takes everything after `corrected_music/` (the
library-relative path the catalog uses: `Delerium feat. Phildel/Mythologie (2016)/Zero.m4a`), straightens it (slashes, Unicode
form) and leaves out what the player can never use or must not show:

  * entries outside `corrected_music/` (the Mac's Traktor factory sounds, Sina True's folders...) and files that aren't
    MP3/AAC (the index also covers FLAC and OGG, which the player doesn't play);
  * `flags` (the Mac's content warnings) and `theme`: only the summary is returned;
  * summaries that are garbled: the local model sometimes slipped into Chinese mid-sentence (73 of 18,943 in the real file).

Matching the lines to the tracks that really exist (the stores and the library have drifted: duplicate-file suffixes, files that
moved albums, case and Unicode differences) is the gate's job, because it holds the catalog (services/music-gate/lyrics.py).

    {"spec_version": "1", "v": 1, "n": 18488, "n_songs": 20014, "dropped": {...}, "by": {"<rel path>": "<summary>"},
     "songs": {"<artist>\\t<title>": "<summary>"}}

Either store may be missing (the Mac hasn't synced yet): its part comes back empty. Neither there is a 503, and so is a store
that is half-synced the first time; a good copy already read is kept and served meanwhile. ETag/304, so the gate's refresh
costs nothing when nothing changed.
"""
import hashlib
import json
import logging
import os
import re
import threading
import unicodedata
from pathlib import Path

from flask import Blueprint, Response, request

from app.version import SPEC_VERSION, error

log = logging.getLogger(__name__)
bp = Blueprint("lyric_summaries", __name__)

_INDEX_PATH = Path(os.environ.get("LYRICS_INDEX_PATH", "/drive-state/lyrics_index.json"))
_DEDUP_PATH = Path(os.environ.get("LYRICS_DEDUP_PATH", "/drive-state/lyrics_dedup.json"))
_ANCHOR = re.compile(r"(?:^|/)corrected_music/(.+)$", re.IGNORECASE)
_PLAYABLE = (".mp3", ".m4a", ".aac")
# CJK, Hangul, fullwidth forms and CJK punctuation: every real summary is English, so one of these means the model drifted.
_FOREIGN = re.compile(r"[　-〿぀-ヿ㐀-䶿一-鿿가-힯＀-￯]")
_MAX_SUMMARY = 400                      # the real ones top out at 163 characters

_lock = threading.Lock()


class Unavailable(Exception):
    pass


def classify(key) -> tuple[str | None, str]:
    """A Mac index key → (library-relative path, "") or (None, why it is left out)."""
    if not isinstance(key, str) or "\x00" in key:
        return None, "bad_path"
    m = _ANCHOR.search(key.replace("\\", "/"))
    if not m:
        return None, "outside_library"
    parts = [p for p in unicodedata.normalize("NFC", m.group(1)).split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        return None, "bad_path"
    rel = "/".join(parts)
    if not rel.lower().endswith(_PLAYABLE):
        return None, "not_playable"
    return rel, ""


def clean_summary(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())            # one line, single spaces
    if not text or len(text) > _MAX_SUMMARY or _FOREIGN.search(text):
        return None
    return text


def build(index: dict) -> tuple[dict[str, str], dict[str, int]]:
    """The per-file store → ({library path: summary}, how many entries were left out for each reason)."""
    by: dict[str, str] = {}
    dropped = {"outside_library": 0, "not_playable": 0, "bad_path": 0, "bad_summary": 0, "duplicate": 0}
    for key, entry in index.items():
        rel, why = classify(key)
        if rel is None:
            dropped[why] += 1
            continue
        text = clean_summary(entry.get("summary") if isinstance(entry, dict) else None)
        if text is None:
            dropped["bad_summary"] += 1
        elif rel in by:                       # two keys that straighten to one path (NFC/NFD twins): the first stays
            dropped["duplicate"] += 1
        else:
            by[rel] = text
    return by, dropped


def build_songs(dedup: dict) -> tuple[dict[str, str], dict[str, int]]:
    """The per-song store → ({"artist<TAB>title": summary}, how many were left out). Keys stay in the Mac's form (the gate
    compares them the way the Mac does); only Unicode form is made one."""
    songs: dict[str, str] = {}
    dropped = {"bad_key": 0, "bad_summary": 0, "duplicate": 0}
    for key, entry in dedup.items():
        artist, tab, title = key.partition("\t") if isinstance(key, str) else ("", "", "")
        if not tab or not artist.strip() or not title.strip():
            dropped["bad_key"] += 1
            continue
        text = clean_summary(entry.get("summary") if isinstance(entry, dict) else None)
        if text is None:
            dropped["bad_summary"] += 1
            continue
        norm = unicodedata.normalize("NFC", key)
        if norm in songs:
            dropped["duplicate"] += 1
        else:
            songs[norm] = text
    return songs, dropped


class _Store:
    """One of the two files: read when it changes, and the last good read kept for when it is being rewritten or gone."""

    def __init__(self, path, builder):
        self.path, self.builder = path, builder      # path: a callable, so a test can point the module at another file
        self.sig, self.built = None, None

    def current(self):
        """(signature, built) of the file as it is now; the last good read if it can't be read; None if there never was one."""
        path = self.path()
        try:
            st = path.stat()
        except OSError:
            return (self.sig, self.built) if self.built is not None else None
        sig = (str(path), st.st_mtime_ns, st.st_size)
        if sig == self.sig:
            return sig, self.built
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as exc:         # being rewritten by Syncthing, or damaged: a good read is kept
            log.warning("lyric-summaries: %s couldn't be read (%s)", path.name, exc)
            return (self.sig, self.built) if self.built is not None else None
        self.sig, self.built = sig, self.builder(data)
        log.info("lyric-summaries: %s → %d lines (left out: %s)", path.name, len(self.built[0]), self.built[1])
        return self.sig, self.built


_files = _Store(lambda: _INDEX_PATH, build)
_songs = _Store(lambda: _DEDUP_PATH, build_songs)
_good: dict = {"sig": None, "body": b"", "etag": ""}   # the answer for the two files' current signatures


def _prepared() -> tuple[bytes, str]:
    """(JSON body, ETag) for the stores as they are now; built again only when one of the files changed."""
    with _lock:
        files, songs = _files.current(), _songs.current()
        if files is None and songs is None:
            raise Unavailable("the lyrics stores aren't there")
        sig = (files[0] if files else None, songs[0] if songs else None)
        if sig == _good["sig"]:
            return _good["body"], _good["etag"]
        by, dropped = files[1] if files else ({}, {})
        lines, songs_dropped = songs[1] if songs else ({}, {})
        body = json.dumps({"spec_version": SPEC_VERSION, "v": 1, "n": len(by), "n_songs": len(lines), "dropped": dropped,
                           "songs_dropped": songs_dropped, "by": by, "songs": lines},
                          separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        _good.update(sig=sig, body=body, etag=hashlib.sha1(body).hexdigest()[:16])
        return _good["body"], _good["etag"]


@bp.get("/music/lyric-summaries")
def lyric_summaries():
    try:
        body, etag = _prepared()
    except Unavailable as exc:
        return error(str(exc), "lyrics_unavailable", 503)
    resp = Response(body, mimetype="application/json", headers={"Cache-Control": "no-cache"})
    resp.set_etag(etag)
    return resp.make_conditional(request)
