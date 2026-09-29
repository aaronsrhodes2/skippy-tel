"""Bilby music service — /bilby/* endpoints.

Bilby is Skippy's automated DJ. Two modes, one Now Playing output:

  source=traktor  (default when Mac is reachable)
    Proxies to stage9_dj_suggest.py running on the Mac at
    settings.bilby_mac_url. Traktor is playing; we report what it says.

  source=drive    (SkippyTel-side, Google Drive streaming)
    Random track selection from the Music/ Drive folder.
    Uses bilby_drive client (in-memory shuffled queue from Drive).

Endpoints
─────────
GET  /bilby/status          — Now Playing (tries traktor, falls back to drive)
GET  /bilby/status?source=X — Force a specific source
POST /bilby/next            — Skip / pick next (source-aware)
POST /bilby/stop            — Stop playback (source-aware)
GET  /bilby/stream/<file_id> — Redirect to /dropship/image/<file_id> for audio bytes

Phone integration
─────────────────
ChatViewModel polls /bilby/status every ~10 s when the music card is
visible. Voice keywords ("what's playing", "Bilby next", "stop music")
hit POST /bilby/next and /bilby/stop.
"""
from flask import Blueprint, redirect, request, url_for

from app.clients import bilby as bilby_client
from app.clients import bilby_drive
from app.config  import settings
from app.version import envelope, error

bp = Blueprint("bilby", __name__)


# ── /bilby/status ────────────────────────────────────────────────────────

@bp.get("/bilby/status")
def bilby_status():
    """Return Now Playing state.

    Response shape:
      {
        "source":        "traktor" | "drive" | "none",
        "traktor_alive": bool,
        "playing_deck":  "a" | "b" | null,
        "now_playing":   {"title": str, "artist": str} | null,
        "loaded": {
          "a": {"title": str, "artist": str} | null,
          "b": {"title": str, "artist": str} | null
        }
      }
    """
    source = request.args.get("source", "auto")

    # ── Traktor path ──────────────────────────────────────────────────────
    if source in ("auto", "traktor") and settings.bilby_mac_url:
        data = bilby_client.now_playing()
        if data is not None:
            deck = data.get("playing_deck")
            loaded = data.get("loaded", {})
            now = data.get("now_playing") or (loaded.get(deck) if deck else None)
            # If nothing is actively playing but something is loaded,
            # surface the most-recently-loaded deck.
            if now is None:
                now = loaded.get("a") or loaded.get("b")
            return envelope({
                "source":        "traktor",
                "traktor_alive": data.get("traktor_alive", False),
                "playing_deck":  deck,
                "now_playing":   now,
                "loaded":        loaded,
            })
        # Mac unreachable — fall through to drive if source=auto
        if source == "traktor":
            return error(
                "Mac Bilby unreachable — is stage9_dj_suggest.py running?",
                error_type="bilby_mac_unreachable",
                status=503,
            )

    # ── Drive path ────────────────────────────────────────────────────────
    if source in ("auto", "drive"):
        folder_id = settings.bilby_drive_folder_id
        if not folder_id:
            return envelope({
                "source":        "none",
                "traktor_alive": False,
                "playing_deck":  None,
                "now_playing":   None,
                "loaded":        {"a": None, "b": None},
                "message":       "No music source available. Set BILBY_MAC_URL or BILBY_DRIVE_FOLDER_ID.",
            })

        track = bilby_drive.now_playing()
        return envelope({
            "source":        "drive",
            "traktor_alive": False,
            "playing_deck":  None,
            "now_playing":   track,          # {title, artist, album, file_id} or None
            "loaded":        {"a": track, "b": None},
            "queue_size":    bilby_drive.queue_size(),
        })

    return error(f"Unknown source '{source}'", error_type="bad_request", status=400)


# ── /bilby/next ──────────────────────────────────────────────────────────

@bp.post("/bilby/next")
def bilby_next():
    """Skip to the next track.

    For source=traktor: advisory only (Mac DJ loads manually).
    For source=drive:   advances the in-memory queue and returns the new track.
    """
    source = request.args.get("source", "auto")

    # If Mac is reachable, it's probably running Traktor — advisory only.
    if source in ("auto", "traktor") and settings.bilby_mac_url:
        data = bilby_client.now_playing()
        if data is not None:
            return envelope({
                "message":  "Next track — load the next track in Traktor.",
                "automated": False,
                "source":   "traktor",
            })

    # Drive mode: advance queue.
    if source in ("auto", "drive") and settings.bilby_drive_folder_id:
        track = bilby_drive.next_track()
        if track:
            return envelope({
                "source":    "drive",
                "automated": True,
                "now_playing": track,
                "queue_size":  bilby_drive.queue_size(),
            })
        return envelope({
            "source":    "drive",
            "automated": False,
            "message":   "Drive queue is empty — no music found in Music/ folder.",
        })

    return envelope({
        "message":  "Next track — no active music source.",
        "automated": False,
    })


# ── /bilby/stop ──────────────────────────────────────────────────────────

@bp.post("/bilby/stop")
def bilby_stop():
    """Stop playback.

    Traktor: advisory only.
    Drive: invalidates the queue (forces re-shuffle on next play).
    """
    source = request.args.get("source", "auto")

    if source in ("auto", "traktor") and settings.bilby_mac_url:
        data = bilby_client.now_playing()
        if data is not None:
            return envelope({"message": "Stop — pause Traktor on the Mac.", "automated": False})

    if source in ("auto", "drive") and settings.bilby_drive_folder_id:
        bilby_drive.invalidate()
        return envelope({
            "source":    "drive",
            "automated": True,
            "message":   "Drive queue cleared.",
        })

    return envelope({"message": "Stop — no active music source.", "automated": False})


# ── /bilby/stream/<file_id> ───────────────────────────────────────────────

@bp.get("/bilby/stream/<file_id>")
def bilby_stream(file_id: str):
    """Redirect audio requests to the DropShip image proxy.

    SkippyMusic (ExoPlayer) hits this URL; SkippyTel forwards to
    GET /dropship/image/<file_id> which streams raw bytes from Drive.
    This keeps the Drive credential boundary in one place (dropship.py).
    """
    return redirect(url_for("dropship.dropship_image", file_id=file_id))
