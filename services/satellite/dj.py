"""Bilby's DJ mode (the Captain, 2026-10-31): his own library mixed like a DJ set, track into track, on the house stereo through the audio router.

Spotify's "Mix" made from his own Traktor work, all on skippy-pc:

  * ANALYSIS: every track's BPM, beat grid (the first beat) and key come from his Traktor collection (collection.nml, kept by music-collection's
    pipeline: stage5_traktor.py, the autocue's "Beat" cue). A track Traktor never saw is analysed once with librosa and cached (state/dj/).
  * THE MIX: the outgoing track leaves on a phrase boundary (32, else 16, else 8 bars' worth of beats from its first beat) that still has
    music after it; the incoming one comes in on its first beat. The incoming track is time-stretched (ffmpeg's rubberband, pitch kept) to the
    outgoing tempo, the two grids are lined up on the audio itself (each grid nudged to the onsets within +-60 ms), they cross over OVERLAP beats
    with an EQ swap (the incoming bass stays out until halfway, then the basses trade places in one beat), and the incoming track then glides back
    to its own tempo over RAMP beats. The whole crossing is rendered ahead of time, while the outgoing track plays, and fed to the router.
  * A PLAIN CUT (the old way: one ends, the next starts) when the keys clash (Camelot distance CLASH or more), the tempos are more than
    MAX_STRETCH apart (half and double time count), or either track is unknown.
  * ORDER: a shuffle in DJ mode is a harmonic, tempo-matched walk through the library (walk()), his higher-rated tracks a little more often.

Gapless: a track, its crossing and the next track are pieces of one ChainSource, one router play on the `media` layer, so Skippy's voice still
ducks it and nothing waits a block between pieces.

    BILBY_DJ=on|off              the vault's default; "Skippy, DJ mode on/off" overrides it (kept in state/dj.json)
    BILBY_MUSIC_ROOT             the library on this PC (default D:/Aaron/Music/corrected_music, what the music gate serves)
    BILBY_TRAKTOR_NML            Traktor's collection (default music-collection/corrected_traktor/collection.nml)

    python dj.py plan "<catalog path A>" "<catalog path B>"     what the mix would do
    python dj.py demo "<A>" "<B>" [--out hdmi]                  play the last bars of A into B on that output (the hub can keep running)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import subprocess
import threading
import time
import unicodedata
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

import stereo

RATE = 48000
STATE = Path(__file__).resolve().parents[2] / "state" / "dj"
DEFAULT_ROOT = "D:/Aaron/Music/corrected_music"
DEFAULT_NML = str(Path(__file__).resolve().parents[3] / "music-collection" / "corrected_traktor" / "collection.nml")

OVERLAP = 16                      # beats the two tracks play together (8 s at 120 BPM)
RAMP = 16                         # beats over which the incoming track glides back to its own tempo
RAMP_STEPS = 4
PHRASES = (32, 16, 8)             # mix-out points, in beats from the first beat, best first
MAX_STRETCH = 0.06
CLASH = 3                         # Camelot distance from which two keys don't mix
NUDGE_S = 0.15                    # how far the audio may move a grid (Traktor's autocue anchors are up to ~70 ms out on live drums)
SEAM_S = 0.008                    # the tiny crossfades between the stretched pieces
LEAD_S = 8.0                      # audio decoded ahead of the mix-out point (to line the grid up on its last bars with drums)
BASS_HZ = 180.0
MARGIN_S = 6.0                    # a mix-out point closer than this to where the track is now is too late to render for


def _np():
    import numpy as np                                             # noqa: PLC0415
    return np


def _nfc(s: str) -> str:
    s = unicodedata.normalize("NFC", s).replace("\\", "/")
    while "//" in s:
        s = s.replace("//", "/")
    return s.strip("/")


# ── keys ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def camelot(value: int) -> tuple[int, bool]:
    """Traktor's MUSICAL_KEY (0-11 C..B major, 12-23 Cm..Bm) -> (Camelot number 1-12, minor)."""
    pc, minor = value % 12, value >= 12
    n = ((5 if minor else 8) + 7 * pc) % 12
    return (n or 12), minor


def key_distance(a: tuple[int, bool] | None, b: tuple[int, bool] | None) -> int | None:
    """Steps on the Camelot wheel: 0 same key, 1 a neighbour or the relative major/minor, 2 an energy boost; None when either is unknown."""
    if not a or not b or not a[0] or not b[0]:
        return None
    d = abs(a[0] - b[0]) % 12
    return min(d, 12 - d) + (0 if a[1] == b[1] else 1)


# ── what is known about a track ───────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Track:
    path: str                     # the music gate's catalog path (relative to the library root)
    bpm: float
    first_beat: float             # seconds: the grid's anchor, bar 1 beat 1
    duration: float
    key: int = 0                  # Camelot number, 0 unknown
    minor: bool = False
    stars: int = 0
    source: str = "traktor"

    @property
    def period(self) -> float:
        return 60.0 / self.bpm

    @property
    def camelot(self) -> tuple[int, bool] | None:
        return (self.key, self.minor) if self.key else None


def parse_nml(nml_path: str, marker: str = "corrected_music") -> dict[str, Track]:
    """Every track in Traktor's collection that lives under the library root, keyed by its catalog path."""
    import xml.etree.ElementTree as ET                             # noqa: PLC0415
    out: dict[str, Track] = {}
    for _ev, e in ET.iterparse(nml_path, events=("end",)):
        if e.tag != "ENTRY":
            continue
        loc, tempo, info = e.find("LOCATION"), e.find("TEMPO"), e.find("INFO")
        try:
            if loc is None or tempo is None:
                continue
            folder = (loc.get("DIR") or "").replace("/:", "/")
            cut = folder.find(f"/{marker}/")
            if cut < 0:
                continue
            rel = _nfc(folder[cut + len(marker) + 2:] + (loc.get("FILE") or ""))
            bpm = float(tempo.get("BPM") or 0)
            if bpm <= 0:
                continue
            grid = beat = fade = None
            for cue in e.findall("CUE_V2"):
                start = float(cue.get("START") or 0) / 1000.0
                if cue.get("TYPE") == "4" and grid is None:
                    grid = start
                elif cue.get("NAME") == "Beat" and beat is None:
                    beat = start
                elif cue.get("TYPE") == "1" and fade is None:
                    fade = start
            first = next((x for x in (grid, beat, fade) if x is not None), 0.0)
            dur = float((info.get("PLAYTIME_FLOAT") or info.get("PLAYTIME") or 0) if info is not None else 0)
            mk = e.find("MUSICAL_KEY")
            key, minor = camelot(int(mk.get("VALUE"))) if mk is not None and (mk.get("VALUE") or "").isdigit() else (0, False)
            rank = int((info.get("RANKING") or 0) if info is not None else 0)
            out[rel] = Track(rel, bpm, first, dur, key, minor, min(5, rank // 51))
        except (ValueError, TypeError):
            pass
        finally:
            e.clear()
    return out


class Library:
    """Track facts by catalog path: Traktor's first, else a cached librosa analysis (made on first ask)."""

    def __init__(self, root: str = DEFAULT_ROOT, nml: str = DEFAULT_NML, state: Path = STATE, analyse=None, log=print):
        self.root, self.nml, self.state, self.log = Path(root), nml, Path(state), log
        self.analyse = analyse or analyse_file
        self._traktor: dict[str, Track] | None = None
        self._lower: dict[str, str] = {}
        self._cache: dict | None = None
        self.lock = threading.Lock()

    def local(self, rel: str) -> str | None:
        p = self.root / rel
        return str(p) if p.is_file() else None

    def traktor(self) -> dict[str, Track]:
        with self.lock:
            if self._traktor is None:
                self._traktor = self._load_traktor()
                self._lower = {k.lower(): k for k in self._traktor}
            return self._traktor

    def _load_traktor(self) -> dict[str, Track]:
        try:
            mtime = os.path.getmtime(self.nml)
        except OSError:
            return {}
        idx = self.state / "traktor-index.json"
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
            if data.get("mtime") == mtime and data.get("nml") == self.nml:
                return {k: Track(**v) for k, v in data["tracks"].items()}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        t0 = time.monotonic()
        tracks = parse_nml(self.nml)
        self.log(f"(dj: read {len(tracks)} tracks from Traktor's collection in {time.monotonic() - t0:.1f} s)")
        try:
            self.state.mkdir(parents=True, exist_ok=True)
            tmp = idx.with_suffix(".tmp")
            tmp.write_text(json.dumps({"mtime": mtime, "nml": self.nml, "tracks": {k: asdict(v) for k, v in tracks.items()}}), encoding="utf-8")
            os.replace(tmp, idx)
        except OSError:
            pass
        return tracks

    def known(self, rel: str) -> Track | None:
        """Without analysing anything (for ordering a whole library)."""
        t = self.traktor()
        k = _nfc(rel)
        hit = t.get(k) or t.get(self._lower.get(k.lower(), ""))
        if hit:
            return hit
        got = self._analysis().get(k)
        return Track(**got["track"]) if got else None

    def info(self, rel: str) -> Track | None:
        hit = self.known(rel)
        if hit:
            return hit
        path = self.local(rel)
        if not path:
            return None
        try:
            tr = self.analyse(path)
        except Exception as e:                                     # noqa: BLE001 — unknown means a plain cut, never a crash
            self.log(f"(dj: could not analyse {rel!r}: {type(e).__name__}: {str(e)[:80]})")
            return None
        if tr is None:
            return None
        tr.path = _nfc(rel)
        with self.lock:
            cache = self._analysis()
            cache[tr.path] = {"track": asdict(tr)}
            try:
                self.state.mkdir(parents=True, exist_ok=True)
                f = self.state / "analysis.json"
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(cache), encoding="utf-8")
                os.replace(tmp, f)
            except OSError:
                pass
        return tr

    def _analysis(self) -> dict:
        if self._cache is None:
            try:
                self._cache = json.loads((self.state / "analysis.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._cache = {}
        return self._cache


_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def analyse_file(path: str) -> Track | None:
    """librosa's beat tracker and a Krumhansl key estimate, for a track Traktor never analysed."""
    import librosa                                                 # noqa: PLC0415
    np = _np()
    y, sr = librosa.load(path, sr=22050, mono=True)
    if len(y) < sr * 20:
        return None
    _tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    beats = np.asarray(beats, dtype=float)
    if len(beats) < 16:
        return None
    period = float(np.median(np.diff(beats)))
    n = np.round((beats - beats[0]) / period)
    slope, icept = np.polyfit(n, beats, 1)                        # one straight grid through every beat
    first = float(icept % slope)
    chroma = librosa.feature.chroma_cqt(y=y[: sr * 120], sr=sr).mean(axis=1)
    best = (-2.0, 0)
    for v in range(24):
        prof = np.roll(_MINOR if v >= 12 else _MAJOR, v % 12)
        r = float(np.corrcoef(chroma, prof)[0, 1])
        best = max(best, (r, v))
    key, minor = camelot(best[1])
    return Track("", float(60.0 / slope), first, float(len(y) / sr), key, minor, 0, "librosa")


# ── audio ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def decode(path: str, start: float, dur: float, tempo: float = 1.0, run=subprocess.run):
    """`dur` seconds of `path` from `start` as (frames, 2) float32 at RATE; time-stretched by `tempo` (rubberband, pitch kept) when not 1."""
    np = _np()
    args = ["ffmpeg", "-v", "error", "-nostdin"]
    pad = 0.0
    if start < 0:
        pad, start = -start, 0.0
        dur -= pad
    if start > 0:
        args += ["-ss", f"{start:.4f}"]
    args += ["-t", f"{max(dur, 0.01):.4f}", "-i", path, "-vn"]
    if abs(tempo - 1.0) > 1e-4:
        args += ["-af", f"rubberband=tempo={tempo:.6f}"]
    args += ["-f", "f32le", "-ac", "2", "-ar", str(RATE), "pipe:1"]
    p = run(args, capture_output=True, timeout=120)
    x = np.frombuffer(p.stdout[: len(p.stdout) // 8 * 8], dtype="<f4").reshape(-1, 2)
    if pad:
        x = np.concatenate([np.zeros((int(round(pad * RATE / tempo)), 2), dtype="float32"), x])
    return x


def fit(x, frames: int):
    np = _np()
    if len(x) >= frames:
        return x[:frames]
    return np.concatenate([x, np.zeros((frames - len(x), 2), dtype="float32")])


def onset_envelope(x, hop: int = 256, n_fft: int = 1024, max_hz: float | None = None):
    """Spectral flux of a mono mix: high where something hits. One value per `hop` frames. `max_hz` keeps only the kick's band."""
    np = _np()
    mono = x.mean(axis=1) if x.ndim == 2 else x
    if len(mono) < n_fft:
        return np.zeros(1)
    frames = 1 + (len(mono) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
    mag = np.abs(np.fft.rfft(mono[idx] * np.hanning(n_fft), axis=1))
    if max_hz:
        mag = mag[:, : int(max_hz * n_fft / RATE) + 1]
    mag = np.log1p(100 * mag)
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    return np.concatenate([[0.0], flux])


def nudge(x, first_beat_s: float, period: float, hop: int = 128, n_fft: int = 1024, limit: float = NUDGE_S) -> float:
    """How far (seconds) to move a grid so its beats, inside the audio `x`, sit on the onsets: the kick drum first (it is on the beat; hi-hats are
    often between), then everything. 0 unless the audio says so clearly: much of his library is played by drummers, and there Traktor's grid is
    the better guess than a weak, wandering onset curve."""
    for max_hz in (200.0, None):
        got = _nudge(x, first_beat_s, period, hop, n_fft, limit, max_hz)
        if got is not None:
            return got
    return 0.0


def _nudge(x, first_beat_s, period, hop, n_fft, limit, max_hz):
    np = _np()
    env = onset_envelope(x, hop, n_fft, max_hz)
    if not env.any():
        return None
    rate = RATE / hop
    centre = (n_fft / 2 + 0.0067 * RATE) / hop                     # a hit shows most a little past the middle of its window (measured on clicks)
    beats = first_beat_s + period * np.arange(int((len(x) / RATE - first_beat_s) / period) + 1)
    beats = beats[(beats >= period / 2) & (beats < len(x) / RATE - period / 2)]
    if len(beats) < 4:
        return None
    shifts = np.arange(-period / 2, period / 2, 1.0 / rate)        # the whole beat, to tell a clear peak from noise
    scores = []
    for s in shifts:
        i = np.round((beats + s) * rate - centre).astype(int)
        i = i[(i >= 1) & (i < len(env) - 1)]
        scores.append(env[i].sum())
    scores = np.asarray(scores)
    best = int(scores.argmax())
    zero = int(np.abs(shifts).argmin())
    clear = scores[best] >= 2.0 * np.median(scores) and scores[best] >= 1.25 * scores[zero]
    if not clear:
        return None
    off = 0.0
    if 0 < best < len(scores) - 1:                                 # the peak between two steps (a parabola through three)
        y0, y1, y2 = scores[best - 1], scores[best], scores[best + 1]
        den = y0 - 2 * y1 + y2
        off = 0.5 * (y0 - y2) / den if den else 0.0
    shift = float(shifts[best] + off / rate)
    return shift if abs(shift) <= limit else None


def loud_end(x, start_s: float) -> float:
    """Where the music in `x` (the track's tail, starting at `start_s` in the track) stops being loud: the last 0.1 s window within 14 dB of the
    tail's loud parts."""
    np = _np()
    w = RATE // 10
    n = len(x) // w
    if n < 2:
        return start_s
    rms = np.sqrt((x[: n * w].reshape(n, w, 2) ** 2).mean(axis=(1, 2)))
    ref = np.percentile(rms, 90)
    if ref <= 0:
        return start_s
    loud = np.nonzero(rms >= ref * 0.2)[0]
    return start_s + (loud[-1] + 1) * 0.1 if len(loud) else start_s


def _bass_split(x):
    from scipy.signal import butter, sosfiltfilt                   # noqa: PLC0415
    sos = butter(4, BASS_HZ, "low", fs=RATE, output="sos")
    low = sosfiltfilt(sos, x, axis=0).astype("float32")
    return low, (x - low).astype("float32")


# ── the plan ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def tempo_match(a_bpm: float, b_bpm: float) -> tuple[float, float] | None:
    """(rubberband tempo for B to play at A's tempo, B's beats per its Traktor beat) when within MAX_STRETCH, half and double time included."""
    best = None
    for mult in (1.0, 2.0, 0.5):
        r = a_bpm / (b_bpm * mult)
        if abs(r - 1) <= MAX_STRETCH and (best is None or abs(r - 1) < abs(best[0] - 1)):
            best = (r, mult)
    return best


@dataclass
class Plan:
    kind: str                     # "mix" or "cut"
    reason: str = ""
    a_out: float = 0.0            # track A's time where the crossing starts (on a phrase boundary)
    phrase: int = 0
    b_in: float = 0.0             # track B's time where it comes in (its first beat)
    tempo: float = 1.0            # B's rubberband tempo while the two play together
    b_period: float = 0.5         # one of B's beats, in B's own seconds
    a_period: float = 0.5
    overlap: int = OVERLAP
    ramp: list[float] = field(default_factory=list)     # B's tempo on each RAMP_STEPS-beat step after the crossing

    @property
    def b_src(self) -> float:
        """B's seconds the crossing and the glide consume."""
        return (self.overlap + len(self.ramp) * (RAMP // RAMP_STEPS)) * self.b_period

    @property
    def b_resume(self) -> float:
        return self.b_in + self.b_src

    @property
    def seconds(self) -> float:
        step = RAMP // RAMP_STEPS * self.b_period
        return self.overlap * self.a_period + sum(step / t for t in self.ramp)


def plan(a: Track | None, b: Track | None, a_loud_end: float | None = None, not_before: float = 0.0) -> Plan:
    if a is None or b is None:
        return Plan("cut", "a track is not analysed")
    kd = key_distance(a.camelot, b.camelot)
    if kd is not None and kd >= CLASH:
        return Plan("cut", f"keys clash ({a.key}{'A' if a.minor else 'B'} -> {b.key}{'A' if b.minor else 'B'})")
    m = tempo_match(a.bpm, b.bpm)
    if m is None:
        return Plan("cut", f"tempos too far apart ({a.bpm:.1f} -> {b.bpm:.1f} BPM)")
    tempo, mult = m
    b_period = b.period / mult
    ramp = [] if abs(tempo - 1) < 0.004 else [tempo + (1 - tempo) * (i + 1) / (RAMP_STEPS + 1) for i in range(RAMP_STEPS)]
    p = Plan("mix", "", tempo=tempo, b_period=b_period, a_period=a.period, b_in=b.first_beat, ramp=ramp)
    if b.first_beat + p.b_src + 4 * b_period > b.duration:
        return Plan("cut", "the next track is too short to mix into")
    span = OVERLAP * a.period
    end = a.duration - 0.25
    loud = a_loud_end if a_loud_end is not None else end
    for unit in PHRASES:
        step = unit * a.period
        k = math.floor((min(end - span, loud - span / 2) - a.first_beat) / step)
        t = a.first_beat + k * step
        if k >= 1 and t >= not_before:
            p.a_out, p.phrase = t, unit
            return p
    return Plan("cut", "no phrase boundary left to mix out on")


def render(p: Plan, a_path: str, b_path: str, run=subprocess.run):
    """The crossing as one clip, starting exactly at A's `a_out` (as nudged) and ending at B's `b_resume` (as nudged).
    -> (clip, a_out, b_resume): where A's piece must stop and B's piece must start, in each track's own seconds."""
    np = _np()
    # line both grids up on the audio itself
    a_raw = decode(a_path, p.a_out - LEAD_S, LEAD_S + p.overlap * p.a_period + 0.5, run=run)
    da = nudge(a_raw, LEAD_S, p.a_period)
    b_head = decode(b_path, p.b_in - 0.5, 0.5 + max(p.overlap, 32) * p.b_period, run=run)
    db = nudge(b_head, 0.5, p.b_period)
    a_out, b_in = p.a_out + da, p.b_in + db
    span = int(round(p.overlap * p.a_period * RATE))
    i0 = int(round((LEAD_S + da) * RATE))
    a_seg = fit(a_raw[i0:], span)

    # B: the crossing at A's tempo, the glide back, then a seam into its own piece
    seam = int(SEAM_S * RATE)
    pieces = [(b_in, p.overlap * p.b_period, p.tempo)]
    src = b_in + p.overlap * p.b_period
    step = RAMP // RAMP_STEPS * p.b_period
    for t in p.ramp:
        pieces.append((src, step, t))
        src += step
    b_resume = src
    total = span + sum(int(round(d / t * RATE)) for _s, d, t in pieces[1:])
    b_all = np.zeros((total + seam, 2), dtype="float32")
    at = 0
    for n, (s, d, t) in enumerate(pieces):                         # each piece placed so its source time `s` lands at `at`, seams crossfaded
        out_len = span if n == 0 else int(round(d / t * RATE))
        pad = 2 * SEAM_S
        got = decode(b_path, s - pad, d + 2 * pad, tempo=t, run=run)
        lead = int(round(pad / t * RATE))
        lo, hi = (at if n == 0 else at - seam), at + out_len + seam
        seg = fit(got[lead - (at - lo):], hi - lo)
        w = np.ones(hi - lo, dtype="float32")
        if n:
            w[: 2 * seam] = np.linspace(0, 1, 2 * seam, dtype="float32")
        f0 = at + out_len - seam - lo
        w[f0:] = np.linspace(1, 0, 2 * seam, dtype="float32")[: len(w) - f0]
        b_all[lo:hi] += seg * w[:, None]
        at += out_len
    # the last seam: B's own audio at b_resume, faded in under the end of the glide, so the next piece starts on the very same signal
    tail = fit(decode(b_path, b_resume - SEAM_S, 2 * SEAM_S, run=run), 2 * seam)
    b_all[total - seam: total + seam] += tail * np.linspace(0, 1, 2 * seam, dtype="float32")[:, None]
    clip = b_all[: total + seam].copy()
    b_resume += SEAM_S

    # the crossing: B's highs fade in over the first half, A's highs out over the second; the basses trade places in one beat at halfway
    a_lo, a_hi = _bass_split(a_seg)
    b_lo, b_hi = _bass_split(clip[:span])
    t = np.arange(span) / RATE
    half = (p.overlap // 2) * p.a_period
    beat = p.a_period
    b_hi_g = np.where(t < half, np.sin(np.pi / 2 * np.clip(t / half, 0, 1)), 1.0)
    a_hi_g = np.where(t < half, 1.0, np.cos(np.pi / 2 * np.clip((t - half) / (span / RATE - half), 0, 1)))
    swap = np.clip((t - half) / beat, 0, 1)
    clip[:span] = a_lo * (1 - swap)[:, None] + a_hi * a_hi_g[:, None] + b_lo * swap[:, None] + b_hi * b_hi_g[:, None]
    return np.clip(clip, -1.0, 1.0).astype("float32"), a_out, b_resume


# ── gapless playback ──────────────────────────────────────────────────────────────────────────────────────────────────────────
class Piece:
    def __init__(self, source, on_start=None, tag=None):
        self.source, self.on_start, self.tag = source, on_start, tag
        self.limit: int | None = None
        self.read = 0
        self.started = False


class ChainSource:
    """Router source made of pieces played back to back with no gap. A playing piece can be told to stop at a frame (cut_at) so the next
    one (a crossing) starts exactly there. While more may come (until finish()), an empty chain is silence, not the end."""

    def __init__(self):
        self.pieces: deque[Piece] = deque()
        self.lock = threading.Lock()
        self.more = True

    def append(self, source, on_start=None, tag=None) -> Piece:
        p = Piece(source, on_start, tag)
        with self.lock:
            self.pieces.append(p)
        return p

    def cut_at(self, piece: Piece, frames: int, margin: int = RATE) -> bool:
        """Stop `piece` after `frames` frames, if it hasn't got within `margin` of there yet."""
        with self.lock:
            if piece not in self.pieces or piece.read + margin > frames:
                return False
            piece.limit = frames
            return True

    def finish(self) -> None:
        self.more = False

    def read(self, frames: int):
        np = _np()
        parts, got = [], 0
        with self.lock:
            while got < frames and self.pieces:
                head = self.pieces[0]
                if not head.started:
                    head.started = True
                    if head.on_start is not None:
                        try:
                            head.on_start()
                        except Exception:                          # noqa: BLE001
                            pass
                want = frames - got
                if head.limit is not None:
                    want = min(want, head.limit - head.read)
                chunk = head.source.read(want) if want > 0 else np.zeros((0, 2), dtype="float32")
                head.read += len(chunk)
                got += len(chunk)
                if len(chunk):
                    parts.append(chunk)
                if (head.limit is not None and head.read >= head.limit) or head.source.done:
                    self.pieces.popleft()
                    head.source.close()
                    continue
                if len(chunk) < want:
                    break                                          # the piece is still decoding: what there is now
        return np.concatenate(parts) if parts else np.zeros((0, 2), dtype="float32")

    @property
    def done(self) -> bool:
        with self.lock:
            return not self.pieces and not self.more

    def close(self) -> None:
        self.more = False
        with self.lock:
            pieces, self.pieces = list(self.pieces), deque()
        for p in pieces:
            p.source.close()


# ── order ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def walk(tracks: list[str], known, limit: int = 300, rng=None, pool: int = 400) -> list[str]:
    """A DJ's order through `tracks`: each next one mixes with the last (tempo within MAX_STRETCH, key within one step if possible), his
    higher-rated tracks a little more often. `known(path)` -> Track or None. Unknown tracks fill in where nothing mixes."""
    rng = rng or random.Random()
    left = list(tracks)
    rng.shuffle(left)
    if not left:
        return []
    order = [left.pop()]
    while left and len(order) < limit:
        last = known(order[-1])
        cands = left[-pool:]
        best, best_i = -1e9, len(left) - 1
        for i, c in enumerate(cands):
            t = known(c)
            s = rng.random()
            if last is not None and t is not None:
                if tempo_match(last.bpm, t.bpm):
                    s += 4
                kd = key_distance(last.camelot, t.camelot)
                s += 3 if kd is not None and kd <= 1 else 1 if kd == 2 else 0
                s += 0.3 * t.stars
            if s > best:
                best, best_i = s, len(left) - len(cands) + i
        order.append(left.pop(best_i))
    return order


# ── the setting ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
def enabled(env: dict | None = None, state: Path = STATE) -> bool:
    try:
        return bool(json.loads((state / "dj.json").read_text(encoding="utf-8"))["on"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return str((env or os.environ).get("BILBY_DJ", "on")).lower() not in ("off", "0", "no", "false")


def set_enabled(on: bool, state: Path = STATE) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "dj.json").write_text(json.dumps({"on": bool(on), "at": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")


# ── the player ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Next:
    kind: str
    reason: str = ""
    clip: object = None
    a_stop: float = 0.0           # A's seconds where its piece stops (the crossing starts)
    b_start: float = 0.0          # B's seconds where its own piece starts
    b_pos: float = 0.0            # B's position to report while the crossing plays


class Mixer:
    """Works out and renders the crossing from A into B (blocking: run it in a thread)."""

    def __init__(self, lib: Library, env: dict | None = None, log=print, run=subprocess.run, state: Path = STATE):
        self.lib, self.env, self.log, self.run, self.state = lib, env, log, run, state

    def prepare(self, a: str, b: str, not_before: float) -> Next:
        if not enabled(self.env, self.state):
            return Next("cut", "DJ mode is off")
        a_path, b_path = self.lib.local(a), self.lib.local(b)
        if not a_path or not b_path:
            return Next("cut", "not in the library on this PC")
        ta, tb = self.lib.info(a), self.lib.info(b)
        loud = None
        if ta is not None:
            tail0 = max(0.0, ta.duration - 90)
            loud = loud_end(decode(a_path, tail0, ta.duration - tail0 + 1, run=self.run), tail0)
        p = plan(ta, tb, loud, not_before)
        if p.kind != "mix":
            return Next("cut", p.reason)
        t0 = time.monotonic()
        clip, a_stop, b_start = render(p, a_path, b_path, run=self.run)
        self.log(f"(dj: {p.phrase}-beat phrase at {a_stop:.1f} s, {ta.bpm:.1f} -> {tb.bpm:.1f} BPM (x{p.tempo:.3f}), keys "
                 f"{ta.key}{'A' if ta.minor else 'B'}->{tb.key}{'A' if tb.minor else 'B'}, grids nudged {(a_stop - p.a_out) * 1000:+.0f}/"
                 f"{(b_start - p.b_resume - SEAM_S) * 1000:+.0f} ms, rendered {len(clip) / RATE:.1f} s in "
                 f"{time.monotonic() - t0:.1f} s)")
        return Next("mix", "", clip, a_stop, b_start, p.b_in)


class DjPlayer(stereo.RouterPlayer):
    """stereo.RouterPlayer (music.BoxMusic's `player` on the router) whose Bilby tracks play as one gapless chain: each track mixes into the next
    when DJ mode is on and the two mix; otherwise one ends and the next starts, with no gap. Anything else in the queue (a Spotify item) ends the
    chain, and RouterPlayer takes it from there."""

    def __init__(self, router, kinds: list[str], lib: Library | None = None, env: dict | None = None, mixer: Mixer | None = None,
                 log=print, **kw):
        super().__init__(router, kinds, log=log, **kw)
        self.env = env
        self.lib = lib or Library(env.get("BILBY_MUSIC_ROOT", DEFAULT_ROOT) if env else DEFAULT_ROOT,
                                  env.get("BILBY_TRAKTOR_NML", DEFAULT_NML) if env else DEFAULT_NML, log=log)
        self.mixer = mixer or Mixer(self.lib, env, log)
        self.chain: ChainSource | None = None
        self.gen = 0

    def _body(self, rel: str, start: float):
        """One track from `start` as a router source: the file itself when it is on this PC, else the hub's media server's URL."""
        import book_out                                             # noqa: PLC0415
        import router as router_mod                                 # noqa: PLC0415
        local = self.lib.local(rel)
        src, at = (local, start) if local else (self.music.media.track_url(rel, start, self.music.towards), 0.0)
        kw = {"popen": self.popen} if self.popen is not None else {}
        return router_mod.PipeSource(book_out.pcm_args(src, at), **kw)

    async def play(self, url: str) -> None:
        await self.stop()
        self.gen += 1
        gen = self.gen
        self.loop = asyncio.get_running_loop()
        m = self.music
        rel, start = (m.track(), m.offset) if m is not None else ("", 0.0)
        chain = ChainSource()
        piece = chain.append(self._body(rel, start), tag=rel)
        self.chain = chain
        p = self.router.play(chain, layer="media", targets=self.kinds, gain=self.gain)
        self.play_obj = p

        async def watch() -> None:
            await asyncio.to_thread(p.finished.wait)
            if self.play_obj is p and self.music is not None:     # the queue ran out by itself
                self.play_obj = None
                await self.music.on_idle()
        self.loop.create_task(watch())
        self.loop.create_task(self._feed(gen, chain, piece, m.idx if m is not None else 0, rel, start))

    async def _feed(self, gen: int, chain: ChainSource, piece: Piece, idx: int, rel: str, start: float) -> None:
        """Keep the next track lined up behind the one playing: its crossing rendered and queued, or a plain join."""
        while gen == self.gen and self.chain is chain and self.music is not None:
            queue = self.music.queue
            if idx + 1 >= len(queue):
                chain.finish()
                return
            nxt = queue[idx + 1]
            if str(nxt).startswith("spotify:"):
                chain.finish()                                     # not Bilby's: the chain ends and RouterPlayer plays it
                return
            here = start + piece.read / RATE
            try:
                step = await asyncio.to_thread(self.mixer.prepare, rel, nxt, here + MARGIN_S)
            except Exception as e:                                 # noqa: BLE001 — a failed mix is a plain cut
                self.log(f"(dj: mix failed: {type(e).__name__}: {str(e)[:100]})")
                step = Next("cut", "error")
            if gen != self.gen or self.chain is not chain:
                return
            if step.kind == "mix" and chain.cut_at(piece, int(round((step.a_stop - start) * RATE))):
                chain.append(_Clip(step.clip), on_start=self._advancer(gen, idx + 1, step.b_pos, True))
                body = chain.append(self._body(nxt, step.b_start), tag=nxt)
                start = step.b_start
            else:
                if step.kind == "mix":
                    step.reason = "too late to mix in"
                self.log(f"(dj: plain cut into the next track: {step.reason})")
                body = chain.append(self._body(nxt, 0.0), on_start=self._advancer(gen, idx + 1, 0.0, False), tag=nxt)
                start = 0.0
            while gen == self.gen and self.chain is chain and not body.started:
                await asyncio.sleep(0.5)
            piece, idx, rel = body, idx + 1, nxt

    def _advancer(self, gen: int, idx: int, pos: float, mixed: bool):
        loop = self.loop

        def fire() -> None:                                         # the mixer's thread: hand it to the hub's loop
            loop.call_soon_threadsafe(self._advance, gen, idx, pos, mixed)
        return fire

    def _advance(self, gen: int, idx: int, pos: float, mixed: bool) -> None:
        m = self.music
        if gen != self.gen or m is None or idx >= len(m.queue):
            return
        m.idx, m.offset, m.started, m.playing = idx, pos, m.clock(), True
        title, artist = m.title()
        self.log(f"[{m.name}] {'mixed into' if mixed else 'playing'} {title}" + (f" by {artist}" if artist else ""))

    async def stop(self) -> None:
        self.gen += 1
        self.chain = None
        await super().stop()

    async def play_spotify(self, uri: str, pos: float = 0.0) -> None:
        """RouterPlayer's (a plain cut from Bilby to Spotify); the chain's feeder stops with it. Without a Spotify client RouterPlayer sets this
        to None on the instance, and BoxMusic skips Spotify items, as before."""
        self.gen += 1
        self.chain = None
        await super().play_spotify(uri, pos)

    def _adopt(self, st: dict) -> None:
        """A Spotify play started from his phone: RouterPlayer stops the chain (a plain cut); its feeder stops with it."""
        self.gen += 1
        self.chain = None
        super()._adopt(st)

class _Clip:
    """A rendered crossing (router.ClipSource without importing the router)."""

    def __init__(self, pcm):
        self.pcm, self.pos = pcm, 0

    def read(self, frames: int):
        chunk = self.pcm[self.pos:self.pos + frames]
        self.pos += len(chunk)
        return chunk

    @property
    def done(self) -> bool:
        return self.pos >= len(self.pcm)

    def close(self) -> None:
        pass


def order(tracks: list[str], lib: Library, limit: int = 300) -> list[str]:
    """A shuffle as a DJ would play it (walk), when DJ mode is on."""
    return walk(tracks, lib.known, limit=limit)


# ── by hand ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _main(argv=None) -> int:
    import argparse                                                 # noqa: PLC0415
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["plan", "demo"])
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--out", default="hdmi", help="the router output for demo (SATELLITE_OUT_HDMI / _BT name it)")
    ap.add_argument("--before", type=float, default=20.0, help="seconds of A before the crossing (demo)")
    ap.add_argument("--after", type=float, default=20.0, help="seconds of B after the crossing (demo)")
    args = ap.parse_args(argv)
    from ear import load_env                                        # noqa: PLC0415
    env = load_env()
    lib = Library(env.get("BILBY_MUSIC_ROOT", DEFAULT_ROOT), env.get("BILBY_TRAKTOR_NML", DEFAULT_NML))
    ta, tb = lib.info(args.a), lib.info(args.b)
    print("A:", ta)
    print("B:", tb)
    a_path, b_path = lib.local(args.a), lib.local(args.b)
    loud = None
    if ta and a_path:
        tail0 = max(0.0, ta.duration - 90)
        loud = loud_end(decode(a_path, tail0, ta.duration - tail0 + 1), tail0)
    p = plan(ta, tb, loud)
    print(p, f"crossing {p.seconds:.1f} s" if p.kind == "mix" else "")
    if args.cmd == "plan" or p.kind != "mix":
        return 0
    clip, a_stop, b_start = render(p, a_path, b_path)
    import book_out                                                 # noqa: PLC0415
    import router as router_mod                                     # noqa: PLC0415
    import speaker_out                                              # noqa: PLC0415
    part = speaker_out.output_parts(env)[args.out]
    r = router_mod.Router([(args.out, part, 0.0)], log=print)
    r.start()
    chain = ChainSource()
    body_a = chain.append(router_mod.PipeSource(book_out.pcm_args(a_path, max(0.0, a_stop - args.before))))
    chain.cut_at(body_a, int(round(min(args.before, a_stop) * RATE)), margin=0)
    chain.append(_Clip(clip), on_start=lambda: print("-- crossing --"))
    body_b = chain.append(router_mod.PipeSource(book_out.pcm_args(b_path, b_start)), on_start=lambda: print("-- B on its own --"))
    chain.cut_at(body_b, int(args.after * RATE), margin=0)
    chain.finish()
    play = r.play(chain, layer="media", targets={args.out})
    play.finished.wait()
    time.sleep(1.0)
    r.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
