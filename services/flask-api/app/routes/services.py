"""
SkippyTel service manifest — GET /services

Self-describes every capability SkippyTel exposes. Phone apps poll this
endpoint to discover available services, configure voice triggers, and
know which HUD zone or companion app to use for each service.

Availability is computed live (sub-50ms checks) so the phone always sees
the real state: Mac offline → Bilby traktor unavailable, SD service down
→ image generation unavailable, etc.

Response shape:
  {
    "spec_version": "1",
    "refreshed_at": "<ISO-8601>",
    "services": [
      {
        "id":           str,   machine name  e.g. "bilby"
        "name":         str,   display name  e.g. "Bilby"
        "tagline":      str,   one-line description
        "available":    bool,  live availability check
        "status_detail":str | null,  human-readable why/state
        "base_url":     str | null,  direct Tailscale URL for external services;
                                     null = proxy all traffic through SkippyTel
        "display": {
          "mode":         "overlay" | "companion" | "inline" | "background",
          "hud_zone":     str | null,  SkippyDroid zone name
          "companion_app":str | null,  Android package name
        },
        "voice_triggers": [str, ...],
        "endpoints":    {str: str, ...}  short name → relative path (appended to
                                         base_url when set, otherwise to SkippyTel)
      },
      ...
    ]
  }
"""

import socket
from datetime import datetime, timezone
from flask import Blueprint, jsonify

from app.config import settings

bp = Blueprint("services", __name__)


# ── Availability probes ────────────────────────────────────────────────────────

def _tcp_reachable(host: str, port: int, timeout: float = 0.8) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _bilby_available() -> tuple[bool, str | None]:
    if settings.bilby_mac_url:
        host = settings.bilby_mac_url.replace("http://", "").replace("https://", "").split(":")[0]
        if _tcp_reachable(host, 7334):
            return True, "Mac Bilby reachable (Traktor mode)"
    if settings.bilby_drive_folder_id:
        return True, "Drive mode (Mac offline)"
    return False, "BILBY_MAC_URL and BILBY_DRIVE_FOLDER_ID not set"


def _image_available() -> tuple[bool, str | None]:
    return False, "Image generation not yet deployed — first-party SD service pending"


def _ups_available() -> tuple[bool, str | None]:
    if _tcp_reachable("host.docker.internal", 9338):
        return True, "UPS monitor online"
    return False, "UPS monitor offline (ups-monitor.py not running)"


def _dropship_available() -> tuple[bool, str | None]:
    if settings.google_drive_images_folder_id:
        return True, f"Drive Images/ folder: {settings.google_drive_images_folder_id[:8]}…"
    return False, "GOOGLE_DRIVE_IMAGES_FOLDER_ID not set"


def _jellyfin_available() -> tuple[bool, str | None]:
    if _tcp_reachable("host.docker.internal", 8096):
        key_status = "API key set" if settings.jellyfin_api_key else "no API key (set JELLYFIN_API_KEY)"
        return True, f"Jellyfin online — {key_status}"
    return False, "Jellyfin offline (not installed or not running)"


# ── Service definitions ────────────────────────────────────────────────────────

def _build_manifest() -> list[dict]:
    bilby_ok,     bilby_detail     = _bilby_available()
    image_ok,     image_detail     = _image_available()
    ups_ok,       ups_detail       = _ups_available()
    dropship_ok,  dropship_detail  = _dropship_available()
    jellyfin_ok,  jellyfin_detail  = _jellyfin_available()

    return [
        {
            "id":      "intent",
            "name":    "Skippy AI",
            "tagline": "Your ancient AI companion — Ollama / Claude / Gemini",
            "available": True,
            "status_detail": f"default model: {settings.ollama_model}",
            "base_url": None,   # lives inside SkippyTel
            "display": {
                "mode":         "companion",
                "hud_zone":     "bottomcenter",
                "companion_app": "local.skippy.chat",
            },
            "voice_triggers": ["*"],   # catch-all — must be last in scanner
            "endpoints": {
                "chat":   "/intent/unmatched",
            },
        },
        {
            "id":      "bilby",
            "name":    "Bilby",
            "tagline": "Automated DJ — Traktor decks or Drive shuffle",
            "available": bilby_ok,
            "status_detail": bilby_detail,
            # Bilby's web UI lives on the Mac at port 7334. Phone clients that
            # are on Tailscale can hit it directly; emulator goes through SkippyTel.
            "base_url": settings.bilby_mac_url or None,
            "display": {
                "mode":         "overlay",
                "hud_zone":     "topcenter",
                "companion_app": None,
            },
            "voice_triggers": [
                "what's playing",
                "now playing",
                "bilby next",
                "next track",
                "skip track",
                "stop music",
                "play music",
            ],
            "endpoints": {
                "status": "/bilby/status",
                "next":   "/bilby/next",
                "stop":   "/bilby/stop",
                "stream": "/bilby/stream/<file_id>",
            },
        },
        {
            "id":      "dropship",
            "name":    "DropShip",
            "tagline": "Google Drive image gallery — all your photos",
            "available": dropship_ok,
            "status_detail": dropship_detail,
            "base_url": None,
            "display": {
                "mode":         "companion",
                "hud_zone":     None,
                "companion_app": "local.skippy.gallery",
            },
            "voice_triggers": [
                "open gallery",
                "show gallery",
                "open dropship",
            ],
            "endpoints": {
                "list":   "/dropship/list",
                "image":  "/dropship/image/<file_id>",
                "thumb":  "/dropship/thumb/<file_id>",
                "upload": "/dropship/upload",
                "health": "/dropship/health",
            },
        },
        {
            "id":      "image",
            "name":    "Image Generator",
            "tagline": "Stable Diffusion on Skippy PC — inline in chat",
            "available": image_ok,
            "status_detail": image_detail,
            "base_url": None,
            "display": {
                "mode":         "inline",
                "hud_zone":     None,
                "companion_app": None,
                "inline_target": "chat",
            },
            "voice_triggers": [
                "show me",
                "generate",
                "draw me",
                "imagine",
                "picture of",
            ],
            "endpoints": {
                "generate": "/image",
            },
        },
        {
            "id":      "translate",
            "name":    "Translator",
            "tagline": "Whisper audio + Gemini text translation",
            "available": True,
            "status_detail": None,
            "base_url": None,
            "display": {
                "mode":         "inline",
                "hud_zone":     None,
                "companion_app": None,
                "inline_target": "chat",
            },
            "voice_triggers": [
                "translate",
                "in english",
                "what does that mean",
            ],
            "endpoints": {
                "text":  "/translate/text",
                "audio": "/translate/audio",
            },
        },
        {
            "id":      "ups",
            "name":    "UPS Monitor",
            "tagline": "Power guardian — CyberPower ABMT1500",
            "available": ups_ok,
            "status_detail": ups_detail,
            "base_url": None,
            "display": {
                "mode":         "overlay",
                "hud_zone":     "topend",
                "companion_app": None,
            },
            "voice_triggers": [
                "ups status",
                "power status",
                "battery status",
            ],
            "endpoints": {
                "status": "/ups/status",
            },
        },
        {
            "id":      "heybuddy",
            "name":    "Hey Buddy",
            "tagline": "Face recognition — DB stays on Skippy PC",
            "available": True,
            "status_detail": None,
            "base_url": None,
            "display": {
                "mode":         "inline",
                "hud_zone":     None,
                "companion_app": None,
                "inline_target": "chat",
            },
            "voice_triggers": [
                "who is that",
                "identify person",
                "hey buddy",
            ],
            "endpoints": {
                "identify": "/heybuddy/identify",
            },
        },
        {
            "id":      "starmap",
            "name":    "StarMap",
            "tagline": "Celestial bodies overhead — stub, physics service pending",
            "available": False,
            "status_detail": "physics-mcp service not yet built",
            "base_url": None,
            "display": {
                "mode":         "overlay",
                "hud_zone":     "viewport",
                "companion_app": None,
            },
            "voice_triggers": [
                "open star map",
                "what's that star",
                "show starmap",
                "night sky",
            ],
            "endpoints": {
                "query": "/starmap",
            },
        },
        {
            "id":      "jellyfin",
            "name":    "Jellyfin",
            "tagline": "Local movie & TV server — D:\\Movies on Skippy PC",
            "available": jellyfin_ok,
            "status_detail": jellyfin_detail,
            # Jellyfin runs on the PC at :8096. Phone clients on Tailscale hit it
            # directly; emulator falls through to the proxy path.
            "base_url": settings.jellyfin_base_url if jellyfin_ok else None,
            "display": {
                "mode":         "companion",
                "hud_zone":     None,
                "companion_app": "org.jellyfin.mobile",   # official Jellyfin Android app
            },
            "voice_triggers": [
                "play a movie",
                "open jellyfin",
                "movie time",
                "what's on",
                "show movies",
                "open movies",
            ],
            "endpoints": {
                "health":  "/health",
                "search":  "/Items?searchTerm={query}&IncludeItemTypes=Movie,Series&Recursive=true",
                "now_playing": "/Sessions?ControllableByUserId=any",
                "libraries": "/Library/MediaFolders",
            },
        },
        {
            "id":      "vault",
            "name":    "Vault",
            "tagline": "Bootstrap — APK downloads, phone configuration",
            "available": True,
            "status_detail": None,
            "base_url": None,
            "display": {
                "mode":         "background",
                "hud_zone":     None,
                "companion_app": None,
            },
            "voice_triggers": [],
            "endpoints": {
                "bootstrap": "/vault/bootstrap",
                "phone":     "/phone",
            },
        },
    ]


# ── Endpoint ──────────────────────────────────────────────────────────────────

@bp.get("/services")
def get_services():
    """Return the live SkippyTel service manifest."""
    return jsonify({
        "spec_version":  "1",
        "refreshed_at":  datetime.now(timezone.utc).isoformat(),
        "services":      _build_manifest(),
    })
