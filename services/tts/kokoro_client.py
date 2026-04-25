import io
import wave
import numpy as np
from config import settings

_kokoro = None


async def init() -> None:
    global _kokoro
    from kokoro_onnx import Kokoro
    _kokoro = Kokoro("kokoro-v1.9.onnx", "voices-v1.0.bin")


async def synthesize(text: str, voice: str | None = None) -> bytes:
    if _kokoro is None:
        raise RuntimeError("Kokoro not initialized")
    v = voice or settings.kokoro_voice
    samples, sample_rate = _kokoro.create(text, voice=v, lang=settings.kokoro_lang)
    return _to_wav(samples, sample_rate)


def _to_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    samples_int16 = (samples * 32767).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples_int16.tobytes())
    return buf.getvalue()
