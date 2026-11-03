"""
Bilby Local — music library scanner for the Syncthing-synced corrected_music folder.

Mounted read-only at /music inside the flask-api container:
  D:\\Aaron\\Music\\corrected_music  →  /music

Folder structure expected:
  /music/<Artist>/<Album>/<track.mp3>   (3-level, standard)
  /music/<Artist>/<track.mp3>           (2-level, loose tracks)

Exposes the same public API as bilby_drive.py so music.py can use either
without caring which backend is active:
  now_playing()  → Optional[dict]
  next_track()   → Optional[dict]
  suggest_next() → Optional[dict]
  search_track() → Optional[dict]
  playlist_for_prompt() → list[dict]
  queue_size()   → int
  invalidate()

Track dict schema (matches bilby_drive.py):
  {
    "file_id":  str,   # relative path from /music root  e.g. "Led Zeppelin/IV/Stairway.mp3"
    "name":     str,   # filename
    "title":    str,   # parsed from filename
    "artist":   str,   # from top-level folder name
    "album":    str,   # from second-level folder name (or "")
  }

stream_url is constructed by music.py as /music/stream/<file_id>.
The streaming endpoint checks /music/<file_id> on the filesystem and
sends the file with Range-request support (ExoPlayer seeking).

Quarantine holds: no imports from zPhone / SkippyDroid / SkippyGlassesMac.
"""

import json
import logging
import os
import random
import time
import threading
from pathlib import Path
from typing import Optional

from app.offload import off_loop

log = logging.getLogger(__name__)

_MUSIC_ROOT = Path("/music")
_AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".ogg", ".wav", ".aac", ".opus", ".wma"}

_lock:         threading.Lock = threading.Lock()
_queue:        list[dict]     = []
_index:        int            = 0
_now:          Optional[dict] = None
_loaded:       bool           = False
_scan_started: bool           = False
_scan_lock:    threading.Lock = threading.Lock()   # one scan at a time; the callers that arrive meanwhile wait here for its answer

# The scan result is kept in a file, so a restart (a deploy, the doctor, gunicorn recycling a worker) reads three megabytes instead of
# walking 21k files over the Windows share, which saturated it for a minute (/health took seconds meanwhile). It is a legacy index (the
# phone-era /music/session and Bilby DJ intents; Bilby's Audio Service has its own catalog in music-gate), so a day-old one is fine.
_INDEX_FILE     = Path("/app/state/bilby_library_index.json")
_INDEX_MAX_AGE_S = 12 * 3600


# ── Public API ────────────────────────────────────────────────────────────────

def available() -> bool:
    """True if the /music mount point exists and contains audio files."""
    return _MUSIC_ROOT.exists()


def ready() -> bool:
    """True once the background scan has finished."""
    with _lock:
        return _loaded and bool(_queue)


def now_playing() -> Optional[dict]:
    """Return the current track dict, or None if still scanning."""
    with _lock:
        if _now:
            return dict(_now)
        if not _loaded:
            # Background scan not done yet — return None rather than blocking
            return None
    # Loaded but _now is None (empty library)
    return None


def next_track() -> Optional[dict]:
    """Advance to the next track in the shuffled queue and return it."""
    _ensure_loaded()
    with _lock:
        global _index, _now
        if not _queue:
            return None
        _index = (_index + 1) % len(_queue)
        _now   = _queue[_index]
        log.info("Bilby Local → %s — %s", _now.get("artist"), _now.get("title"))
        return dict(_now)


def suggest_next(
    current_file_id: Optional[str] = None,
    current_artist: Optional[str]  = None,
) -> Optional[dict]:
    """Pick the next DJ 'column 2' track.

    70 % same artist → keeps the vibe.
    30 % random from library → keeps things fresh.
    Never returns the current track.
    """
    _ensure_loaded()
    with _lock:
        pool = [t for t in _queue if t.get("file_id") != current_file_id]
        if not pool:
            # Only one track in library — return it anyway rather than None
            return dict(_queue[0]) if _queue else None

        same_artist: list[dict] = []
        if current_artist:
            ca = current_artist.strip().lower()
            same_artist = [
                t for t in pool
                if t.get("artist", "").strip().lower() == ca
            ]

        if same_artist and random.random() < 0.70:
            chosen = random.choice(same_artist)
        else:
            chosen = random.choice(pool)

        log.info(
            "Bilby Local suggest → %s — %s  (pool %d same-artist %d)",
            chosen.get("artist"), chosen.get("title"),
            len(pool), len(same_artist),
        )
        return dict(chosen)


def search_track(artist: str, title: str) -> Optional[dict]:
    """Fuzzy search for a track by artist + title."""
    _ensure_loaded()
    needle_artist = artist.strip().lower()
    needle_title  = title.strip().lower()

    with _lock:
        best_score, best_track = 0, None
        for t in _queue:
            ta = t.get("artist", "").strip().lower()
            tt = t.get("title",  "").strip().lower()
            score = 0
            if needle_artist and needle_artist in ta:   score += 2
            if needle_title  and needle_title  in tt:   score += 2
            if any(w in ta for w in needle_artist.split() if len(w) > 2): score += 1
            if any(w in tt for w in needle_title.split()  if len(w) > 2): score += 1
            if score > best_score:
                best_score, best_track = score, t

        if best_track and best_score >= 2:
            log.info("Bilby Local search '%s / %s' → %s — %s (score %d)",
                     artist, title,
                     best_track.get("artist"), best_track.get("title"), best_score)
            return dict(best_track)
        return None


def playlist_for_prompt(prompt: str, count: int = 10) -> list[dict]:
    """Use Ollama to select tracks from the local library matching a text description.

    Gets unique artist list → asks Ollama which match the vibe → filters library
    → returns up to `count` shuffled tracks from those artists.
    Falls back to random shuffle if Ollama is unreachable.
    """
    import requests as _req
    from app.config import settings

    _ensure_loaded()

    with _lock:
        if not _queue:
            return []
        artists = sorted({t.get("artist", "Unknown") for t in _queue if t.get("artist")})

    matched_artists: list[str] = []
    try:
        # Cap the artist list to avoid overwhelming the context window
        artist_block = "\n".join(f"- {a}" for a in artists[:400])
        system = (
            "You are a music selector. Given a list of artists and a music description, "
            "return ONLY the names of artists from that list that best match the description. "
            "Output exactly one artist name per line. No bullets, no numbers, no explanation. "
            "Return at least 5 names if possible, up to 20."
        )
        user_msg = (
            f"Music description: {prompt}\n\n"
            f"Artist list:\n{artist_block}\n\n"
            "Which artists best fit the description? Return only artist names, one per line."
        )
        resp = _req.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model":    settings.ollama_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user_msg},
                ],
                "stream": False,
            },
            timeout=20,
        )
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "")
        returned = {line.strip().lower() for line in raw.splitlines() if line.strip()}
        artist_lower_map = {a.lower(): a for a in artists}
        matched_artists = [artist_lower_map[r] for r in returned if r in artist_lower_map]
        log.info("Bilby Local playlist '%s': Ollama matched %d artists", prompt, len(matched_artists))
    except Exception as exc:
        log.warning("Bilby Local playlist: Ollama call failed (%s), using random", exc)

    with _lock:
        if matched_artists:
            matched_set = {a.lower() for a in matched_artists}
            pool = [t for t in _queue if t.get("artist", "").lower() in matched_set]
        else:
            pool = list(_queue)

    if not pool:
        with _lock:
            pool = list(_queue)

    random.shuffle(pool)
    result = pool[:count]
    log.info("Bilby Local playlist '%s': %d tracks from %d candidates", prompt, len(result), len(pool))
    return [dict(t) for t in result]


def queue_size() -> int:
    with _lock:
        return len(_queue)


def invalidate():
    """Force a re-scan on next access (the saved index goes too: it would just be read back)."""
    global _loaded, _queue, _now
    with _lock:
        _loaded = False
        _queue  = []
        _now    = None
    try:
        _INDEX_FILE.unlink()
    except OSError:
        pass


# ── Internal ──────────────────────────────────────────────────────────────────

def _ensure_loaded():
    global _loaded, _queue, _index, _now
    with _lock:
        if _loaded and _queue:
            return
    with _scan_lock:                       # one scan, however many callers: the others wait here, not in a scan of their own
        with _lock:
            if _loaded and _queue:
                return
        # In a real thread (app/offload.py): this app runs on gevent workers, where a scan of the whole library ON the event loop froze
        # the worker for minutes (every stat is a round trip to the Windows file share), /health with it: gunicorn killed the worker at
        # 120 s, the new one scanned again, and the doctor DMed the Captain "Skippy's API is still failing after 3 tries" (2026-11-02).
        tracks = off_loop(_load_tracks)
        with _lock:
            if tracks:
                random.shuffle(tracks)
                _queue  = tracks
                _index  = 0
                _now    = tracks[0]
                _loaded = True
                log.info("Bilby Local: scanned %d tracks from %s", len(tracks), _MUSIC_ROOT)
            else:
                _loaded = True
                log.warning("Bilby Local: no audio files found under %s", _MUSIC_ROOT)


def _read_index() -> Optional[list[dict]]:
    """The saved scan, when it is for this library and young enough; None otherwise."""
    try:
        if time.time() - _INDEX_FILE.stat().st_mtime > _INDEX_MAX_AGE_S:
            return None
        data = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
        tracks = data.get("tracks") if isinstance(data, dict) and data.get("root") == str(_MUSIC_ROOT) else None
        if isinstance(tracks, list) and tracks and all(isinstance(t, dict) and "file_id" in t for t in tracks[:50]):
            return tracks
    except (OSError, ValueError):
        pass
    return None


def _write_index(tracks: list[dict]) -> None:
    """Save the scan (best effort, a file that is never half written: the new one replaces the old in one step)."""
    try:
        tmp = _INDEX_FILE.with_name(_INDEX_FILE.name + ".tmp")
        tmp.write_text(json.dumps({"root": str(_MUSIC_ROOT), "tracks": tracks}, separators=(",", ":")), encoding="utf-8")
        tmp.replace(_INDEX_FILE)
    except OSError:
        pass


def _load_tracks() -> list[dict]:
    """In the scan's thread: the saved index when it is fresh enough, else a walk of the library (which is then saved)."""
    kept = _read_index()
    if kept:
        return kept
    tracks = _scan_library()
    if tracks:
        _write_index(tracks)
    return tracks


def _scan_library() -> list[dict]:
    """Walk /music three levels deep: Artist → Album → track files.

    os.scandir, not Path.iterdir() + is_dir()/is_file(): a DirEntry knows its type from the directory listing itself, so the walk
    costs one listing per folder. The old walk made a stat() per file, and on the Windows bind mount each stat is a round trip to
    the host (the whole library took minutes). Runs in a thread of its own (see _ensure_loaded): it logs nothing from here."""
    if not _MUSIC_ROOT.exists():
        return []

    tracks: list[dict] = []

    def entries(folder: Path):
        try:
            with os.scandir(folder) as it:
                return sorted(it, key=lambda e: e.name)       # (a stable order: what "random.shuffle" starts from does not change what is found)
        except OSError:
            return []

    for artist_entry in entries(_MUSIC_ROOT):
        artist_path = Path(artist_entry.path)
        if not artist_entry.is_dir():
            # Loose files at root level
            if artist_path.suffix.lower() in _AUDIO_EXTS:
                title, artist = _parse_filename(artist_entry.name)
                tracks.append(_make_track(artist_path, title, artist, ""))
            continue

        artist_name = artist_entry.name
        for child in entries(artist_path):
            child_path = Path(child.path)
            if not child.is_dir():
                # Artist-level loose file
                if child_path.suffix.lower() in _AUDIO_EXTS:
                    title, _ = _parse_filename(child.name)
                    tracks.append(_make_track(child_path, title, artist_name, ""))
                continue

            album_name = child.name
            for track_file in entries(child_path):
                if track_file.is_file() and Path(track_file.name).suffix.lower() in _AUDIO_EXTS:
                    title, _ = _parse_filename(track_file.name)
                    tracks.append(_make_track(Path(track_file.path), title, artist_name, album_name))

    return tracks


def _make_track(path: Path, title: str, artist: str, album: str) -> dict:
    rel = path.relative_to(_MUSIC_ROOT)
    return {
        "file_id": str(rel).replace("\\", "/"),   # forward slashes for URL safety
        "name":    path.name,
        "title":   title,
        "artist":  artist,
        "album":   album,
    }


def _parse_filename(filename: str) -> tuple[str, str]:
    """Parse title and artist from a music filename.

    Handles:
      "Artist - Title.mp3"       → ("Title", "Artist")
      "01 - Artist - Title.mp3"  → ("Title", "Artist")
      "Title.mp3"                → ("Title", "Unknown")
    """
    import re
    stem = filename.rsplit(".", 1)[0]
    stem = re.sub(r"^\d+[-_.\s]+", "", stem)
    stem = stem.replace("_", " ").strip()
    for sep in (" - ", " – ", " — "):
        if sep in stem:
            parts = stem.split(sep, 1)
            return parts[1].strip(), parts[0].strip()
    return stem or filename, "Unknown"


# ── Background startup scan ───────────────────────────────────────────────────
# WSL2 bind-mounts are slow. Start the scan at import time so it is ready before the first HTTP request arrives. Under gevent this
# "thread" is a greenlet: it only WAITS for the real thread _ensure_loaded starts for the walk (app/offload.py), so the worker serves
# requests meanwhile. Defined down here so _ensure_loaded is already in scope.

def _background_scan() -> None:
    global _scan_started
    if not _MUSIC_ROOT.exists():
        return
    with _lock:
        if _scan_started:
            return
        _scan_started = True
    _ensure_loaded()   # blocks only the background thread


threading.Thread(target=_background_scan, daemon=True, name="bilby-local-scan").start()
