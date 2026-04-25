"""Vault + phone recovery endpoints.

Zero-auth: Tailscale membership IS the authorization. Only devices on the
Tailscale mesh can reach port 3003. Nothing here contains secrets — secrets
(API keys, Drive tokens) stay server-side and are never surfaced to the phone.

GET /vault/bootstrap   — phone app config (called on first launch / re-install)
GET /phone             — HTML recovery page with APK links + QR codes
GET /apk/<app>         — download a Skippy APK by short name

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

from flask import Blueprint, Response, abort, jsonify, render_template_string, request, send_file

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
            "image":     bool(settings.sd_base_url),
            "bilby":     bool(settings.bilby_mac_url or settings.bilby_drive_folder_id),
            "translate": True,
            "heybuddy":  True,
        },
        "apk_base_url": f"{skippy_tel_url}/apk",
        "phone_url":    f"{skippy_tel_url}/phone",
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


# ── /phone ────────────────────────────────────────────────────────────────

_PHONE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Skippy Phone Recovery</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #000;
    color: #00ff88;
    font-family: 'Courier New', monospace;
    padding: 24px 16px;
    max-width: 520px;
    margin: 0 auto;
  }
  h1 { font-size: 1.1rem; letter-spacing: 3px; text-transform: uppercase;
       color: #fff; margin-bottom: 4px; }
  .subtitle { font-size: 0.72rem; color: #555; letter-spacing: 2px;
              text-transform: uppercase; margin-bottom: 32px; }
  .step { margin-bottom: 32px; }
  .step-num { font-size: 0.65rem; color: #555; letter-spacing: 2px;
              text-transform: uppercase; margin-bottom: 8px; }
  .apk-card {
    border: 1px solid #1a3a2a;
    border-radius: 6px;
    padding: 16px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .apk-card .info { flex: 1; }
  .apk-card .name { font-size: 1rem; color: #fff; margin-bottom: 2px; }
  .apk-card .desc { font-size: 0.72rem; color: #555; }
  .apk-card .status { font-size: 0.7rem; margin-top: 6px; }
  .apk-card .status.ok   { color: #00ff88; }
  .apk-card .status.miss { color: #ff4444; }
  a.dl-btn {
    display: inline-block;
    padding: 10px 18px;
    background: #0a2a1a;
    border: 1px solid #00ff88;
    color: #00ff88;
    text-decoration: none;
    border-radius: 4px;
    font-family: monospace;
    font-size: 0.85rem;
    white-space: nowrap;
  }
  a.dl-btn:hover { background: #00ff88; color: #000; }
  a.dl-btn.disabled {
    border-color: #333;
    color: #333;
    pointer-events: none;
  }
  .qr-wrap { text-align: center; margin-top: 8px; }
  .qr-label { font-size: 0.65rem; color: #555; margin-bottom: 4px; }
  .note {
    font-size: 0.72rem;
    color: #556;
    border-left: 2px solid #1a3a2a;
    padding-left: 12px;
    margin-top: 8px;
    line-height: 1.6;
  }
  .tailscale-box {
    border: 1px solid #1a2a3a;
    border-radius: 6px;
    padding: 16px;
    margin-bottom: 12px;
  }
  .tailscale-box .name { color: #fff; margin-bottom: 6px; }
  .ts-url { color: #4af; font-size: 0.85rem; }
  hr { border: none; border-top: 1px solid #111; margin: 32px 0; }
  .footer { font-size: 0.65rem; color: #333; text-align: center; margin-top: 32px; }
</style>
</head>
<body>

<h1>Skippy</h1>
<div class="subtitle">phone recovery &amp; setup</div>

<div class="step">
  <div class="step-num">Step 1 — connect to the mesh</div>
  <div class="tailscale-box">
    <div class="name">Tailscale</div>
    <div class="note">
      Install Tailscale from Play Store, then tap
      <strong style="color:#fff">Sign in with Google</strong>.
      Your Google account is already there — one tap and you're on the mesh.
      Once connected, <span class="ts-url">{{ skippy_tel_url }}</span> is reachable.
    </div>
  </div>
</div>

<div class="step">
  <div class="step-num">Step 2 — install Skippy apps</div>
  <p class="note" style="margin-bottom:16px">
    Tap a button to download the APK, then open it to install.
    You may need to allow installs from browser in Android settings once.
  </p>

  {% for key, (filename, label, desc) in apks.items() %}
  <div class="apk-card">
    <div class="info">
      <div class="name">{{ label }}</div>
      <div class="desc">{{ desc }}</div>
      <div class="status {% if available[key] %}ok{% else %}miss{% endif %}">
        {% if available[key] %}✓ available{% else %}✗ not yet staged on PC{% endif %}
      </div>
    </div>
    {% if available[key] %}
    <a class="dl-btn" href="/apk/{{ key }}">↓ install</a>
    {% else %}
    <span class="dl-btn disabled">↓ install</span>
    {% endif %}
  </div>
  {% endfor %}
</div>

<div class="step">
  <div class="step-num">Step 3 — scan from PC screen (optional)</div>
  <p class="note" style="margin-bottom:12px">
    Scan this QR with your new phone's camera to open this page without typing.
  </p>
  <div class="qr-wrap">
    <div class="qr-label">this page</div>
    <div id="qr-phone"></div>
  </div>
</div>

<hr>

<div class="step">
  <div class="step-num">bootstrap endpoint</div>
  <p class="note">
    Apps call <strong style="color:#aaa">GET {{ skippy_tel_url }}/vault/bootstrap</strong>
    on first launch to pull server config. No credentials stored on phone.
    Tailscale membership is the only gate.
  </p>
</div>

<div class="footer">Skippy the Magnificent — running on {{ host }}</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"
        integrity="sha512-CNgIRecGo7nphbeZ04Sc13ka07paqdeTu0WR1IM4kNcpmBAUSHSNlMhklSjK/Rf8BDjMPpZGBzXSQOFnv5V8w=="
        crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
  new QRCode(document.getElementById("qr-phone"), {
    text: "{{ phone_url }}",
    width: 180,
    height: 180,
    colorDark: "#00ff88",
    colorLight: "#000000",
    correctLevel: QRCode.CorrectLevel.M
  });
</script>

</body>
</html>
"""

@bp.get("/phone")
def phone_recovery():
    """Phone recovery page — APK downloads + QR codes."""
    host = request.host
    scheme = "http"
    skippy_tel_url = f"{scheme}://{host}"
    phone_url      = f"{skippy_tel_url}/phone"

    available = {
        key: (APK_DIR / filename).exists()
        for key, (filename, _, _) in _APK_NAMES.items()
    }

    html = render_template_string(
        _PHONE_HTML,
        apks          = _APK_NAMES,
        available     = available,
        skippy_tel_url = skippy_tel_url,
        phone_url     = phone_url,
        host          = host,
    )
    return Response(html, mimetype="text/html")
