"""Translation endpoints.

POST /translate/audio — Whisper transcribe + translate to English.
  Request:  multipart audio (S23 sends WAV/AAC), optional `hint_lang` form field.
  Response: { detected_lang, english_text, confidence }
  Latency:  < 3s end-to-end.
  Privacy:  audio never leaves the mesh — Whisper runs locally.

POST /translate/text — detect language + translate to English via Gemini.
  Request:  { text: str, hint_lang?: str (ISO-639-1, e.g. "es") }
  Response: { detected_lang: str, english_text: str }
  Latency:  < 1s typical (Gemini REST).
  Fast path: pure-ASCII text with no hint_lang is returned as-is (definitely English).
"""
import json as _json

from flask import Blueprint, request

from app.clients import whisper as whisper_client
from app.clients import gemini as gemini_client
from app.version import envelope, error

bp = Blueprint("translate", __name__)


@bp.post("/translate/audio")
def translate_audio():
    if "audio" not in request.files:
        return error(
            "audio file required (multipart field name 'audio')",
            error_type="bad_request",
            status=400,
        )

    audio = request.files["audio"]
    hint_lang = request.form.get("hint_lang")

    try:
        result = whisper_client.transcribe(
            audio.read(),
            filename=audio.filename or "audio.bin",
            content_type=audio.mimetype,
            hint_lang=hint_lang,
        )
        return envelope(result)
    except whisper_client.WhisperUnavailable as e:
        return error(str(e), error_type="upstream_unavailable", status=503)
    except Exception as e:
        return error(str(e), error_type="upstream_error", status=502)


@bp.post("/translate/text")
def translate_text():
    """Detect language of text and translate.

    Default behaviour: translate to English (same as before).
    Optional `target_lang` field: translate to any ISO-639-1 language instead.

    Fast path: pure-ASCII text with no hint and target_lang == "en" (or unset)
    is returned immediately as { detected_lang:"en", english_text:<original> }
    without touching Gemini.

    Slow path: Gemini detects the language and produces the requested translation.
    Typical latency < 1s for short utterances (single-sentence voice transcripts).
    """
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return error("text is required", error_type="bad_request", status=400)

    hint_lang  = (body.get("hint_lang")   or "").strip() or None
    target_lang = (body.get("target_lang") or "en").strip() or "en"

    # Fast path — pure ASCII with no hint and targeting English is overwhelmingly English.
    if not hint_lang and target_lang == "en" and text.isascii():
        return envelope({"detected_lang": "en", "english_text": text})

    try:
        result = _detect_and_translate(text, hint_lang, target_lang=target_lang)
        return envelope(result)
    except Exception as e:
        return error(str(e), error_type="upstream_error", status=502)


def _detect_and_translate(
    text: str,
    hint_lang: str | None,
    target_lang: str = "en",
) -> dict:
    """Use Gemini to detect the language and translate to `target_lang`.

    The system prompt locks Gemini into JSON-only mode. The user prompt is
    a single compact instruction so the round-trip stays under 1s.

    Always returns:
      { detected_lang: str, english_text: str }
    where `english_text` is actually the translated text in `target_lang`
    (field name kept for backward compat with existing clients).
    """
    lang_hint   = f" (hint: the language is likely '{hint_lang}')" if hint_lang else ""
    target_name = {
        "en": "English", "es": "Spanish", "fr": "French",
        "de": "German",  "pt": "Portuguese", "it": "Italian",
        "ja": "Japanese","zh": "Chinese (Simplified)",
    }.get(target_lang, target_lang.upper())

    prompt = (
        f"Detect the language of the text below{lang_hint} and translate it to {target_name}. "
        f"If it is already {target_name}, return it unchanged.\n"
        "Respond with a single JSON object — no markdown, no explanation:\n"
        '{"detected_lang":"<ISO-639-1 code>","english_text":"<translation>"}\n\n'
        f"Text: {text}"
    )
    raw = gemini_client.chat(
        prompt,
        system=(
            "You are a translation assistant. "
            "Always respond with a single valid JSON object only. "
            "No markdown code blocks. No extra text."
        ),
    )
    # Strip accidental markdown fences (e.g. ```json ... ```)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0].strip()

    parsed = _json.loads(raw)
    return {
        "detected_lang": str(parsed.get("detected_lang", hint_lang or "unknown")),
        "english_text":  str(parsed.get("english_text", text)),
    }
