"""Sync whisper client. Proxies multipart audio to the whisper container."""
import os

import httpx


WHISPER_BASE_URL = os.getenv("WHISPER_BASE_URL", "http://whisper:8000")


class WhisperUnavailable(Exception):
    pass


def transcribe(audio_bytes: bytes, filename: str,
               content_type: str | None = None,
               hint_lang: str | None = None) -> dict:
    files = {"audio": (filename or "audio.bin", audio_bytes,
                       content_type or "application/octet-stream")}
    data = {"hint_lang": hint_lang} if hint_lang else {}
    try:
        # 30s ceiling — brief budget is <3s end-to-end, but the very
        # first request after container start pays the model load
        # (~10-20s for medium on 4070 Ti). Keep the door open.
        r = httpx.post(
            f"{WHISPER_BASE_URL}/transcribe",
            files=files,
            data=data,
            timeout=30.0,
        )
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError as e:
        raise WhisperUnavailable("whisper service unreachable") from e
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response is not None else ""
        raise WhisperUnavailable(
            f"whisper HTTP {e.response.status_code}: {body}"
        ) from e
