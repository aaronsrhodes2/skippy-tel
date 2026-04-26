"""ElevenLabs Text-to-Speech client — streaming audio for Skippy's voice.

Streaming endpoint: POST /v1/text-to-speech/{voice_id}/stream
Returns: audio/mpeg chunks — Android MediaPlayer starts playing within ~400ms.

Voice configuration (set in .env):
  ELEVENLABS_API_KEY   — your ElevenLabs API key
  ELEVENLABS_VOICE_ID  — voice ID (default: Clyde, 2EiwWnXFnvU5JabPnv8n)
  ELEVENLABS_MODEL     — TTS model (default: eleven_turbo_v2_5, lowest latency)

For the R.C. Brey / Skippy clone:
  1. Rip 1-3 min of clean Brey-as-Skippy from the Expeditionary Force audiobooks.
  2. Upload to ElevenLabs → Instant Voice Clone → copy the voice ID.
  3. Set ELEVENLABS_VOICE_ID=<that_id> in .env.

⚠ ATTRIBUTION & LICENSE:
  Character: Skippy the Magnificent © Craig Alanson (Expeditionary Force series)
  Narration: R.C. Brey (audiobook narrator)
  Any cloned voice is for PERSONAL USE ONLY.
  Permission from Craig Alanson AND R.C. Brey required before any distribution.
"""
import logging
from typing import Iterator

import httpx
from app.config import settings

log = logging.getLogger(__name__)

_BASE = "https://api.elevenlabs.io/v1"

# Voice settings tuned for Skippy's character:
# - stability 0.55: slightly variable delivery (he's expressive, not monotone)
# - similarity_boost 0.80: stay close to the voice profile
# - style 0.40: moderate style exaggeration (enough for the snark to land)
# - use_speaker_boost: enhances clarity for phone speaker playback
_VOICE_SETTINGS = {
    "stability":         0.55,
    "similarity_boost":  0.80,
    "style":             0.40,
    "use_speaker_boost": True,
}


class ElevenLabsUnavailable(Exception):
    pass


def _headers() -> dict:
    return {
        "xi-api-key":   settings.elevenlabs_api_key,
        "Content-Type": "application/json",
        "Accept":       "audio/mpeg",
    }


def is_configured() -> bool:
    return bool(settings.elevenlabs_api_key)


def stream_tts(text: str, voice_id: str | None = None) -> Iterator[bytes]:
    """
    Stream audio/mpeg chunks from ElevenLabs for the given text.
    Yields raw bytes as they arrive — caller pipes them to the HTTP response.

    Raises ElevenLabsUnavailable on connection or auth failure.
    """
    if not is_configured():
        raise ElevenLabsUnavailable("ELEVENLABS_API_KEY not set")

    vid   = voice_id or settings.elevenlabs_voice_id
    model = settings.elevenlabs_model
    url   = f"{_BASE}/text-to-speech/{vid}/stream"

    payload = {
        "text":           text,
        "model_id":       model,
        "voice_settings": _VOICE_SETTINGS,
    }

    try:
        with httpx.stream(
            "POST", url,
            headers=_headers(),
            json=payload,
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0),
        ) as r:
            if r.status_code == 401:
                raise ElevenLabsUnavailable("ElevenLabs API key invalid or expired")
            if r.status_code == 422:
                raise ElevenLabsUnavailable(f"ElevenLabs rejected request: {r.text}")
            r.raise_for_status()
            for chunk in r.iter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk
    except httpx.ConnectError as e:
        raise ElevenLabsUnavailable("ElevenLabs unreachable") from e
    except httpx.HTTPStatusError as e:
        raise ElevenLabsUnavailable(f"ElevenLabs HTTP {e.response.status_code}") from e
