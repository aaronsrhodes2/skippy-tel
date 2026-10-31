"""Skippy's voice out of skippy-pc's own outputs (the HDMI to the Sony, a paired Bluetooth stereo) as well as, or instead of, the room box's speaker.

Why: the box's speaker is small, and the PC already has the clip: no Wi-Fi hop, no fetch and buffering on the box, and the hub knows exactly when the
sound starts. Each output has a name to find in Windows' sound devices (a part of the name), a start delay so the outputs line up (a Bluetooth stereo
is ~200 ms late, the box ~600 ms), and ducks whatever else is playing on the PC while Skippy talks (Bilby in a browser, a game).

    SATELLITE_OUT=office=pod+hdmi+bt         per room: any of pod (the box's own speaker), hdmi, bt   (default: pod)
    SATELLITE_OUT_HDMI=NVIDIA High Definition        a part of the Windows output's name
    SATELLITE_OUT_BT=Enclave Audio
    SATELLITE_OUT_DELAYS=hdmi=0.5,bt=0.4,pod=0       seconds to wait before starting each, to line them up by ear
"""
from __future__ import annotations

import asyncio
import subprocess

RATE = 48000


def find_device(part: str):
    """(index, name) of the WASAPI (else any) output whose name contains `part`, or None (a Bluetooth stereo that is off is simply not there)."""
    import sounddevice as sd                                   # noqa: PLC0415
    best = None
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0 and part.lower() in d["name"].lower():
            api = sd.query_hostapis(d["hostapi"])["name"]
            if best is None or "WASAPI" in api:
                best = (i, d["name"])
    return best


def decode(mp3: bytes, gain: float = 1.0):
    import numpy as np                                         # noqa: PLC0415
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", "pipe:0", "-f", "f32le", "-ac", "2", "-ar", str(RATE), "pipe:1"],
                         input=mp3, capture_output=True, timeout=20).stdout
    return (np.frombuffer(raw, dtype="<f4").reshape(-1, 2) * gain).astype("float32")


class Ducker:
    """Lowers the other programs' volume while Skippy talks, then puts it back (Windows per-app volumes through pycaw)."""
    def __init__(self, factor: float = 0.25):
        self.factor, self.saved = factor, []

    def duck(self) -> None:
        try:
            from pycaw.pycaw import AudioUtilities                # noqa: PLC0415
            import os                                              # noqa: PLC0415
            for s in AudioUtilities.GetAllSessions():
                if s.Process is not None and s.Process.pid != os.getpid() and s.SimpleAudioVolume is not None:
                    v = s.SimpleAudioVolume.GetMasterVolume()
                    self.saved.append((s, v))
                    s.SimpleAudioVolume.SetMasterVolume(v * self.factor, None)
        except Exception:                                          # noqa: BLE001 — no ducking is better than no voice
            self.saved = []

    def restore(self) -> None:
        for s, v in self.saved:
            try:
                s.SimpleAudioVolume.SetMasterVolume(v, None)
            except Exception:                                      # noqa: BLE001 — the program closed meanwhile
                pass
        self.saved = []


class PcOut:
    def __init__(self, kind: str, part: str, delay_s: float = 0.0, log=print, duck: bool = True):
        self.kind, self.part, self.delay_s, self.log, self.duck = kind, part, delay_s, log, duck

    def available(self):
        try:
            return find_device(self.part)
        except Exception:                                          # noqa: BLE001
            return None

    async def play(self, mp3: bytes, gain: float = 1.0) -> bool:
        dev = self.available()
        if dev is None:
            self.log(f"(output {self.kind}: no sound device matching {self.part!r}: skipped)")
            return False
        try:
            data = await asyncio.to_thread(decode, mp3, gain)
        except Exception as e:                                     # noqa: BLE001
            self.log(f"(output {self.kind}: could not decode: {type(e).__name__})")
            return False
        if self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        ducker = Ducker() if self.duck else None

        def run() -> None:
            import sounddevice as sd                               # noqa: PLC0415
            if ducker:
                ducker.duck()
            try:
                sd.play(data, RATE, device=dev[0], blocking=True)
            finally:
                if ducker:
                    ducker.restore()
        try:
            await asyncio.to_thread(run)
            return True
        except Exception as e:                                     # noqa: BLE001
            self.log(f"(output {self.kind} {dev[1]!r}: {type(e).__name__}: {str(e)[:80]})")
            return False


def parse_outputs(env: dict, room: str, log=print) -> tuple[bool, list[PcOut]]:
    """(play on the box's own speaker?, the PC outputs) for a room from the vault settings; the default is the box only."""
    want = "pod"
    for part in (env.get("SATELLITE_OUT") or "").split(";"):
        if part.strip().startswith(room + "="):
            want = part.split("=", 1)[1].strip()
    kinds = [k.strip() for k in want.split("+") if k.strip()]
    delays = {}
    for pair in (env.get("SATELLITE_OUT_DELAYS") or "pod=0.6,bt=0.4,hdmi=0.5").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            delays[k.strip()] = float(v)
    parts = {"hdmi": env.get("SATELLITE_OUT_HDMI", "NVIDIA High Definition"), "bt": env.get("SATELLITE_OUT_BT", "Enclave Audio")}
    pcs = [PcOut(k, parts[k], delays.get(k, 0.0), log=log) for k in kinds if k in parts]
    return ("pod" in kinds or not pcs), pcs
