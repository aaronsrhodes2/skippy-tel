"""
SkippyTel music session — /music/* endpoints

Manages a single active music session. Three modes:

  dj       — Bilby autonomous DJ. Picks first track, then uses suggest_next()
              (same-artist column 2 logic) to choose each successor. After
              the current track ends the phone calls POST /music/session/advance
              and Bilby picks the next track automatically.

  playlist — User gives a text prompt ("90s hip hop"). Bilby uses Ollama to
              select 10-12 matching tracks from the local library and queues
              them. Plays in order; at queue end falls through to DJ mode
              seeded by the last track.

  single   — Play one specific track (artist + title fuzzy search).
              After it ends, transitions to DJ mode seeded by that track
              (same behaviour as "Bilby uses that song as the now playing
              and picks up as if he was asked to DJ").

Music source priority
─────────────────────
  1. bilby_local  — local filesystem at /music (Syncthing corrected_music).
                    Fast, no API calls, 21k+ tracks. Used when /music is mounted.
  2. bilby_drive  — Google Drive Music/ folder. Fallback when /music not present.

Stream URLs
───────────
  GET /music/stream/<path:rel_path>
    → if /music/<rel_path> exists on disk → send_file (Range headers supported)
    → else → redirect to /bilby/stream/<rel_path> (Drive fallback)

  Phone ExoPlayer:  http://skippy-pc:3003/music/stream/<artist>/<album>/<track.mp3>
  Emulator:         http://10.0.2.2:3003/music/stream/<artist>/<album>/<track.mp3>

Quarantine holds: does not import from zPhone / SkippyDroid / SkippyGlassesMac.
"""

import threading
import uuid
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, redirect, request, send_file, url_for

from app.clients import bilby_local, bilby_drive
from app.version import envelope, error

bp = Blueprint("music", __name__)

_MUSIC_ROOT = Path("/music")


# ── Backend selection ─────────────────────────────────────────────────────────

def _backend():
    """Return whichever music backend is available. Local preferred."""
    if bilby_local.available():
        return bilby_local
    return bilby_drive


# ── Session state (single active session, protected by _lock) ─────────────────

_lock    = threading.Lock()
_session: dict | None = None


def _empty_session() -> dict:
    return {
        "session_id":  None,
        "mode":        None,
        "status":      "stopped",
        "now_playing": None,
        "queue":       [],
        "history":     [],   # last 20 file_ids played (dedup in DJ)
        "dj_seed":     None,
    }


def _make_stream_url(file_id: str) -> str:
    """Build stream URL. file_id may contain path separators for local files."""
    # URL-encode each path component but preserve slashes
    encoded = quote(file_id, safe="/")
    return f"/music/stream/{encoded}"


def _track_to_now_playing(track: dict) -> dict:
    fid = track.get("file_id", "")
    return {
        "artist":     track.get("artist") or "Unknown",
        "title":      track.get("title")  or "Unknown",
        "album":      track.get("album",  ""),
        "file_id":    fid,
        "stream_url": _make_stream_url(fid),
    }


def _session_response(sess: dict) -> dict:
    return {
        "session_id":       sess["session_id"],
        "mode":             sess["mode"],
        "status":           sess["status"],
        "now_playing":      sess["now_playing"],
        "queue_remaining":  len(sess.get("queue", [])),
        "source":           "local" if bilby_local.available() else "drive",
    }


# ── DJ helpers ────────────────────────────────────────────────────────────────

def _pick_dj_next(sess: dict) -> dict | None:
    """Ask the active backend for a column-2 suggestion."""
    np             = sess.get("now_playing") or {}
    current_fid    = np.get("file_id")
    current_artist = np.get("artist")
    return _backend().suggest_next(
        current_file_id=current_fid,
        current_artist=current_artist,
    )


def _advance_session(sess: dict) -> dict | None:
    """Move to the next track. Returns new now_playing dict or None."""
    np = sess.get("now_playing")
    if np:
        history = sess.get("history", [])
        history.append(np["file_id"])
        sess["history"] = history[-20:]
        sess["dj_seed"] = np

    # Playlist: pop from queue first.
    if sess["mode"] == "playlist" and sess.get("queue"):
        track = sess["queue"].pop(0)
        return _track_to_now_playing(track)

    # Queue exhausted or DJ/single → DJ pick. Auto-convert mode.
    if sess["mode"] in ("single", "playlist"):
        sess["mode"] = "dj"

    track = _pick_dj_next(sess)
    return _track_to_now_playing(track) if track else None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@bp.post("/music/session")
def start_session():
    """
    Start or restart a music session.

    Body:
      { "mode":   "dj" | "playlist" | "single",
        "prompt": "90s hip hop",        (playlist)
        "artist": "Daft Punk",          (single)
        "title":  "Around the World",   (single)
        "count":  10                    (playlist, default 10)
      }
    """
    global _session
    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "dj").lower()

    if mode not in ("dj", "playlist", "single"):
        return error(f"Unknown mode '{mode}'. Use dj, playlist, or single.",
                     error_type="bad_request", status=400)

    backend = _backend()
    sess    = _empty_session()
    sess["session_id"] = uuid.uuid4().hex[:8]
    sess["mode"]       = mode
    sess["status"]     = "playing"

    if mode == "dj":
        track = backend.now_playing() or backend.next_track()
        if track:
            sess["now_playing"] = _track_to_now_playing(track)

    elif mode == "playlist":
        prompt = body.get("prompt", "").strip()
        count  = max(1, int(body.get("count", 10)))
        if not prompt:
            return error("'prompt' is required for playlist mode",
                         error_type="bad_request", status=400)
        tracks = backend.playlist_for_prompt(prompt, count)
        if not tracks:
            return error(
                "No tracks found matching that description. "
                "Is the music library mounted at /music?",
                error_type="no_tracks", status=404,
            )
        sess["now_playing"] = _track_to_now_playing(tracks[0])
        sess["queue"]       = tracks[1:]

    elif mode == "single":
        artist = body.get("artist", "").strip()
        title  = body.get("title",  "").strip()
        track  = backend.search_track(artist, title) if (artist or title) else None
        if not track:
            track = backend.now_playing() or backend.next_track()
        if track:
            sess["now_playing"] = _track_to_now_playing(track)

    if not sess.get("now_playing"):
        return error(
            "No tracks available. Ensure /music is mounted and contains audio files.",
            error_type="no_tracks", status=404,
        )

    with _lock:
        _session = sess

    return envelope(_session_response(sess))


@bp.get("/music/session")
def get_session():
    """Return current session state. Phone polls every 5 s."""
    with _lock:
        sess = _session
    if not sess or sess["status"] == "stopped":
        return envelope({
            "session_id": None, "mode": None, "status": "stopped",
            "now_playing": None, "queue_remaining": 0,
            "source": "local" if bilby_local.available() else "drive",
        })
    return envelope(_session_response(sess))


@bp.post("/music/session/advance")
def advance():
    """
    Phone calls this when the current track ends (ExoPlayer STATE_ENDED).
    Bilby picks next track; single → DJ transition happens automatically.
    """
    global _session
    with _lock:
        sess = _session
    if not sess or sess["status"] == "stopped":
        return error("No active session", error_type="no_session", status=404)

    nxt = _advance_session(sess)
    with _lock:
        if nxt:
            _session["now_playing"] = nxt
            _session["status"]      = "playing"
        else:
            _session["status"]      = "stopped"
            _session["now_playing"] = None

    return envelope(_session_response(_session))


@bp.post("/music/session/skip")
def skip():
    """Manual skip — identical to advance."""
    return advance()


@bp.post("/music/session/control")
def control():
    """Body: { "action": "pause" | "resume" | "stop" }"""
    global _session
    body   = request.get_json(silent=True) or {}
    action = body.get("action", "").lower()
    if action not in ("pause", "resume", "stop"):
        return error("action must be pause, resume, or stop",
                     error_type="bad_request", status=400)

    with _lock:
        if not _session:
            return error("No active session", error_type="no_session", status=404)
        if action == "stop":
            _session = _empty_session()
            return envelope({"status": "stopped"})
        _session["status"] = "paused" if action == "pause" else "playing"
        return envelope(_session_response(_session))


# ── Stream endpoint ───────────────────────────────────────────────────────────

@bp.get("/music/stream/<path:rel_path>")
def music_stream(rel_path: str):
    """
    Serve audio for ExoPlayer.

    Local path (corrected_music):
      /music/stream/Led Zeppelin/IV/Stairway to Heaven.mp3
      → serves /music/Led Zeppelin/IV/Stairway to Heaven.mp3 directly
      → Werkzeug handles Range headers automatically (ExoPlayer seeking works)

    Drive file_id fallback (no slashes):
      /music/stream/1L_3xRIkrJt3_LIEJxEHO3mlGlY27Ip4B
      → redirects to /bilby/stream/<id> → /dropship/image/<id>
    """
    # Security: prevent path traversal outside /music
    try:
        full_path = (_MUSIC_ROOT / rel_path).resolve()
        full_path.relative_to(_MUSIC_ROOT.resolve())   # raises ValueError if outside
    except ValueError:
        return error("Invalid path", error_type="bad_request", status=400)

    if full_path.exists() and full_path.is_file():
        return send_file(full_path, conditional=True)

    # Fall back to Drive proxy
    return redirect(url_for("bilby.bilby_stream", file_id=rel_path))
