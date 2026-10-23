"""Skippy's ears and mouth for the portal's chats (/chat and the personal Skippy on /me), through skippy-pc's voice-gate.

The audio never passes through here. skippy-pc's voice-gate (services/voice-gate/gate.py) takes a recording to Whisper and
speaks in Skippy's voice (ElevenLabs, key kept at home), on the internet through Tailscale Funnel port 10000, and only for a pass
this module signs. The key comes from the home token's sha256, like the music and video gates', with its own label and signed
text so none of the three passes opens another. The home reporter says where the gate is and whether it is ready ("voice_gate").

Which voice a reply is spoken in is decided HERE, on the server, and signed into the pass (`voice_for`), never taken from the page:

    stock       Kokoro-82M, a stock voice, rendered at home (tts-studio). Everyone allowed to chat: the Public mode.
    skippy      the cloned Skippy voice, F5-TTS, rendered at home. The Captain, in the Private mode (and whenever no mode is given).
    elevenlabs  the cloud Skippy voice (ElevenLabs via flask-api). The Captain, only when the page says the chat is in Auto mode.

The cloned voices are for personal use only, so nobody but the Captain is ever signed one, whatever the page asks for. Every voice
pass also hears (the microphone). The older two scopes still sign as before: "listen" (hears only) and "speak" (= elevenlabs), so a
page that hasn't moved to voices keeps working; when the gate can't speak, the page reads aloud with the device's own voice.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time

PASS_S = 8 * 3600
PASS_BUCKET_S = 6 * 3600
# Tailscale Funnel: https only, a *.ts.net name, and no port or the one Funnel port this gate uses (10000).
GATE_URL = re.compile(r"https://[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net(:10000)?")


def gate_key(token_sha256: str) -> bytes:
    """Must match services/voice-gate/gate.py gate_key()."""
    return hmac.new(token_sha256.encode(), b"voice-gate-v1", hashlib.sha256).digest()


VOICES = ("stock", "skippy", "elevenlabs")
MODES = ("public", "private", "auto")


def sign(key: bytes, exp: int, scope: str = "listen") -> str:
    """scope: "listen", "speak" or a voice name. Must match services/voice-gate/gate.py sign()."""
    text = f"voice-speak:{exp}" if scope == "speak" else f"voice-tts-{scope}:{exp}" if scope in VOICES else f"voice:{exp}"
    return f"{exp}.{hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:32]}"


def voice_for(is_captain: bool, mode: str | None = None) -> str:
    """The voice a person gets in a chat mode: anyone but the Captain gets stock, whatever the mode says; the Captain gets
    ElevenLabs only in Auto, stock if he asks for Public, else the local Skippy clone."""
    if not is_captain:
        return "stock"
    mode = (mode or "").strip().lower()
    return "elevenlabs" if mode == "auto" else "stock" if mode == "public" else "skippy"


class Voice:
    def __init__(self, home, clock=time.time, gate_url_re=None):
        self.home, self.clock = home, clock
        self.gate_url_re = gate_url_re or GATE_URL          # tests/dev harnesses may point it at a gate on this machine

    def gate_url(self) -> str | None:
        info = ((self.home.report or {}).get("capabilities") or {}).get("voice_gate")
        url = info.get("url") if isinstance(info, dict) else None
        if not isinstance(url, str):
            return None
        url = url.rstrip("/")
        return url if self.gate_url_re.fullmatch(url) else None

    def gate(self, speak: bool = False, voice: str | None = None) -> dict:
        """Where the voice service is and a pass for it, or why it can't be used right now (one plain sentence).

        voice (from voice_for(), never straight from a request): a pass that hears and speaks in that voice, {"voice": ...,
        "speak": True}. Without it, the older passes: speak=False hears only, speak=True is the ElevenLabs voice."""
        if voice is not None and voice not in VOICES:
            raise ValueError(f"unknown voice {voice!r}")
        stored = self.home._stored_hash()
        if not stored:
            return {"ok": False, "why": "skippy-pc isn't paired with the portal."}
        if not self.home.online():
            return {"ok": False, "why": "skippy-pc isn't reporting in, so the voice is out of reach."}
        url = self.gate_url()
        if not url or not self.home.can("voice_gate"):
            return {"ok": False, "why": "skippy-pc is up, but its voice gate (or Whisper behind it) isn't answering."}
        exp = (int(self.clock()) // PASS_BUCKET_S + 1) * PASS_BUCKET_S + PASS_S
        if voice:
            return {"ok": True, "url": url, "pass": sign(gate_key(stored), exp, voice), "exp": exp, "speak": True, "voice": voice}
        return {"ok": True, "url": url, "pass": sign(gate_key(stored), exp, "speak" if speak else "listen"),
                "exp": exp, "speak": speak, "voice": "elevenlabs" if speak else None}
