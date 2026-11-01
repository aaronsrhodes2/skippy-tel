"""Skippy's voice out of skippy-pc's own outputs (the HDMI to the Sony, a paired Bluetooth stereo) as well as, or instead of, the room box's speaker.

Why: the box's speaker is small, and the PC already has the clip: no Wi-Fi hop, no fetch and buffering on the box, and the hub knows exactly when the
sound starts. Each output has a name to find in Windows' sound devices (a part of the name), a start delay so the outputs line up (a Bluetooth stereo
is ~200 ms late, the box ~600 ms), and ducks whatever else is playing on the PC while Skippy talks (Bilby in a browser, a game).

    SATELLITE_OUT=office=pod+hdmi+bt         per room: any of pod (the box's own speaker), hdmi, bt   (default: pod)
    SATELLITE_OUT_HDMI=NVIDIA High Definition        a part of the Windows output's name
    SATELLITE_OUT_BT=Enclave Audio
    SATELLITE_OUT_DELAYS=hdmi=0.5,bt=0.4,pod=0       seconds to wait before starting each, to line them up by ear
    SATELLITE_ONE_VOICE=1                            (default) a room with a pod AND a PC output speaks from the PC output alone while it
                                                     is open: the pod is the mic, the face and the lips, and the voice only as the fallback
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

    def __init__(self, router, kinds: list[str], log=print, duck_others: bool = True, decoder=None, sole: bool = False):
        self.router, self.kinds, self.log, self.duck_others = router, set(kinds), log, duck_others
        self.sole = sole                                           # the room's ONE voice while open: the pod stays silent (hub.Room._out)
        self.kind = "+".join(kinds)
        self.decoder = decoder or decode_cached
        self.talking_until = 0.0                                   # (monotonic) when the outputs fall silent: the room stays deaf until then (hub.Room.feed)
        self.probe = False                                         # the next clip is a probe of a silent output (sync.Calibrator.probe_due)
        self.last_heard = True                                     # False: the last probe was not heard in the room (the pod should say it)

    def _silent(self, k: str) -> bool:
        cal = getattr(self.router, "calibrator", None)
        return bool(cal is not None and hasattr(cal, "silent") and cal.silent(k))

    def available(self):
        """Open and heard: the room's voice. Open but taken for off (the projector, at night: sync.py) is not, except once per PROBE_S."""
        if not any(self.router.available(k) for k in self.kinds):
            return None
        if any(not self._silent(k) for k in self.kinds):
            return True
        if any(self.router.calibrator.probe_due(k) for k in self.kinds):
            self.probe = True
            return True
        return None

    async def play(self, mp3: bytes, gain: float = 1.0, on_sound=None) -> bool:
        """Play it on the outputs; True when it started (False: dropped, never late). `on_sound(at, seconds)`, at once when it starts: when it
        sounds in the room (time.monotonic: the router's queue, the stream's latency and the measured rest, sync.py), for the pod's drawn lips."""
        probe, self.probe, self.last_heard = self.probe, False, True
        targets = self.kinds if probe else {k for k in self.kinds if not self._silent(k)}
        if not targets:
            return False                                           # taken for off: nobody would hear it (and its copy would confuse sync.py)
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
        started = False
        if ducker:
            await asyncio.to_thread(ducker.duck)
        try:
            p = self.router.play(router_mod.ClipSource(pcm), layer="voice", targets=targets)
            started = await asyncio.to_thread(_wait_started, p, router_mod.STALE_VOICE_S + 1.0)
            if started and p.marks:
                at = sound_at(self.router, p.marks)
                self.talking_until = max(self.talking_until, at + len(pcm) / RATE + ECHO_TAIL_S)
                if on_sound is not None:
                    on_sound(at, len(pcm) / RATE)
            await asyncio.to_thread(p.finished.wait, len(pcm) / RATE + 5)
            await asyncio.sleep(max((self.router.sinks[k].delay_s for k in self.kinds if k in self.router.sinks), default=0))
            cal = getattr(self.router, "calibrator", None)
            if cal is not None and p.marks and probe:              # a probe: was it heard? (the hub has the pod say it when not)
                await asyncio.to_thread(cal.measure, pcm, dict(p.marks))
                self.last_heard = not any(self._silent(k) for k in targets)
            elif cal is not None and p.marks:                      # how far each copy landed from the pod's (sync.py), in the background
                asyncio.ensure_future(asyncio.to_thread(cal.measure, pcm, dict(p.marks)))
        finally:
            if ducker:
                await asyncio.to_thread(ducker.restore)
        return bool(started)


SOUND_EXTRA_S = 0.05                                               # past the stream's own latency, until sync.py has measured the output


def _wait_started(p, timeout: float) -> bool:
    import time                                                    # noqa: PLC0415
    end = time.monotonic() + timeout
    while not getattr(p, "started", True) is True and not p.finished.is_set() and time.monotonic() < end:
        time.sleep(0.01)
    return bool(getattr(p, "started", True))


def sound_at(router, marks: dict) -> float:
    """When a voice clip sounds in the room (time.monotonic): its first block's push, the queue ahead of it, the stream's latency, and what the
    room's microphone measured on top (sync.Calibrator.extra: the projector's own audio delay). The earliest of its outputs."""
    cal = getattr(router, "calibrator", None)
    extra = getattr(cal, "extra", {}) if cal is not None else {}
    return min(m[0] + m[1] + m[2] + extra.get(k, SOUND_EXTRA_S) for k, m in marks.items())


def output_parts(env: dict) -> dict:
    return {"hdmi": env.get("SATELLITE_OUT_HDMI", "NVIDIA High Definition"), "bt": env.get("SATELLITE_OUT_BT", "Enclave Audio"),
            "enclave": env.get("SATELLITE_OUT_ENCLAVE", "tcp://skippy-core:4953")}   # the Enclave on skippy-core's Bluetooth (router.NetStream)


def split_kinds(spec: str) -> list[str]:
    """Every output a spec names: "pod+enclave>hdmi" -> ["pod", "enclave", "hdmi"] ('+' all of them, '>' the first that is open)."""
    return [k.strip() for k in spec.replace(">", "+").split("+") if k.strip()]


def prefer_chains(env: dict) -> list[list[str]]:
    """Every '>' preference in SATELLITE_OUT and SKIPPY_STEREO_OUT: "pod+enclave>hdmi" -> [["enclave", "hdmi"]] (router.Router.effective)."""
    specs = [part.split("=", 1)[1] for part in (env.get("SATELLITE_OUT") or "").split(";") if "=" in part]
    specs.append(env.get("SKIPPY_STEREO_OUT") or "")
    chains = []
    for spec in specs:
        for group in spec.split("+"):
            chain = [k.strip() for k in group.split(">") if k.strip()]
            if len(chain) > 1 and chain not in chains:
                chains.append(chain)
    return chains


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
    return split_kinds(want)


def one_voice(env: dict) -> bool:
    """SATELLITE_ONE_VOICE (default on; the Captain, 2026-10-31: "it's too hard to sync these wimpy hubs"): a room with a pod AND a PC output
    speaks from the PC output alone while it is open; the pod stays the mic, the face and the lips, and speaks only when no PC output plays."""
    return env.get("SATELLITE_ONE_VOICE", "1").lower() not in ("0", "off", "no")


def announce_kinds(env: dict) -> list[str]:
    """SATELLITE_ANNOUNCE_OUT=enclave (2026-11-01): outputs in a place with no pod and no microphone (the Enclave upstairs) that play every
    room's announcements: the routines, timers, messages typed in the portal. Never the replies of a conversation."""
    return [k for k in split_kinds(env.get("SATELLITE_ANNOUNCE_OUT") or "") if k in output_parts(env)]


def announce_outputs(env: dict, room: str, log=print) -> list:
    """The announcement outputs for `room` (those it doesn't already play on), through the router. sync.py never measures them (no microphone
    hears them), so they are never taken for off: an announcement is sent whenever the output is open (the speaker on)."""
    kinds = [k for k in announce_kinds(env) if k not in room_kinds(env, room)]
    if not kinds or env.get("SATELLITE_ROUTER", "1") == "0":
        return []
    import router as router_mod                                    # noqa: PLC0415
    return [RoutedOut(router_mod.shared(env, log), kinds, log=log, duck_others=False)]


def parse_outputs(env: dict, room: str, log=print) -> tuple[bool, list]:
    """(play on the box's own speaker?, the PC outputs) for a room from the vault settings; the default is the box only.
    The PC outputs go through the house's audio router (router.py) unless SATELLITE_ROUTER=0, which keeps the old one-PcOut-per-output path."""
    kinds = room_kinds(env, room)
    parts, delays = output_parts(env), output_delays(env)
    pc_kinds = [k for k in kinds if k in parts]
    if pc_kinds and env.get("SATELLITE_ROUTER", "1") != "0":
        import router as router_mod                                # noqa: PLC0415
        sole = "pod" in kinds and one_voice(env)
        return "pod" in kinds, [RoutedOut(router_mod.shared(env, log), pc_kinds, log=log, sole=sole)]
    pcs = [PcOut(k, parts[k], delays.get(k, 0.0), log=log) for k in pc_kinds]
    return ("pod" in kinds or not pcs), pcs
