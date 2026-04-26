"""
SkippyTel — /explain endpoint

Self-documenting protocol reference for zPhone integration.

GET /explain
  Returns the full zPhone integration protocol as structured JSON plus
  human-readable plain text. Intended for internal developer use only:
    - Devs building new SkippyTel service routes
    - External service authors self-registering via POST /register
    - New native Android apps joining the local.skippy.* family
    - AI agents (Claude Code, etc.) bootstrapping into the ecosystem

  This endpoint is NOT a public API surface. SkippyTel lives behind
  Tailscale — only devices on the Captain's private network can reach it.
  The /explain doc exists to help *us* build more things faster, not to
  expose internals to the internet. No secrets are disclosed here (no API
  keys, no credentials, no user data) — just architecture and contracts.

GET /explain/compliance
  Returns the compliance checklist only — each requirement with its id,
  rationale, and optional exception. Use this to verify a new service or
  app before adding it to the manifest.
"""

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

bp = Blueprint("explain", __name__)

# ── Protocol document ─────────────────────────────────────────────────────────

_PROTOCOL = {
    "name": "zPhone Integration Protocol",
    "version": "1",
    "description": (
        "SkippyTel is the hub of the Skippy AR/AI ecosystem. "
        "Phone apps (SkippyChat, SkippyDroid) and external services "
        "communicate through it. This document describes everything a "
        "service or app needs to do to integrate correctly."
    ),

    # ── The 3 main views ──────────────────────────────────────────────────
    "views": {
        "description": (
            "The Captain's world has three display surfaces. "
            "A service declares which ones it supports via the 'capabilities' "
            "field in GET /services."
        ),
        "chat": {
            "name": "SkippyChat",
            "description": (
                "Phone portrait mode. Voice-first feed of messages. "
                "Services here respond inline to queries — they produce text "
                "or images that appear as bubbles in the feed. "
                "No persistent UI; each interaction is a request/response cycle."
            ),
            "capability_key": "chat",
            "entry_point": "POST /intent/unmatched or dedicated endpoint",
            "trigger": "voice keyword or typed text",
        },
        "hud": {
            "name": "SkippyDroid HUD",
            "description": (
                "Phone landscape mode, overlaid on the VITURE XR glasses. "
                "Services render as persistent overlays in named HUD zones "
                "(topcenter, topend, viewport, etc.). Black pixels are "
                "additive-transparent — the real world shows through. "
                "Services are always-on while the mode is active."
            ),
            "capability_key": "hud",
            "entry_point": "FeatureModule (native) or SUIP scene push (external)",
            "trigger": "ContextEngine mode change or voice command",
        },
        "fullscreen": {
            "name": "SkippyDroid FULLSCREEN",
            "description": (
                "SkippyDroid in GlassesDisplayEngine.Mode.FULLSCREEN. "
                "HUD chrome is hidden; the selected service fills the entire "
                "1920×1200 canvas. Only one service claims the Viewport at a "
                "time. If no service is active, the glass panoramic backdrop "
                "is shown instead. "
                "Voice: 'fullscreen' activates, 'hud mode' restores chrome."
            ),
            "capability_key": "fullscreen",
            "entry_point": "HudZone.Viewport FeatureModule or SUIP PassthroughHost",
            "trigger": "voice 'fullscreen' or passthroughHost.activate(id)",
        },
    },

    # ── Compliance requirements ───────────────────────────────────────────
    "compliance": {
        "description": (
            "A service is zPhone-compliant if it runs natively within "
            "SkippyChat / SkippyDroid without requiring a third-party Android "
            "app or browser. Non-compliant services are marked in GET /services "
            "and are regarded with loathsome disdain by the other services."
        ),
        "requirements": [
            {
                "id":          "no_external_app",
                "description": "Does not require launching a third-party Android app.",
                "rationale":   "The Captain cannot use voice to dismiss or return from an external app.",
            },
            {
                "id":          "no_browser",
                "description": "Does not require opening a web browser.",
                "rationale":   "Browser tabs break the immersive passthrough interface.",
            },
            {
                "id":          "no_ui_theft",
                "description": "Does not steal focus / bring itself to the foreground unprompted.",
                "rationale":   "SkippyDroid must remain the top activity during glasses sessions.",
            },
            {
                "id":          "voice_trigger",
                "description": "Declares at least one voice_trigger phrase in GET /services.",
                "rationale":   "The Captain cannot touch the screen while wearing the glasses.",
                "exception":   "Infrastructure services (vault) may omit voice triggers.",
            },
            {
                "id":          "black_background",
                "description": "Uses #000000 as the background color in any HUD/fullscreen UI.",
                "rationale":   "Black = additive-transparent on the VITURE XR. Colored fills block the real world.",
            },
            {
                "id":          "palette",
                "description": (
                    "UI colors are drawn from the canonical 9-color Skippy palette: "
                    "Black(#000000) Green(#00FF88) White(#FFFFFF) Amber(#FFAA00) "
                    "Cyan(#00CCFF) Violet(#818CF8) Red(#FF4444) "
                    "DimGreen(#003322) DimGreenHi(#006644)."
                ),
                "rationale":   "Consistent palette across HUD layers and chat bubbles.",
            },
            {
                "id":          "graceful_offline",
                "description": "Degrades gracefully when the service is unreachable (null fields, not crashes).",
                "rationale":   "The Captain may rotate into SkippyDroid HUD with the Mac Bilby offline.",
            },
        ],
    },

    # ── Service registration protocol ─────────────────────────────────────
    "service_registration": {
        "description": (
            "How to add a new service to SkippyTel. Two paths: "
            "static (edit services.py) for first-party services, "
            "dynamic (POST /register) for self-registering external services."
        ),
        "static": {
            "file":   "SkippyTel/services/flask-api/app/routes/services.py",
            "method": "Add an entry to the list returned by _build_manifest().",
            "required_fields": [
                "id", "name", "tagline", "available", "capabilities",
                "zphone_compliant", "display", "voice_triggers", "endpoints",
            ],
            "display_modes": ["overlay", "companion", "inline", "background"],
            "hud_zones":     [
                "topcenter", "topstart", "topend",
                "bottomcenter", "bottomstart", "bottomend",
                "leftbar", "rightbar", "viewport",
            ],
        },
        "dynamic": {
            "endpoint":    "POST /register",
            "description": (
                "External services can self-register by POSTing the same "
                "JSON shape as a static manifest entry. SkippyDroid's "
                "PassthroughHost will accept SUIP v1 scene pushes from "
                "registered services."
            ),
            "suip_manifest": "GET /manifest.json on the external service",
            "suip_version":  "1",
        },
    },

    # ── Android app compliance ────────────────────────────────────────────
    "android_app": {
        "description": "Requirements for a new native Android app in the zPhone ecosystem.",
        "package_prefix": "local.skippy.",
        "requirements": [
            "Package name must start with local.skippy.",
            "Uses ChatPalette or HudPalette color constants (no raw hex in composables).",
            "Does not call requestedOrientation = LANDSCAPE — SkippyDroid owns landscape.",
            "Does not register its own SpeechRecognizer — SkippyChat/SkippyDroid own voice.",
            "Declares its voice_triggers in SkippyTel GET /services, not locally.",
            "APK is staged to SkippyTel/apks/ so the Vault bootstrap can distribute it.",
        ],
        "palette_file":   "zPhone/apps/SkippyChat/.../compositor/ChatPalette.kt",
        "hud_palette_file": "zPhone/apps/SkippyDroid/.../compositor/HudPalette.kt",
    },

    # ── SkippyTel Flask API conventions ──────────────────────────────────
    "api_conventions": {
        "base_url":       "http://skippy-pc:3003  (Tailscale) | http://10.0.2.2:3003 (emulator)",
        "port":           3003,
        "content_type":   "application/json",
        "auth":           "None (Tailscale network-layer trust — do not expose to public internet)",
        "health_endpoint": "GET /health → {status: ok, spec_version: 1, ...}",
        "services_endpoint": "GET /services → manifest with capabilities + compliance fields",
        "intent_endpoint": "POST /intent/unmatched → {reply, speak, tier, narration?, image_b64?}",
        "response_fields": {
            "reply":       "str — text to display in SkippyChat feed",
            "speak":       "str|null — text to pass to TTS (defaults to reply if null)",
            "tier":        "str|null — 'local' | 'cloud' | service id",
            "narration":   "str|null — in-character narration line shown above reply",
            "image_b64":   "str|null — base64 PNG for image generation results",
            "image_prompt":"str|null — the SD prompt that produced the image",
        },
        "error_convention": "Non-2xx returns {error: str, detail: str, hint: str?}",
    },
}


# ── Endpoint ──────────────────────────────────────────────────────────────────

@bp.get("/explain")
def explain():
    """
    Return the full zPhone integration protocol.

    Query params:
      ?format=json  (default) — structured JSON
      ?format=text            — human-readable plain text summary
    """
    fmt = request.args.get("format", "json")
    if fmt == "text":
        lines = [
            "zPhone Integration Protocol v1",
            "=" * 40,
            "",
            _PROTOCOL["description"],
            "",
            "THE 3 MAIN VIEWS",
            "-" * 20,
        ]
        for vk, vv in _PROTOCOL["views"].items():
            if vk == "description":
                continue   # skip the prose description key
            lines.append(f"  {vk.upper()} ({vv['name']})")
            lines.append(f"    {vv['description']}")
            lines.append(f"    Capability key: '{vv['capability_key']}'")
            lines.append("")

        lines += [
            "COMPLIANCE",
            "-" * 20,
            _PROTOCOL["compliance"]["description"],
            "",
        ]
        for req in _PROTOCOL["compliance"]["requirements"]:
            lines.append(f"  [{req['id']}]  {req['description']}")
            lines.append(f"    Why: {req['rationale']}")
            lines.append("")

        lines += [
            "API CONVENTIONS",
            "-" * 20,
            f"  Base URL : {_PROTOCOL['api_conventions']['base_url']}",
            f"  Auth     : {_PROTOCOL['api_conventions']['auth']}",
            f"  Health   : {_PROTOCOL['api_conventions']['health_endpoint']}",
            f"  Services : {_PROTOCOL['api_conventions']['services_endpoint']}",
            f"  Intent   : {_PROTOCOL['api_conventions']['intent_endpoint']}",
        ]
        return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}

    return jsonify({
        "spec_version":  "1",
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "protocol":      _PROTOCOL,
    })


@bp.get("/explain/compliance")
def explain_compliance():
    """
    Return the compliance requirements as a checklist for automated tooling.
    Each requirement has an id, description, rationale, and optional exception.
    """
    return jsonify({
        "spec_version": "1",
        "requirements": _PROTOCOL["compliance"]["requirements"],
        "views":        list(_PROTOCOL["views"].keys()),
        "palette": {
            "Black":      "#000000",
            "Green":      "#00FF88",
            "White":      "#FFFFFF",
            "Amber":      "#FFAA00",
            "Cyan":       "#00CCFF",
            "Violet":     "#818CF8",
            "Red":        "#FF4444",
            "DimGreen":   "#003322",
            "DimGreenHi": "#006644",
        },
        "android_requirements": _PROTOCOL["android_app"]["requirements"],
    })
