"""The sound hardware: the microphone in, the speaker out. sounddevice (PortAudio) and ffmpeg are imported or run only here, and only when used,
so everything else in this package is testable without any audio device."""
from __future__ import annotations

import math
import queue
import subprocess

RATE = 16000
BLOCK_MS = 50
BLOCK = RATE * BLOCK_MS // 1000


class Mic:
    """Blocks of microphone samples (mono float32, 16 kHz, 50 ms each), as (t_ms, samples)."""

    def __init__(self, device=None, gain: float = 1.0):
        import sounddevice as sd                                    # noqa: PLC0415 — only a machine with audio has it
        self.sd, self.gain, self.q = sd, gain, queue.Queue(maxsize=400)
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK, device=device, callback=self._cb)

    def _cb(self, indata, frames, time_info, status):               # noqa: ARG002
        try:
            self.q.put_nowait(indata[:, 0].copy() * self.gain)
        except queue.Full:
            pass                                                    # we were busy (speaking or thinking): the old audio is not wanted

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *a):
        self.stream.stop()
        self.stream.close()

    def blocks(self):
        t = 0
        while True:
            yield t, self.q.get()
            t += BLOCK_MS

    def flush(self):
        """Drop everything heard while we were talking (so we never answer ourselves)."""
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass


def _play(samples, rate: int, device=None) -> None:
    import sounddevice as sd                                        # noqa: PLC0415
    sd.play(samples, rate, device=device)
    sd.wait()


def play_mp3(mp3: bytes, device=None) -> None:
    """Skippy's voice (mp3 from the gate) out of the chosen speaker. ffmpeg decodes it."""
    import numpy as np                                              # noqa: PLC0415
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", "24000", "pipe:1"], input=mp3, capture_output=True, timeout=30)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("could not decode the voice")
    _play(np.frombuffer(p.stdout, dtype=np.float32), 24000, device)


def mp3_to_pcm16(mp3: bytes, rate: int = 22050) -> tuple[bytes, int]:
    """Skippy's voice as raw 16-bit mono PCM, for a room satellite (the hub sends it over Wyoming; the satellite only plays PCM)."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1"], input=mp3, capture_output=True, timeout=30)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("could not decode the voice")
    return p.stdout, rate


def local_voice_pcm16(text: str) -> tuple[bytes, int]:
    """The fallback voice for a room satellite: Windows' built-in speech, rendered to a WAV file instead of this machine's speaker."""
    import tempfile                                                 # noqa: PLC0415
    import wave                                                     # noqa: PLC0415
    from pathlib import Path                                        # noqa: PLC0415
    safe = text.replace("'", "''")[:400]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "say.wav"
        subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                        f"$s.SetOutputToWaveFile('{out}'); $s.Speak('{safe}'); $s.Dispose()"], timeout=60, capture_output=True)
        if not out.is_file():
            raise RuntimeError("the machine's own voice did not answer")
        with wave.open(str(out), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                raise RuntimeError("unexpected local voice format")
            return w.readframes(w.getnframes()), w.getframerate()


def chime(kind: str = "wake", device=None) -> None:
    """A short two-note chime made on the spot (no sound files): rising when he is heard, falling when it stops listening."""
    import numpy as np                                              # noqa: PLC0415
    rate = 24000
    notes = {"wake": (660, 880), "done": (880, 520), "timer": (880, 880, 880)}.get(kind, (660,))
    out = []
    for hz in notes:
        n = int(rate * 0.11)
        t = np.arange(n) / rate
        env = np.minimum(1, np.minimum(t / 0.01, (t[-1] - t) / 0.05))
        out.append(0.22 * env * np.sin(2 * math.pi * hz * t))
    _play(np.concatenate(out).astype(np.float32), rate, device)


def speak_local(text: str) -> None:
    """The fallback voice when Skippy's own is out of budget or unreachable: Windows' built-in speech (the machine's default speaker)."""
    safe = text.replace("'", "''")[:400]
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", f"Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{safe}')"],
                   timeout=60, capture_output=True)


def list_devices() -> str:
    import sounddevice as sd                                        # noqa: PLC0415
    lines = []
    for i, d in enumerate(sd.query_devices()):
        kind = ("IN " if d["max_input_channels"] else "   ") + ("OUT" if d["max_output_channels"] else "   ")
        lines.append(f"{i:3} {kind} {d['name']}")
    return "\n".join(lines) + f"\n(default input/output: {sd.default.device})"
