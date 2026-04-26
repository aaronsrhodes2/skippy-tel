"""SkippyTel — Text-to-Speech endpoint

GET  /tts/stream?text=<url-encoded-text>
     Streams audio/mpeg from ElevenLabs directly to the caller.
     Android MediaPlayer uses this URL as a data source — playback starts
     within ~400ms of the response beginning.

     Query params:
       text     — the text to speak (required, max 500 chars enforced)
       voice_id — optional ElevenLabs voice ID override

GET  /tts/health
     Returns TTS configuration status (key set? voice ID? model?).
     Does NOT make an ElevenLabs call — just validates config.

Voice attribution:
  Default voice: Clyde (2EiwWnXFnvU5JabPnv8n) — stock ElevenLabs voice.
  For R.C. Brey / Skippy clone: set ELEVENLABS_VOICE_ID in .env.

  ⚠ Character: Skippy the Magnificent © Craig Alanson (Expeditionary Force)
  ⚠ Narration: R.C. Brey
  ⚠ Cloned voice = personal use only. Permission from both required for release.
"""
import logging

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.clients import elevenlabs_client as elabs
from app.version import error

log = logging.getLogger(__name__)
bp  = Blueprint("tts", __name__)

_MAX_CHARS = 500   # Skippy is terse. Long walls of text shouldn't be spoken verbatim.


@bp.get("/tts/health")
def tts_health():
    """Config check — does not call ElevenLabs."""
    return jsonify({
        "configured":  elabs.is_configured(),
        "voice_id":    elabs.settings.elevenlabs_voice_id,
        "model":       elabs.settings.elevenlabs_model,
        "max_chars":   _MAX_CHARS,
    })


@bp.get("/tts/stream")
def tts_stream():
    """
    Stream TTS audio for the given text.
    Android MediaPlayer calls this URL directly — streaming starts immediately.

    Usage:
      GET /tts/stream?text=I+am+Skippy+the+Magnificent
    """
    text = (request.args.get("text") or "").strip()
    if not text:
        return error("text is required", status=400)

    # Truncate gracefully — cut at a sentence boundary if possible
    if len(text) > _MAX_CHARS:
        cut = text[:_MAX_CHARS]
        # Try to end at last sentence boundary
        for sep in (". ", "! ", "? ", ", "):
            idx = cut.rfind(sep)
            if idx > _MAX_CHARS // 2:
                cut = cut[:idx + 1]
                break
        text = cut.strip()
        log.debug("tts: truncated to %d chars", len(text))

    voice_id = request.args.get("voice_id") or None

    try:
        def _generate():
            for chunk in elabs.stream_tts(text, voice_id=voice_id):
                yield chunk

        return Response(
            stream_with_context(_generate()),
            mimetype="audio/mpeg",
            headers={
                "Cache-Control":       "no-cache",
                "X-Accel-Buffering":   "no",
                "Transfer-Encoding":   "chunked",
            },
        )

    except elabs.ElevenLabsUnavailable as e:
        log.warning("tts: ElevenLabs unavailable: %s", e)
        return error(str(e), error_type="tts_unavailable", status=503)
    except Exception as e:
        log.error("tts: unexpected error: %s", e)
        return error(str(e), error_type="tts_error", status=500)
