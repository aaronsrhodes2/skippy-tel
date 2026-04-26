"""LLaVA-based photo description client.

Sends image bytes to llava (multimodal) via Ollama and returns a
plain-English description suitable for full-text search.

The model is called with a rich prompt so the description covers:
  - People (appearance, clothing, approximate age)
  - Objects and setting
  - Activity / scene
  - Visible text, signs, landmarks
  - Colors and mood

If llava is not installed, the function returns an empty string and
logs a warning — tagging silently skips that image.
"""
import base64
import logging
from typing import Optional

import httpx
from app.config import settings

log = logging.getLogger(__name__)

# Use a separate timeout — vision inference is slow on CPU
_TIMEOUT = httpx.Timeout(120.0)

_PROMPT = (
    "Describe this photo in detail for later search. "
    "Cover: people (appearance, clothing, gender, age estimate), "
    "objects, setting/location, activity, any visible text or signs, "
    "colors, and mood. Be specific. If you recognise a celebrity or "
    "famous place name it. Reply in 2-4 sentences of plain English."
)


def describe_image(image_bytes: bytes, mime: str = "image/jpeg") -> Optional[str]:
    """
    Send `image_bytes` to llava via Ollama /api/generate.
    Returns the model's description string, or None on failure.
    """
    b64 = base64.b64encode(image_bytes).decode()
    url = f"{settings.ollama_base_url}/api/generate"
    payload = {
        "model": "llava",
        "prompt": _PROMPT,
        "images": [b64],
        "stream": False,
    }
    try:
        r = httpx.post(url, json=payload, timeout=_TIMEOUT)
        if r.status_code == 404:
            log.warning("photo_tagger: llava model not found in Ollama — skipping tag")
            return None
        r.raise_for_status()
        desc = r.json().get("response", "").strip()
        log.debug("photo_tagger: described %d bytes → %s...", len(image_bytes), desc[:60])
        return desc or None
    except httpx.ConnectError:
        log.warning("photo_tagger: Ollama unreachable — skipping tag")
        return None
    except Exception as exc:
        log.error("photo_tagger: error describing image: %s", exc)
        return None
