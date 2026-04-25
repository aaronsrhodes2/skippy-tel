"""
Service icon generation — GET /services/<service_id>/icon

Returns a 128×128 PNG icon for a SkippyTel service.

Priority:
  1. Cached PNG in state/icons/<id>.png (generated once, reused forever)
  2. Stable Diffusion — dark neon app icon, service-specific prompt
  3. Pillow fallback — colored circle + initial letter (always works)

Icons are generated lazily on first request and cached permanently.
Delete state/icons/<id>.png to force regeneration.
"""

import io
from pathlib import Path

from flask import Blueprint, send_file, abort

bp = Blueprint("service_icons", __name__)

# ── Config ─────────────────────────────────────────────────────────────────────

ICON_DIR  = Path("/app/state/icons")
ICON_SIZE = 128

# Accent colours per service — used by Pillow icon generator.
# When we stand up a first-party SD service, prompts go here too.
_SERVICE_META: dict[str, str] = {
    "intent":    "#00FFFF",
    "bilby":     "#AA44FF",
    "dropship":  "#4488FF",
    "image":     "#00FF88",
    "translate": "#FFFF44",
    "ups":       "#FF8800",
    "heybuddy":  "#00FFDD",
    "starmap":   "#2244FF",
    "jellyfin":  "#FFAA00",
    "vault":     "#00FF44",
}
_DEFAULT_COLOR = "#888888"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _generate_pillow_fallback(service_id: str, color_hex: str) -> bytes:
    """Generate a simple colored circle icon using Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img  = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Parse hex color
        r = int(color_hex[1:3], 16)
        g = int(color_hex[3:5], 16)
        b = int(color_hex[5:7], 16)

        # Outer glow (slightly larger, translucent)
        glow_color = (r, g, b, 60)
        draw.ellipse([4, 4, ICON_SIZE - 4, ICON_SIZE - 4], fill=glow_color)

        # Dark background circle
        draw.ellipse([8, 8, ICON_SIZE - 8, ICON_SIZE - 8], fill=(12, 12, 20, 255))

        # Bright ring
        draw.ellipse([10, 10, ICON_SIZE - 10, ICON_SIZE - 10],
                     outline=(r, g, b, 255), width=3)

        # Initial letter, centered
        letter = (service_id[0] if service_id else "S").upper()
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 52)
        except Exception:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), letter, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = (ICON_SIZE - tw) // 2 - bbox[0]
        ty = (ICON_SIZE - th) // 2 - bbox[1]
        draw.text((tx, ty), letter, fill=(r, g, b, 255), font=font)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    except ImportError:
        # Ultra-fallback: minimal 1×1 PNG
        return (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
            b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )


def _get_or_create_icon(service_id: str) -> bytes:
    """Return cached icon bytes, generating via Pillow if needed."""
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = ICON_DIR / f"{service_id}.png"

    if cache_path.exists():
        return cache_path.read_bytes()

    _, color = _SERVICE_META.get(service_id, ("", _DEFAULT_COLOR))
    png_bytes = _generate_pillow_fallback(service_id, color)
    cache_path.write_bytes(png_bytes)
    return png_bytes


# ── Endpoint ───────────────────────────────────────────────────────────────────

@bp.get("/services/<service_id>/icon")
def get_service_icon(service_id: str):
    """
    Return a 128×128 PNG icon for the given service.
    Generated on first call (SD or Pillow fallback), cached permanently.
    Delete state/icons/<id>.png to regenerate.
    """
    # Sanitise input — alphanumeric + underscore only
    clean_id = "".join(c for c in service_id if c.isalnum() or c == "_")
    if not clean_id:
        abort(400, "Invalid service id")

    png = _get_or_create_icon(clean_id)
    return send_file(
        io.BytesIO(png),
        mimetype="image/png",
        as_attachment=False,
        download_name=f"{clean_id}.png",
    )
