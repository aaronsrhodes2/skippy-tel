"""make_image's first choice: Gemini's image model, with the Captain's free key (the paid one if there is no free one).
NOTE (2026-10): Google gives free keys NO image output (the pricing page: "Not available"; free keys get 429 "limit: 0"),
so with the free key every picture is a Refused and the local SDXL draws it. Image output needs the paid key.

Gemini is usually the best picture, but it refuses some prompts and has quotas, so every outcome that isn't a picture is a
Refused (say why, in a few words) and the chat falls back to skippy-pc's own SDXL (chat_tools._make_image). A refusal is
any of: a blocked prompt (promptFeedback.blockReason), a candidate that finished for a safety/prohibited/no-image reason,
a reply with no inline image, an HTTP error (quota 429, 5xx) or no answer in time.

    POST {BASE}/models/{model}:generateContent   x-goog-api-key: <key>
    {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}}
 -> candidates[0].content.parts[*].inlineData {mimeType, data (base64)}
"""
from __future__ import annotations

import base64
import os

import requests

BASE = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_S = 90
MAX_BYTES = 20_000_000


class Refused(Exception):
    """Gemini didn't make the picture (refused, out of quota, failed): fall back to the local studio."""


def _key() -> str:
    for name in ("GEMINI_FREE_API_KEY", "GEMINI_API_KEY"):
        v = os.environ.get(name, "").strip()
        if v and v != "unset":
            return v
    return ""


def model() -> str:
    return os.environ.get("GEMINI_IMAGE_MODEL", "").strip() or "gemini-3.1-flash-image"   # 2.5-flash-image is deprecated (2026-10)


def ready() -> bool:
    return bool(_key())


def _aspect(width: int, height: int) -> str:
    if width > height:
        return "3:2"
    if height > width:
        return "2:3"
    return "1:1"


def generate(prompt: str, width: int = 1024, height: int = 1024, http=requests) -> tuple[bytes, str]:
    """(image bytes, mime type), or Refused with a short reason."""
    key = _key()
    if not key:
        raise Refused("no Gemini key")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE", "TEXT"],
                                 "imageConfig": {"aspectRatio": _aspect(width, height)}}}
    try:
        r = http.post(f"{BASE}/models/{model()}:generateContent", json=body, timeout=TIMEOUT_S,
                      headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    except requests.RequestException as exc:
        raise Refused(f"Gemini didn't answer ({type(exc).__name__})") from exc
    if r.status_code == 429:
        raise Refused("Gemini's free quota is used up for now")
    if r.status_code >= 400:
        raise Refused(f"Gemini returned {r.status_code}")
    try:
        data = r.json()
    except ValueError as exc:
        raise Refused("Gemini's reply wasn't JSON") from exc
    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise Refused(f"Gemini blocked the prompt ({blocked})")
    candidates = data.get("candidates") or []
    if not candidates:
        raise Refused("Gemini returned no picture")
    first = candidates[0] or {}
    for part in ((first.get("content") or {}).get("parts") or []):
        inline = part.get("inlineData") or part.get("inline_data") or {}
        mime = inline.get("mimeType") or inline.get("mime_type") or ""
        if inline.get("data") and mime in ("image/png", "image/jpeg"):
            raw = base64.b64decode(inline["data"])
            if len(raw) > MAX_BYTES:
                raise Refused("Gemini's picture was too big")
            return raw, mime
    reason = first.get("finishReason") or "no image in the reply"
    raise Refused(f"Gemini didn't draw it ({reason})")
