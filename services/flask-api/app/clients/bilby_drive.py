"""
Bilby Drive client — random track selection from Google Drive Music/ folder.

Used when the Mac is offline and source=drive is requested.
Derives metadata (artist/album) from the Drive folder hierarchy:
  Music/ → Artist/ → Album/ → track.mp3
Title comes from filename parsing.

State machine:
  _queue    — shuffled list of track dicts, pre-fetched from Drive
  _index    — current position in queue
  _now      — currently "playing" track (returned by now_playing())

The queue is refreshed when empty or explicitly invalidated. Track
metadata is cached in memory for the process lifetime.

Threading note:
  Credentials are loaded once and shared (with a lock for refresh).
  Each worker calls _build_svc() which uses static_discovery=True so
  NO network call is made to googleapis.com/discovery — the bundled
  local schema is used instead. This prevents the thundering-herd of
  hundreds of simultaneous discovery requests that crashed Docker Desktop.
  max_workers is capped at 4 to limit concurrent Drive API calls.
"""

import logging
import random
import threading
from typing import Optional

# ── Shared credential cache (load once, refresh with lock) ───────────────────
_creds_lock  = threading.Lock()
_shared_creds = None   # type: ignore   # google.oauth2.credentials.Credentials | None

log = logging.getLogger(__name__)

_lock    = threading.Lock()
_queue:   list[dict] = []
_index:   int        = 0
_now:     Optional[dict] = None
_loaded:  bool       = False   # True once the first Drive fetch completes

_AUDIO_EXTS = (".mp3", ".m4a", ".flac", ".ogg", ".wav", ".aac", ".opus", ".wma")
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]  # token was issued with full drive scope


# ── Public API ────────────────────────────────────────────────────────────────

def now_playing() -> Optional[dict]:
    """Return the current track dict {"title":…,"artist":…,"album":…,"file_id":…} or None."""
    with _lock:
        if _now:
            return dict(_now)
    _ensure_loaded()
    with _lock:
        return dict(_now) if _now else None


def next_track() -> Optional[dict]:
    """Advance to the next track in the queue and return it."""
    _ensure_loaded()
    with _lock:
        global _index, _now
        if not _queue:
            return None
        _index = (_index + 1) % len(_queue)
        _now = _queue[_index]
        log.info("Bilby Drive → %s — %s", _now.get("artist"), _now.get("title"))
        return dict(_now)


def queue_size() -> int:
    with _lock:
        return len(_queue)


def invalidate():
    """Force a re-fetch on next access (call after new files are uploaded)."""
    global _loaded, _queue, _now
    with _lock:
        _loaded = False
        _queue  = []
        _now    = None


def suggest_next(
    current_file_id: Optional[str] = None,
    current_artist: Optional[str] = None,
) -> Optional[dict]:
    """Pick the next track for Bilby DJ 'column 2' mode.

    Selection priority:
      70 %  — same artist as current (keeps the vibe consistent)
      30 %  — random from the entire library (keeps things fresh)
    Never returns the current track itself.
    Falls back to pure random if same-artist pool is empty.
    """
    import random as _random
    _ensure_loaded()
    with _lock:
        pool = [t for t in _queue if t.get("file_id") != current_file_id]
        if not pool:
            return None

        same_artist: list[dict] = []
        if current_artist:
            ca = current_artist.strip().lower()
            same_artist = [
                t for t in pool
                if t.get("artist", "").strip().lower() == ca
            ]

        # Weighted pick: 70 % same-artist if available, else pure random.
        if same_artist and _random.random() < 0.70:
            chosen = _random.choice(same_artist)
        else:
            chosen = _random.choice(pool)

        log.info(
            "Bilby DJ suggest → %s — %s  (pool %d same-artist %d)",
            chosen.get("artist"), chosen.get("title"),
            len(pool), len(same_artist),
        )
        return dict(chosen)


def search_track(artist: str, title: str) -> Optional[dict]:
    """Find a specific track by artist + title fuzzy match.

    Checks artist folder name AND title from filename.
    Returns the best match or None if not found.
    """
    _ensure_loaded()
    needle_artist = artist.strip().lower()
    needle_title  = title.strip().lower()

    with _lock:
        candidates: list[tuple[int, dict]] = []
        for t in _queue:
            ta = t.get("artist", "").strip().lower()
            tt = t.get("title",  "").strip().lower()
            score = 0
            if needle_artist and needle_artist in ta:
                score += 2
            if needle_title and needle_title in tt:
                score += 2
            # Partial word matches
            if any(w in ta for w in needle_artist.split() if len(w) > 2):
                score += 1
            if any(w in tt for w in needle_title.split() if len(w) > 2):
                score += 1
            if score >= 2:
                candidates.append((score, t))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        chosen = candidates[0][1]
        log.info("Bilby search '%s / %s' → %s — %s (score %d)",
                 artist, title, chosen.get("artist"), chosen.get("title"), candidates[0][0])
        return dict(chosen)


def playlist_for_prompt(prompt: str, count: int = 10) -> list[dict]:
    """Generate a playlist from the Drive library matching a text description.

    Uses Ollama to semantically match the prompt against available artists,
    then picks tracks from those artists. This ensures we only return tracks
    that actually exist in the library (no hallucinated song titles).

    Falls back to a random shuffle if Ollama is unreachable.
    """
    import random as _random
    from app.config import settings

    _ensure_loaded()

    with _lock:
        if not _queue:
            return []
        # Build artist list (deduplicated, sorted)
        artists = sorted({t.get("artist", "Unknown") for t in _queue if t.get("artist")})

    if not artists:
        return []

    # ── Ask Ollama which artists match the prompt ─────────────────────────
    matched_artists: list[str] = []
    try:
        import requests as _req
        artist_block = "\n".join(f"- {a}" for a in artists[:300])  # cap list size
        system = (
            "You are a music selector. Given a list of artists and a music description, "
            "return ONLY the names of artists that best match the description. "
            "Output exactly one artist name per line, no bullets, no numbers, no explanation. "
            "Return at least 5 names if possible."
        )
        user_msg = (
            f"Music description: {prompt}\n\n"
            f"Artist list:\n{artist_block}\n\n"
            "Which artists from this list best fit the description? "
            "Return only artist names, one per line."
        )
        resp = _req.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model":    settings.ollama_model,
                "messages": [
                    {"role": "system",  "content": system},
                    {"role": "user",    "content": user_msg},
                ],
                "stream": False,
            },
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "")
        # Parse returned names — match against actual artist list (case-insensitive)
        returned = {line.strip().lower() for line in raw.splitlines() if line.strip()}
        artist_lower_map = {a.lower(): a for a in artists}
        matched_artists = [
            artist_lower_map[r]
            for r in returned
            if r in artist_lower_map
        ]
        log.info("Bilby playlist '%s': Ollama matched %d artists", prompt, len(matched_artists))
    except Exception as exc:
        log.warning("Bilby playlist: Ollama unreachable (%s), falling back to random", exc)

    # ── Build track pool from matched artists (or full library on fallback) ──
    with _lock:
        if matched_artists:
            matched_set = {a.lower() for a in matched_artists}
            pool = [
                t for t in _queue
                if t.get("artist", "").lower() in matched_set
            ]
        else:
            pool = list(_queue)

    if not pool:
        with _lock:
            pool = list(_queue)

    _random.shuffle(pool)
    selected = pool[:count]
    log.info("Bilby playlist '%s': returning %d tracks from %d candidates",
             prompt, len(selected), len(pool))
    return [dict(t) for t in selected]


# ── Internal ──────────────────────────────────────────────────────────────────

def _ensure_loaded():
    global _loaded, _queue, _index, _now
    with _lock:
        if _loaded and _queue:
            return
    # Fetch outside the lock to avoid blocking other requests for 10+ seconds.
    tracks = _fetch_tracks()
    with _lock:
        if tracks:
            random.shuffle(tracks)
            _queue  = tracks
            _index  = 0
            _now    = tracks[0]
            _loaded = True
            log.info("Bilby Drive: loaded %d tracks", len(tracks))
        else:
            _loaded = True   # don't retry every request on empty library
            log.warning("Bilby Drive: no tracks found in Music/ folder")


_TOKEN_FILE = "/drive-state/drive_token.json"   # bind-mounted from music-collection/state/


def _get_shared_creds():
    """Return a valid shared Credentials object.

    Credentials are loaded from disk once and refreshed under a lock so
    multiple threads don't race to hit oauth2.googleapis.com simultaneously.
    """
    global _shared_creds
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    with _creds_lock:
        if _shared_creds is None:
            _shared_creds = Credentials.from_authorized_user_file(
                _TOKEN_FILE, _DRIVE_SCOPES
            )
        if _shared_creds.expired and _shared_creds.refresh_token:
            log.info("Bilby Drive: refreshing OAuth token")
            _shared_creds.refresh(Request())
    return _shared_creds


def _build_svc():
    """Build a Drive service for the calling thread.

    Uses static_discovery=True so the bundled local API schema is used —
    NO network call to googleapis.com/discovery. This eliminates the
    thundering-herd of hundreds of simultaneous discovery requests.

    Credentials are shared (see _get_shared_creds); each call creates a
    new authorized HTTP transport so threads don't share connection state.
    """
    from googleapiclient.discovery import build

    creds = _get_shared_creds()
    # static_discovery=True: use the bundled local Drive v3 schema (no HTTP)
    return build("drive", "v3", credentials=creds, static_discovery=True)


def _fetch_tracks() -> list[dict]:
    """
    Walk the Music/ folder 3 levels deep (Artist → Album → files) using
    parallel per-thread Drive service instances.

    Each ThreadPoolExecutor worker calls _build_svc() to get its own
    service — this avoids SSL record-layer failures from sharing a single
    httplib2-backed service across threads.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.config import settings

    folder_id = settings.bilby_drive_folder_id
    if not folder_id:
        return []

    try:
        svc = _build_svc()

        # ── Level 0: list Music/ root ─────────────────────────────────────
        root_files, artist_folders = _list_folder_with_svc(svc, folder_id)
        tracks = list(root_files)
        log.info("Bilby Drive L0: %d files, %d artist folders", len(root_files), len(artist_folders))

        if not artist_folders:
            return tracks

        # ── Level 1: artist folders → album folders (parallel) ────────────
        album_folders: list[tuple[str, str]] = []  # (folder_id, artist_name)

        def _list_artist(af):
            s = _build_svc()
            files, subs = _list_folder_with_svc(s, af["id"])
            return files, subs, af["name"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_list_artist, af): af for af in artist_folders}
            for fut in as_completed(futures):
                try:
                    files, subfolders, artist_name = fut.result()
                    for t in files:
                        t["artist"] = artist_name
                    tracks.extend(files)
                    for sf in subfolders:
                        album_folders.append((sf["id"], artist_name))
                except Exception as exc:
                    af = futures[fut]
                    log.warning("Bilby Drive L1 error for '%s': %s", af.get("name"), exc)

        log.info("Bilby Drive L1: %d tracks, %d album folders pending", len(tracks), len(album_folders))

        if not album_folders:
            return tracks

        # ── Level 2: album folders → track files (parallel) ───────────────
        def _list_album(album_id, artist_name):
            s = _build_svc()
            files, _ = _list_folder_with_svc(s, album_id)
            return files, artist_name

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures2 = {
                pool.submit(_list_album, aid, art): (aid, art)
                for aid, art in album_folders
            }
            for fut in as_completed(futures2):
                aid, art = futures2[fut]
                try:
                    files, artist_name = fut.result()
                    for t in files:
                        t["artist"] = artist_name
                    tracks.extend(files)
                except Exception as exc:
                    log.warning("Bilby Drive L2 error: %s", exc)

        log.info("Bilby Drive: %d total tracks found", len(tracks))
        return tracks

    except Exception as exc:
        log.error("Bilby Drive fetch error: %s", exc)
        return []


def _list_folder_with_svc(svc, folder_id: str) -> tuple[list[dict], list[dict]]:
    """List direct children of a folder using the provided service instance.

    Returns (audio_tracks, subfolders) where:
      audio_tracks — list of track dicts (file_id, name, title, artist="", album="")
      subfolders   — list of {"id": ..., "name": ...}
    """
    audio_tracks: list[dict] = []
    subfolders:   list[dict] = []
    page_token = None

    while True:
        resp = svc.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            pageSize=500,
            pageToken=page_token,
            fields="nextPageToken, files(id, name, mimeType)",
        ).execute()

        for f in resp.get("files", []):
            name = f.get("name", "")
            mime = f.get("mimeType", "")
            if mime == "application/vnd.google-apps.folder":
                subfolders.append({"id": f["id"], "name": name})
            elif mime.startswith("audio/") or any(
                name.lower().endswith(ext) for ext in _AUDIO_EXTS
            ):
                title, artist = _parse_filename(name)
                audio_tracks.append({
                    "file_id": f["id"],
                    "name":    name,
                    "title":   title,
                    "artist":  artist,   # overwritten by caller with folder name
                    "album":   "",
                })

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return audio_tracks, subfolders


def _parse_filename(filename: str) -> tuple[str, str]:
    """Parse title and artist from a music filename.

    Handles common patterns:
      "Artist - Title.mp3"        → ("Title", "Artist")
      "01 - Artist - Title.mp3"   → ("Title", "Artist")
      "Title.mp3"                 → ("Title", "Unknown")

    Returns (title, artist).
    """
    import re
    stem = filename.rsplit(".", 1)[0]              # strip extension
    stem = re.sub(r"^\d+[-_.\s]+", "", stem)       # strip leading track number
    stem = stem.replace("_", " ").strip()

    # Try "Artist - Title" or "Artist – Title" split
    for sep in (" - ", " – ", " — "):
        if sep in stem:
            parts = stem.split(sep, 1)
            return parts[1].strip(), parts[0].strip()

    return stem or filename, "Unknown"
