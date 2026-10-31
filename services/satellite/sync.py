"""Skippy's voice on the pod and on the PC's own outputs (the projector's HDMI), lined up by ear, by the hub itself (the Captain, 2026-10-31).

A fixed delay per output (SATELLITE_OUT_DELAYS) was never right for long: the projector has its own audio latency, the pod buffers what it fetches,
and the Captain heard the pod 0.3-0.6 s ahead. So the hub listens: skippy-pc's own microphone (a USB mic in the office, SATELLITE_SYNC_MIC) is
kept open into a ring of the last RING_S seconds, in memory only, never stored or sent anywhere. After each voice clip the router played, that
stretch of the room is deconvolved by the clip itself (the voice band): every copy of the clip the room played is a peak.

  * which peak is which: the router knows when the clip's first block went into each output's queue and how much was queued ahead of it
    (router.Play.marks), so each output's copy has an expected arrival; the projector's own latency on top of that is learned (`extra`).
    The pod's copy is the strongest peak that is no PC output's.
  * the correction: the output's delay moves by GAIN of the measured offset (smoothed: one odd reply can't throw it), clamped to
    [MIN_DELAY, MAX_DELAY]; an offset over MAX_OFFSET is ignored as a mismeasurement. The router uses the new delay from the next clip on
    (router.Sink.realign) and the hub keeps it in state/audio-sync.json for its next start.

    SATELLITE_SYNC_MIC=CMTECK        a part of the PC microphone's name; 0 turns the measuring off (the delays then stay as they are)

Every measurement is one line in the hub log: "(sync: hdmi +312 ms behind the pod; delay 0.500 -> 0.313 s)".
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

RATE = 48000                     # the router's rate
FS = 16000                       # what the correlation runs at
RING_S = 90.0
GAIN = 0.6                       # the share of a measured offset taken off the delay at once
FINE_GAIN, FINE_S = 0.3, 0.10    # under FINE_S the rest is the pod's own start jitter (+-40 ms live, 2026-10-31): smaller steps
MIN_DELAY, MAX_DELAY = 0.0, 2.0
MAX_OFFSET = 1.5
DEAD_BAND = 0.010
SEARCH_BEFORE, SEARCH_AFTER = 12.0, 12.0   # seconds around the router's first push in which a copy can arrive (2026-10-31: the Captain heard
                                           # one copy ~8 s after the other, which a +-2 s window never saw)
PEAK_MIN = 8.0                   # a peak this many times the response's noise floor is a copy of the clip
PEAK_REL = 0.06                  # and at least this fraction of the strongest copy
ECHO_S = 0.025                   # a copy this close to an output's is as likely its reflection off a wall as the pod
TELL_APART_S = 0.015             # two copies whose distances from where an output's was due differ by less than this: can't say which is which
ASSIGN_S = 0.30                  # an output's copy lands within this of where the router expected it (plus the learned extra)
STATE = Path(__file__).resolve().parents[2] / "state" / "audio-sync.json"


def _np():
    import numpy as np                                             # noqa: PLC0415
    return np


def load(path: Path = STATE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def saved_delays(path: Path = STATE) -> dict:
    return {k: float(v["delay"]) for k, v in (load(path).get("outputs") or {}).items() if isinstance(v, dict) and "delay" in v}


def to_fs(pcm48):
    """The router's 48 kHz stereo clip as 16 kHz mono (a 3-sample average, then every third: plenty for a voice band correlation)."""
    np = _np()
    mono = pcm48.mean(axis=1) if pcm48.ndim == 2 else pcm48
    n = len(mono) // 3 * 3
    return mono[:n].reshape(-1, 3).mean(axis=1).astype("float32")


def correlate(mic, ref):
    """The room's response to the clip, the voice band only: |h|[lag] for lag = where in `mic` a copy of the clip starts (0..len(mic)-1).
    A regularised deconvolution (the room divided by the clip), not GCC-PHAT: whitening the room's own spectrum, which holds two copies, puts
    false peaks at every multiple of their spacing."""
    np = _np()
    n = 1
    while n < len(mic) + len(ref):
        n *= 2
    m, r = np.fft.rfft(mic, n), np.fft.rfft(ref, n)
    power = np.abs(r) ** 2
    g = m * np.conj(r) / (power + 0.05 * float(power.mean()) + 1e-12)
    f = np.fft.rfftfreq(n, 1 / FS)
    g[(f < 200) | (f > 4000)] = 0
    cc = np.abs(np.fft.irfft(g, n))[: len(mic)]
    return cc


def peaks(cc, count: int = 4, apart_s: float = 0.01):
    """[(lag, height over the noise floor)] of the strongest separate peaks, strongest first, only those over PEAK_MIN."""
    np = _np()
    cc = cc.copy()
    floor = float(np.median(cc)) or 1e-9
    out, gap = [], max(1, int(apart_s * FS))
    for _ in range(count):
        i = int(np.argmax(cc))
        h = float(cc[i]) / floor
        if h < PEAK_MIN or (out and h < PEAK_REL * out[0][1]):
            break
        out.append((i, h))
        cc[max(0, i - gap): i + gap] = 0
    return out


class MicRing:
    """The PC microphone, kept open into a ring of the last RING_S seconds (16 kHz mono), each block stamped with the clock it arrived by."""

    def __init__(self, part: str, log=print, clock=time.monotonic, open_stream=None):
        self.part, self.log, self.clock = part, log, clock
        self.open_stream = open_stream or _sd_input
        self.blocks: list = []                                     # [(clock at the block's first sample, samples)]
        self.lock = threading.Lock()
        self.stream = None
        self.name = ""

    def start(self) -> bool:
        """Open it (the audio router calls this from its device scan, every RESCAN_S while it is not open; a failure is said once)."""
        try:
            self.stream, self.name = self.open_stream(self.part, self._callback)
        except Exception as e:                                     # noqa: BLE001 — no microphone: no measuring, the delays stay
            self.stream = None
            if not getattr(self, "_said_missing", False):
                self.log(f"(sync: no microphone matching {self.part!r}: {type(e).__name__}: {str(e)[:80]})")
            self._said_missing = True
            return False
        self._said_missing = False
        self.log(f"(sync: listening to the room through {self.name!r}, last {RING_S:.0f} s in memory only)")
        return True

    def _callback(self, indata, frames, _time, _status) -> None:
        self.add(self.clock() - frames / RATE, indata[:, 0].copy())

    def add(self, t0: float, samples48) -> None:
        samples = to_fs(samples48)
        with self.lock:
            self.blocks.append((t0, samples))
            while self.blocks and self.blocks[0][0] < t0 - RING_S:
                self.blocks.pop(0)

    def slice(self, start: float, end: float):
        """(samples, clock of the first) from `start` to `end`, or (None, 0) if the ring doesn't cover it."""
        np = _np()
        with self.lock:
            got = [(t, s) for t, s in self.blocks if t + len(s) / FS > start and t < end]
        if not got:
            return None, 0.0
        t0 = got[0][0]
        return np.concatenate([s for _, s in got]), t0


class Calibrator:
    def __init__(self, router, kinds, mic: MicRing, log=print, path: Path = STATE, clock=time.monotonic):
        self.router, self.kinds, self.mic, self.log, self.path, self.clock = router, list(kinds), mic, log, path, clock
        saved = load(path).get("outputs") or {}
        self.extra = {k: float((saved.get(k) or {}).get("extra", 0.1)) for k in self.kinds}   # each output's own latency past its stream's (the projector's)
        self.lock = threading.Lock()

    def measure(self, pcm48, marks: dict) -> dict | None:
        """After a clip has played: where each copy landed, and the corrected delays. None when nothing could be told."""
        try:
            return self._measure(pcm48, marks)
        except Exception as e:                                     # noqa: BLE001 — a measurement never takes the voice down
            self.log(f"(sync: {type(e).__name__}: {str(e)[:100]})")
            return None

    def _measure(self, pcm48, marks: dict) -> dict | None:
        marks = {k: m for k, m in marks.items() if k in self.kinds}
        if not marks:
            return None
        ref = to_fs(pcm48)
        if len(ref) < FS // 2 or not ref.any():
            return None
        t_push = min(m[0] for m in marks.values())
        wait = t_push + len(ref) / FS + SEARCH_AFTER - self.clock()
        if wait > 0:
            time.sleep(wait)                                        # the last copy's tail is still in the air (this runs in a thread)
        room, t0 = self.mic.slice(t_push - SEARCH_BEFORE, t_push + len(ref) / FS + SEARCH_AFTER)
        if room is None or len(room) < len(ref):
            self.log("(sync: the microphone did not cover the clip: no measurement)")
            return None
        found = [(t0 + lag / FS, h) for lag, h in peaks(correlate(room, ref))]
        if not found:
            self.log("(sync: no copy of the clip heard in the room)")
            return None
        expected = {k: m[0] + m[1] + m[2] + self.extra[k] for k, m in marks.items()}
        mine, left, unsure = {}, list(found), set()
        for k, e in sorted(expected.items()):
            ranked = sorted(left, key=lambda p: abs(p[0] - e))
            if ranked and abs(ranked[0][0] - e) <= ASSIGN_S:
                mine[k] = ranked[0]
                left.remove(ranked[0])
                if len(ranked) > 1 and abs(ranked[1][0] - e) - abs(ranked[0][0] - e) < TELL_APART_S:
                    unsure.add(k)                                    # the pod's copy lands about as near: they are close together anyway
        near = [p for p in left if any(abs(p[0] - m[0]) <= ECHO_S for m in mine.values())]
        far = [p for p in left if p not in near]
        pod = max(far, key=lambda p: p[1], default=None)          # an output's own reflection (15:37: two copies 12 ms apart) is not the pod
        best_near = max(near, key=lambda p: p[1], default=None)
        if pod is None or (best_near is not None and pod[1] < 0.3 * best_near[1]):
            pod = best_near or pod                                # nothing clearly apart: the pod is right beside it
        result = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "outputs": {}}
        if pod is None or not mine:
            heard = ", ".join(f"{k} {mine[k][1]:.0f}x" for k in mine) or "no PC output"
            due = min(expected.values())
            where = ", ".join(f"{(t - due) * 1000:+.0f} ms {h:.0f}x" for t, h in found)
            self.log(f"(sync: heard {heard}{', the pod' if pod else ', not the pod'}: nothing to line up; copies at {where} from where the output's was due)")
            return None
        for k, (t, h) in mine.items():
            off = t - pod[0]                                        # positive: this output is behind the pod
            sink = self.router.sinks.get(k)
            if sink is None:
                continue
            old = sink.delay_s
            if abs(off) > 2 * TELL_APART_S and k not in unsure:     # clearly apart: this is surely the output's copy, so learn its own latency
                with self.lock:
                    self.extra[k] = 0.3 * self.extra[k] + 0.7 * (t - (marks[k][0] + marks[k][1] + marks[k][2]))
            new = old
            if abs(off) > MAX_OFFSET:
                who = "the pod's" if off < 0 else f"the {k}'s"
                note = f"{who} copy came {abs(off):.1f} s late: no delay can fix that, unchanged"
            elif k in unsure:
                note = f"too close to tell which copy is which (under {abs(off) * 1000:.0f} ms): unchanged"
            elif abs(off) < DEAD_BAND:
                note = "together"
            else:
                new = min(MAX_DELAY, max(MIN_DELAY, old - (GAIN if abs(off) > FINE_S else FINE_GAIN) * off))
                sink.delay_s = new                                  # the next clip gets it (router.Sink.realign)
                note = f"delay {old:.3f} -> {new:.3f} s"
            self.log(f"(sync: {k} {off * 1000:+.0f} ms {'behind' if off >= 0 else 'ahead of'} the pod (peaks pod {pod[1]:.0f}x, {k} {h:.0f}x); {note})")
            result["outputs"][k] = {"offset": round(off, 4), "delay": round(new, 4), "extra": round(self.extra[k], 4)}
        self._save(result)
        return result

    def _save(self, result: dict) -> None:
        data = load(self.path)
        outs = data.setdefault("outputs", {})
        for k, v in result["outputs"].items():
            outs[k] = {"delay": v["delay"], "extra": v["extra"]}
        hist = (data.get("history") or [])[-29:]
        hist.append(result)
        data["history"] = hist
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError as e:
            self.log(f"(sync: could not save {self.path.name}: {e})")


def _sd_input(part: str, callback):
    import sounddevice as sd                                       # noqa: PLC0415
    best = None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and part.lower() in d["name"].lower():
            if best is None or "WASAPI" in sd.query_hostapis(d["hostapi"])["name"]:
                best = (i, d["name"])
    if best is None:
        raise LookupError("not plugged in")
    s = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", device=best[0], callback=callback, blocksize=int(RATE * 0.05))
    s.start()
    return s, best[1]


def pod_rooms_kinds(env: dict) -> set[str]:
    """The PC outputs that play in a room together with its pod: the ones to line up."""
    out = set()
    for part in (env.get("SATELLITE_OUT") or "").split(";"):
        if "=" in part:
            kinds = {k.strip() for k in part.split("=", 1)[1].split("+")}
            if "pod" in kinds:
                out |= kinds - {"pod"}
    return out


def for_router(router, env: dict, log=print) -> Calibrator | None:
    part = env.get("SATELLITE_SYNC_MIC", "CMTECK")
    kinds = pod_rooms_kinds(env) & set(router.sinks)
    if not kinds or part in ("", "0"):
        return None
    return Calibrator(router, kinds, MicRing(part, log=log), log=log)   # the router's device scan opens the microphone
