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
ECHO_TAIL_S = 0.6                                                  # a stereo's own buffering and the room's echo, after the clip's last sample


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


_PCM: "collections.OrderedDict[bytes, object]" = None             # sha1(mp3) -> its decoded sound: each clip is decoded once (prepare, decode_cached)
_PCM_MAX = 48


def _cache():
    global _PCM
    if _PCM is None:
        import collections                                         # noqa: PLC0415
        _PCM = collections.OrderedDict()
    return _PCM


def _key(mp3: bytes) -> bytes:
    import hashlib                                                 # noqa: PLC0415
    return hashlib.sha1(mp3).digest()


def _remember(mp3: bytes, pcm) -> None:
    c = _cache()
    c[_key(mp3)] = pcm
    c.move_to_end(_key(mp3))
    while len(c) > _PCM_MAX:
        c.popitem(last=False)


def decode_cached(mp3: bytes, gain: float = 1.0):
    """decode(), once per clip: a clip prepare() made, or a pre-recorded quip played before, is not decoded again."""
    pcm = _cache().get(_key(mp3))
    if pcm is None:
        pcm = decode(mp3)
        _remember(mp3, pcm)
    return pcm if gain == 1.0 else (pcm * gain).astype("float32")


def prepare(mp3: bytes, run=None, decoder=None) -> bytes:
    """The audio router's one pass over a reply, on skippy-pc: decode it ONCE (the voice gate's MP3 pieces joined end to end, one header each),
    keep that sound for the PC outputs (decode_cached finds it), and encode the pod's copy from the same sound as ONE clean MP3 stream (the pod's
    decoder stops at a second piece's header: 2026-10-31, silent mid-answer). Returns the pod's MP3; a single-piece clip is returned as it is."""
    if mp3.count(b"Info") + mp3.count(b"Xing") <= 1:
        return mp3
    try:
        pcm = (decoder or decode)(mp3)
        got = (run or subprocess.run)(["ffmpeg", "-v", "error", "-nostdin", "-f", "f32le", "-ar", str(RATE), "-ac", "2", "-i", "pipe:0",
                                      "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "pipe:1"],
                                     input=pcm.tobytes(), capture_output=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return mp3
    if not got:
        return mp3
    _remember(got, pcm)                                            # the PC outputs play the very same sound, not a second decode
    return got


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


class RoutedOut:
    """Skippy's voice on ALL of a room's PC outputs through the house's audio router (router.py): decoded once, mixed over a book or music
    (which ducks under it instead of stopping), on outputs that stay open. Same `play` as PcOut, so the hub's Room uses it unchanged."""

    def __init__(self, router, kinds: list[str], log=print, duck_others: bool = True, decoder=None):
        self.router, self.kinds, self.log, self.duck_others = router, set(kinds), log, duck_others
        self.kind = "+".join(kinds)
        self.decoder = decoder or decode_cached
        self.talking_until = 0.0                                   # (monotonic) when the outputs fall silent: the room stays deaf until then (hub.Room.feed)

    def available(self):
        return any(self.router.available(k) for k in self.kinds) or None

    async def play(self, mp3: bytes, gain: float = 1.0) -> bool:
        try:
            pcm = await asyncio.to_thread(self.decoder, mp3, gain)
        except Exception as e:                                     # noqa: BLE001
            self.log(f"(output {self.kind}: could not decode: {type(e).__name__})")
            return False
        import router as router_mod                                # noqa: PLC0415
        import time                                                # noqa: PLC0415
        lag = max((self.router.sinks[k].delay_s for k in self.kinds if k in self.router.sinks), default=0)
        self.talking_until = max(self.talking_until, time.monotonic() + len(pcm) / RATE + lag + ECHO_TAIL_S)
        ducker = Ducker() if self.duck_others else None            # other programs (Bilby in a browser, a game) still duck under him
        if ducker:
            await asyncio.to_thread(ducker.duck)
        try:
            p = self.router.play(router_mod.ClipSource(pcm), layer="voice", targets=self.kinds)
            await asyncio.to_thread(p.finished.wait, len(pcm) / RATE + 5)
            await asyncio.sleep(max((self.router.sinks[k].delay_s for k in self.kinds if k in self.router.sinks), default=0))
        finally:
            if ducker:
                await asyncio.to_thread(ducker.restore)
        return True


def output_parts(env: dict) -> dict:
    return {"hdmi": env.get("SATELLITE_OUT_HDMI", "NVIDIA High Definition"), "bt": env.get("SATELLITE_OUT_BT", "Enclave Audio")}


def output_delays(env: dict) -> dict:
    delays = {}
    for pair in (env.get("SATELLITE_OUT_DELAYS") or "pod=0.6,bt=0.4,hdmi=0.5").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            delays[k.strip()] = float(v)
    return delays


def room_kinds(env: dict, room: str) -> list[str]:
    want = "pod"
    for part in (env.get("SATELLITE_OUT") or "").split(";"):
        if part.strip().startswith(room + "="):
            want = part.split("=", 1)[1].strip()
    return [k.strip() for k in want.split("+") if k.strip()]


def parse_outputs(env: dict, room: str, log=print) -> tuple[bool, list]:
    """(play on the box's own speaker?, the PC outputs) for a room from the vault settings; the default is the box only.
    The PC outputs go through the house's audio router (router.py) unless SATELLITE_ROUTER=0, which keeps the old one-PcOut-per-output path."""
    kinds = room_kinds(env, room)
    parts, delays = output_parts(env), output_delays(env)
    pc_kinds = [k for k in kinds if k in parts]
    if pc_kinds and env.get("SATELLITE_ROUTER", "1") != "0":
        import router as router_mod                                # noqa: PLC0415
        return "pod" in kinds, [RoutedOut(router_mod.shared(env, log), pc_kinds, log=log)]
    pcs = [PcOut(k, parts[k], delays.get(k, 0.0), log=log) for k in pc_kinds]
    return ("pod" in kinds or not pcs), pcs
