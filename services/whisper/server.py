"""skippytel-whisper — faster-whisper microservice.

Internal-only service on the compose network. The flask-api container
proxies multipart audio uploads here. faster-whisper does
transcribe+translate-to-English in one pass via task="translate".

Lazy-loads the model on first request. Subsequent requests reuse it
(model lives in process memory; ~2GB GPU resident for medium).

Privacy doctrine (from brief): audio never leaves the mesh. This
service writes uploads to a tempfile, runs inference, deletes the
tempfile. Nothing persisted.
"""
import os
import tempfile
import logging
from typing import Optional

import httpx
from fastapi import FastAPI, UploadFile, File, Form
from faster_whisper import WhisperModel

MODEL_SIZE = os.getenv("WHISPER_MODEL", "medium")
DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")

# SkippyTel keyword primer — fetched once at startup, biases the Whisper
# decoder toward the Captain's known voice commands.
_FLASK_API_URL = os.getenv("FLASK_API_URL", "http://flask-api:5000")
_primer: Optional[str] = None

log = logging.getLogger("whisper.server")


def _fetch_primer() -> Optional[str]:
    """Fetch /keywords/whisper-primer from flask-api. Fails silently."""
    try:
        r = httpx.get(f"{_FLASK_API_URL}/keywords/whisper-primer", timeout=3.0)
        if r.status_code == 200 and r.text.strip():
            phrases = r.text.strip()
            log.info("Whisper STT primer loaded: %d chars", len(phrases))
            return phrases
    except Exception as exc:
        log.warning("Could not fetch keyword primer: %s", exc)
    return None


_model: Optional[WhisperModel] = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    return _model


app = FastAPI(title="skippytel-whisper")


@app.on_event("startup")
async def _on_startup():
    """Load keyword primer on container start so every transcribe() benefits."""
    global _primer
    _primer = _fetch_primer()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "whisper",
        "model": MODEL_SIZE,
        "device": DEVICE,
        "loaded": _model is not None,
    }


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    hint_lang: Optional[str] = Form(None),
    initial_prompt: Optional[str] = Form(None),
):
    # faster-whisper's transcribe() accepts a file path and shells out to
    # ffmpeg internally for decode (handles AAC/WAV/AMR/Opus etc.). We
    # write the upload to a tempfile rather than streaming to keep ffmpeg
    # happy — it wants seekable input.
    suffix = os.path.splitext(audio.filename or "")[1] or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await audio.read())
        path = tmp.name

    try:
        model = get_model()
        # Use caller-supplied primer, or fall back to the startup-loaded
        # keyword primer so every session benefits from STT biasing.
        prompt = initial_prompt or _primer or None
        segments, info = model.transcribe(
            path,
            task="translate",          # output is always English
            language=hint_lang,        # None = auto-detect
            beam_size=1,               # speed over marginal quality
            vad_filter=True,           # drop silence segments
            initial_prompt=prompt,     # keyword biasing
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return {
            "detected_lang": info.language,
            "english_text": text,
            "confidence": float(info.language_probability),
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
