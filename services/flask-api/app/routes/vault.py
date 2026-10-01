"""Vault + APK download endpoints (data and files only; no HTML page).

Zero-auth: Tailscale membership IS the authorization. Only devices on the
Tailscale mesh can reach port 3003. Nothing here contains secrets — secrets
(API keys, Drive tokens) stay server-side and are never surfaced to the phone.

GET /vault/bootstrap   — phone app config (called on first launch / re-install)
GET /apk/<app>         — download a Skippy APK by short name

The HTML recovery page that used to live at GET /phone (APK links + QR codes)
was removed with the rest of skippy core's interface; the shelved phone apps
only ever read /vault/bootstrap and /apk/<app>.

APKs live at /apks/<name>.apk inside the container, bind-mounted from
./apks/ on the host. Drop new builds there; no container restart needed
(Flask streams from disk on each request).

Known APK names (case-insensitive):
  skippy-chat    → SkippyChat (voice + AI chat)
  skippy-droid   → SkippyDroid (AR HUD overlay)
  skippy-gallery → SkippyGallery (generated image viewer)
"""
import os
from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file

from app.config  import settings
from app.version import SPEC_VERSION, envelope

bp = Blueprint("vault", __name__)

APK_DIR = Path("/apks")

_APK_NAMES = {
    "skippy-chat":    ("skippy-chat.apk",    "SkippyChat",    "AI voice chat — Skippy's primary interface"),
    "skippy-droid":   ("skippy-droid.apk",   "SkippyDroid",   "AR HUD overlay for landscape mode"),
    "skippy-gallery": ("skippy-gallery.apk", "SkippyGallery", "Generated image gallery"),
}


# ── /vault/bootstrap ─────────────────────────────────────────────────────

@bp.get("/vault/bootstrap")
def vault_bootstrap():
    """Phone app configuration. Called on first launch or after re-install.

    The phone needs only two things to bootstrap:
      1. The SkippyTel URL (already known — it's where this request came from)
      2. Which features are enabled

    Everything sensitive (API keys, Drive tokens) stays server-side.
    The phone never holds credentials — Tailscale is the gate.
    """
    # Derive SkippyTel URL from the request host so it works on any network
    # (Tailscale MagicDNS, LAN, emulator loopback, etc.) without config.
    host = request.host  # e.g. "skippy-pc:3003" or "10.0.2.2:3003"
    scheme = "http"      # Tailscale is trusted mesh; TLS not required inside it
    skippy_tel_url = f"{scheme}://{host}"

    return envelope({
        "skippy_tel_url":      skippy_tel_url,
        "bilby_mac_url":       settings.bilby_mac_url or None,
        "features": {
            "sms":       True,
            "image":     False,   # not yet deployed — first-party SD service pending
            "bilby":     bool(settings.bilby_mac_url or settings.bilby_drive_folder_id),
            "translate": True,
        },
        "apk_base_url": f"{skippy_tel_url}/apk",
    })


# ── /apk/<app> ────────────────────────────────────────────────────────────

@bp.get("/apk/<app_name>")
def download_apk(app_name: str):
    """Stream a Skippy APK by short name (e.g. /apk/skippy-chat)."""
    key = app_name.lower().removesuffix(".apk")
    if key not in _APK_NAMES:
        known = ", ".join(_APK_NAMES)
        abort(404, f"Unknown app '{app_name}'. Known: {known}")

    filename, label, _ = _APK_NAMES[key]
    path = APK_DIR / filename
    if not path.exists():
        abort(503, f"{label} APK not yet staged. Copy {filename} to SkippyTel/apks/ on the PC.")

    return send_file(
        path,
        mimetype="application/vnd.android.package-archive",
        as_attachment=True,
        download_name=filename,
    )
