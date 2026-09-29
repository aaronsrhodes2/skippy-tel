"""Bilby music service — /bilby/* endpoints.

Source priority chain (applied in order, first non-null wins):

  1. Mac Bilby live poll  — stage9_dj_suggest.py on the Mac via Tailscale.
                            Requires BILBY_MAC_URL set and Mac reachable.

  2. Pushed state cache   — Mac Bilby (or any client) calls POST /bilby/push
                            to proactively send now-playing data to SkippyTel.
                            Served for up to PUSH_TTL_S seconds after the last
                            push. Survives brief Tailscale drops during a show.

  3. Drive Bilby          — SkippyTel-side random queue from the Drive Music/
                            folder. Auto-syncs to the pushed track title when
                            a push arrives (searches Drive for the same song).

  4. "none"               — No source available. BigCard shows stoner-nap msg.

Endpoints
─────────
GET  /bilby/status          — Now Playing (priority chain above)
GET  /bilby/status?source=X — Force a specific source
POST /bilby/push            — Mac Bilby pushes now-playing here (see below)
POST /bilby/next            — Skip / pick next (source-aware)
POST /bilby/stop            — Stop playback (source-aware)
GET  /bilby/stream/<id>     — Redirect to /dropship/image/<id> for audio bytes

Mac Bilby push wiring (add to stage9_dj_suggest.py on the Mac):
  On every track-load event:
    import requests, threading
    def _push():
        try: requests.post(
            "http://skippy-pc:3003/bilby/push",
            json={"title": track_title, "artist": track_artist,
                  "bpm": bpm_float, "key": key_str},
            timeout=3)
        except: pass
    threading.Thread(target=_push, daemon=True).start()
"""
import time

from flask import Blueprint, redirect, request, url_for

from app.clients import bilby as bilby_client
from app.clients import bilby_drive
from app.clients import bilby_local
from app.config  import settings
from app.version import envelope, error

bp = Blueprint("bilby", __name__)

# ── Pushed-state cache ────────────────────────────────────────────────────────
# Mac Bilby calls POST /bilby/push on track changes. We cache the result and
# serve it as a fallback when the live Mac poll fails (Tailscale hiccup, etc.).
# TTL: 30 seconds — stale data is worse than "none" for the now-playing card.
PUSH_TTL_S: int = 30

_pushed_now_playing: dict | None = None   # {title, artist, bpm?, key?}
_pushed_at: float = 0.0


def _pushed_state_fresh() -> dict | None:
    """Return the pushed now-playing dict if it's within TTL, else None."""
    if _pushed_now_playing and (time.time() - _pushed_at) < PUSH_TTL_S:
        return _pushed_now_playing
    return None


# ── /bilby/push ───────────────────────────────────────────────────────────────

@bp.post("/bilby/push")
def bilby_push():
    """Mac Bilby calls this when the now-playing track changes.

    Body (JSON):
      { "title": str, "artist": str, "bpm"?: float, "key"?: str }

    Side-effects:
      - Caches the state for PUSH_TTL_S seconds.
      - If BILBY_DRIVE_FOLDER_ID is configured, searches Drive for the same
        track and queues it so the phone player stays in sync with Traktor.
    """
    global _pushed_now_playing, _pushed_at

    body = request.get_json(silent=True) or {}
    title  = (body.get("title")  or "").strip()
    artist = (body.get("artist") or "").strip()

    if not title and not artist:
        return error("title or artist is required", error_type="bad_request", status=400)

    _pushed_now_playing = {
        "title":  title,
        "artist": artist,
        "bpm":    body.get("bpm"),
        "key":    body.get("key"),
    }
    _pushed_at = time.time()

    # ── Drive auto-sync ───────────────────────────────────────────────────
    # If Drive Bilby is configured, queue the same track so the phone
    # player matches what Traktor is playing. Best-effort, non-blocking.
    synced = False
    if settings.bilby_drive_folder_id and title:
        try:
            from app.clients import bilby_local as backend  # noqa: PLC0415
            track = backend.search_track(artist, title)
            if track:
                bilby_drive.queue_specific(track)
                synced = True
        except Exception:
            pass   # Drive sync is advisory — never block the push response

    return envelope({
        "received": True,
        "title":    title,
        "artist":   artist,
        "synced_to_drive": synced,
    })


# ── /bilby/status ─────────────────────────────────────────────────────────────

@bp.get("/bilby/status")
def bilby_status():
    """Return Now Playing state using the priority chain.

    Response shape:
      {
        "source":        "traktor" | "pushed" | "drive" | "none",
        "traktor_alive": bool,
        "playing_deck":  "a" | "b" | null,
        "now_playing":   {"title", "artist", "bpm"?, "key"?} | null,
        "loaded":        {"a": ..., "b": ...},
        "stale_s"?:      int   (seconds since last push, only on source=pushed)
      }
    """
    source = request.args.get("source", "auto")

    # ── 1. Mac Bilby live poll ─────────────────────────────────────────────
    if source in ("auto", "traktor") and settings.bilby_mac_url:
        data = bilby_client.now_playing()
        if data is not None:
            deck   = data.get("playing_deck")
            loaded = data.get("loaded", {})
            # Mac Bilby's full now-playing (bpm, key, elapsed…) when a deck plays;
            # otherwise the loaded deck, else the most-recently-loaded one.
            now    = data.get("now_playing") or (loaded.get(deck) if deck else None)
            if now is None:
                now = loaded.get("a") or loaded.get("b")
            # Enrich with bpm/key from pushed state if Mac Bilby omits them
            pushed = _pushed_state_fresh()
            if now and pushed and now.get("title") == pushed.get("title"):
                now = {**now,
                       "bpm": now.get("bpm") or pushed.get("bpm"),
                       "key": now.get("key") or pushed.get("key")}
            return envelope({
                "source":        "traktor",
                "traktor_alive": data.get("traktor_alive", False),
                "playing_deck":  deck,
                "now_playing":   now,
                "loaded":        loaded,
            })
        if source == "traktor":
            return error(
                "Mac Bilby unreachable — is stage9_dj_suggest.py running?",
                error_type="bilby_mac_unreachable",
                status=503,
            )

    # ── 2. Pushed state (recent webhook from Mac Bilby) ───────────────────
    if source in ("auto", "traktor"):
        pushed = _pushed_state_fresh()
        if pushed:
            stale = int(time.time() - _pushed_at)
            return envelope({
                "source":        "pushed",
                "traktor_alive": False,
                "playing_deck":  None,
                "now_playing":   pushed,
                "loaded":        {"a": pushed, "b": None},
                "stale_s":       stale,
            })

    # ── 3. Local library (corrected_music mounted at /music) ─────────────────
    if source in ("auto", "local") and bilby_local.available():
        if not bilby_local.ready():
            # Background scan still running (~40s on first boot) — don't block
            return envelope({
                "source":        "local",
                "traktor_alive": False,
                "playing_deck":  None,
                "now_playing":   None,
                "loaded":        {"a": None, "b": None},
                "message":       "Library scan in progress…",
            })
        track = bilby_local.now_playing()
        if track:
            return envelope({
                "source":        "local",
                "traktor_alive": False,
                "playing_deck":  None,
                "now_playing":   track,
                "loaded":        {"a": track, "b": None},
                "queue_size":    bilby_local.queue_size(),
            })

    # ── 4. Drive Bilby ────────────────────────────────────────────────────
    if source in ("auto", "drive"):
        folder_id = settings.bilby_drive_folder_id
        if not folder_id:
            return envelope({
                "source":        "none",
                "traktor_alive": False,
                "playing_deck":  None,
                "now_playing":   None,
                "loaded":        {"a": None, "b": None},
                "message":       "No music source — local /music not mounted and BILBY_DRIVE_FOLDER_ID not set.",
            })
        track = bilby_drive.now_playing()
        return envelope({
            "source":        "drive",
            "traktor_alive": False,
            "playing_deck":  None,
            "now_playing":   track,
            "loaded":        {"a": track, "b": None},
            "queue_size":    bilby_drive.queue_size(),
        })

    # ── 4. Nothing ────────────────────────────────────────────────────────
    return envelope({
        "source":        "none",
        "traktor_alive": False,
        "playing_deck":  None,
        "now_playing":   None,
        "loaded":        {"a": None, "b": None},
    })


# ── /bilby/next ───────────────────────────────────────────────────────────────

@bp.post("/bilby/next")
def bilby_next():
    source = request.args.get("source", "auto")

    if source in ("auto", "traktor") and settings.bilby_mac_url:
        data = bilby_client.now_playing()
        if data is not None:
            return envelope({
                "message":   "Next track — load the next track in Traktor.",
                "automated": False,
                "source":    "traktor",
            })

    # Local music session (covers bilby_local and any active /music/session).
    if source in ("auto", "local"):
        from app.routes import music as _music  # noqa: PLC0415
        nxt = _music.skip_current_track()
        if nxt:
            return envelope({
                "source":      "local",
                "automated":   True,
                "now_playing": nxt,
            })
        # No active session — try bilby_local directly (start-of-session first skip)
        if bilby_local.available() and bilby_local.ready():
            track = bilby_local.next_track()
            if track:
                return envelope({
                    "source":      "local",
                    "automated":   True,
                    "now_playing": {"artist": track.get("artist"), "title": track.get("title")},
                })

    if source in ("auto", "drive") and settings.bilby_drive_folder_id:
        track = bilby_drive.next_track()
        if track:
            return envelope({
                "source":      "drive",
                "automated":   True,
                "now_playing": track,
                "queue_size":  bilby_drive.queue_size(),
            })
        return envelope({
            "source":    "drive",
            "automated": False,
            "message":   "Drive queue is empty.",
        })

    return envelope({"message": "Next track — no active music source.", "automated": False})


# ── /bilby/stop ───────────────────────────────────────────────────────────────

@bp.post("/bilby/stop")
def bilby_stop():
    source = request.args.get("source", "auto")

    if source in ("auto", "traktor") and settings.bilby_mac_url:
        data = bilby_client.now_playing()
        if data is not None:
            return envelope({"message": "Stop — pause Traktor on the Mac.", "automated": False})

    if source in ("auto", "drive") and settings.bilby_drive_folder_id:
        bilby_drive.invalidate()
        return envelope({"source": "drive", "automated": True, "message": "Drive queue cleared."})

    return envelope({"message": "Stop — no active music source.", "automated": False})


# ── /bilby/stream/<file_id> ───────────────────────────────────────────────────

@bp.get("/bilby/stream/<file_id>")
def bilby_stream(file_id: str):
    """Redirect audio to the DropShip proxy (keeps Drive creds in one place)."""
    return redirect(url_for("dropship.dropship_image", file_id=file_id))
