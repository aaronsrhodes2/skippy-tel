"""The home listener's way to ears and mouth: the voice gate (services/voice-gate) on this same PC, with a pass signed from the home token.

The gate does the work already built for the portal's chats: Whisper for speech to text, Skippy's local voice (Kokoro + F5-TTS via tts-studio,
with a cache of what was said before) for text to speech, and the Ollama summary that turns a long reply into a few spoken sentences.
Passes are the same scheme as portal/voice.py (label "voice-gate-v1"); services/satellite/tests pin this against services/voice-gate/gate.py.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import struct
import time
import wave

import requests

PASS_S = 8 * 3600
PASS_BUCKET_S = 6 * 3600


class GateError(Exception):
    """The gate could not do it (down, refused, nothing usable)."""


class BudgetSpent(GateError):
    """Skippy's real voice has used today's character budget: use another voice until tomorrow."""


def gate_key(home_token: str) -> bytes:
    hashed = hashlib.sha256(home_token.encode()).hexdigest()
    return hmac.new(hashed.encode(), b"voice-gate-v1", hashlib.sha256).digest()


VOICES = ("stock", "skippy")          # services/voice-gate/gate.py: a pass names the one voice it may speak in


def sign(key: bytes, exp: int, scope: str = "skippy") -> str:
    """scope: "listen" or a voice name (the house is the Captain's: the rooms speak as the local Skippy clone). Must match gate.sign()."""
    text = f"voice-tts-{scope}:{exp}" if scope in VOICES else f"voice:{exp}"
    return f"{exp}.{hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:32]}"


def wav_bytes(samples, rate: int = 16000) -> bytes:
    """Mono 16-bit WAV of float samples in -1..1."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, int(float(x) * 32767)))) for x in samples))
    return buf.getvalue()


class Gate:
    def __init__(self, base: str, home_token: str, post=requests.post, clock=time.time):
        self.base, self.key, self.post, self.clock = base.rstrip("/"), gate_key(home_token), post, clock

    def _url(self, route: str) -> str:
        exp = (int(self.clock()) // PASS_BUCKET_S + 1) * PASS_BUCKET_S + PASS_S
        return f"{self.base}/t/{sign(self.key, exp)}/{route}"

    def stt(self, wav: bytes, lang: str = "en") -> str:
        try:
            r = self.post(self._url("stt") + (f"?lang={lang}" if lang else ""), data=wav, headers={"Content-Type": "audio/wav"}, timeout=40)
        except requests.RequestException as e:
            raise GateError("the voice gate is not answering") from e
        if r.status_code != 200:
            raise GateError(f"speech to text refused ({r.status_code})")
        return str((r.json() or {}).get("text") or "").strip()

    def tts(self, text: str) -> bytes:
        try:
            r = self.post(self._url("tts"), json={"text": text[:500]}, timeout=60)
        except requests.RequestException as e:
            raise GateError("the voice gate is not answering") from e
        if r.status_code == 429:
            try:
                if (r.json() or {}).get("budget"):
                    raise BudgetSpent("today's voice budget is spent")
            except ValueError:
                pass
            raise GateError("the voice gate is busy")
        if r.status_code != 200 or not r.content:
            raise GateError(f"text to speech refused ({r.status_code})")
        return r.content

    def summary(self, text: str) -> str:
        try:
            r = self.post(self._url("summary"), json={"text": text[:8000]}, timeout=45)
        except requests.RequestException as e:
            raise GateError("the voice gate is not answering") from e
        if r.status_code != 200:
            raise GateError(f"summary refused ({r.status_code})")
        said = str((r.json() or {}).get("text") or "").strip()
        if not said:
            raise GateError("empty summary")
        return said
