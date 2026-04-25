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
  The google-api-python-client service (httplib2 backend) is NOT thread-safe.
  Each worker thread creates its own service via _build_svc() so parallel
  folder listings don't corrupt shared HTTP state.
"""

import logging
import random
import threading
from typing import Optional

log = logging.getLogger(__name__)

_lock    = threading.Lock()
_queue:   list[dict] = []
_index:   int        = 0
_now:     Optional[dict] = None
_loaded:  bool       = False   # True once the first Drive fetch completes

_AUDIO_EXTS = (".mp3", ".m4a", ".flac", ".ogg", ".wav", ".aac", ".opus", ".wma")
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


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


def _build_svc():
    """Build a fresh (non-cached) Drive service for the calling thread.

    Each parallel worker calls this so every thread has its own httplib2
    Http instance — shared services cause SSL record-layer failures.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(_TOKEN_FILE, _DRIVE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("drive", "v3", credentials=creds)


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

        with ThreadPoolExecutor(max_workers=16) as pool:
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

        with ThreadPoolExecutor(max_workers=16) as pool:
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
