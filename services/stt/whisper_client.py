import asyncio
import tempfile
import os
from config import settings

_model = None


async def init() -> None:
    global _model
    from faster_whisper import WhisperModel
    loop = asyncio.get_event_loop()
    _model = await loop.run_in_executor(
        None,
        lambda: WhisperModel(
            settings.whisper_model_size,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        ),
    )


async def transcribe(audio_bytes: bytes, language: str | None = None) -> str:
    if _model is None:
        raise RuntimeError("Whisper not initialized")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _transcribe_sync, tmp_path, language)
        return result
    finally:
        os.unlink(tmp_path)


def _transcribe_sync(path: str, language: str | None) -> str:
    kwargs = {}
    if language:
        kwargs["language"] = language
    segments, _ = _model.transcribe(path, **kwargs)
    return " ".join(seg.text.strip() for seg in segments)
