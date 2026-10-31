"""The house's audio router on skippy-pc: every sound the hub plays on the PC's own outputs goes through here (the Captain, 2026-10-31).

Before it, each output decoded each sound on its own (a clip three times for pod + HDMI + Bluetooth), a book stopped and restarted to make room for
Skippy's voice, a Bluetooth stereo turned on after the hub started was never seen (PortAudio lists devices once), and a stereo that had been idle
clipped the first word while it woke. Now:

  * one SINK per output (`hdmi`, `bt`): a stream that stays open and is fed continuously (silence when nothing plays, so a Bluetooth stereo
    never sleeps between sentences), with that output's delay built in, so all of them line up with the pod;
  * each SOURCE (a voice clip, a book's ffmpeg stream) is decoded ONCE and mixed into every sink it targets;
  * LAYERS: `voice` (Skippy talking) sits over `media` (books, music); media is ducked to DUCK while any voice plays, and can be ducked from
    outside (the hub while it listens), with a short ramp, never a stop;
  * a missing output (the stereo is off) is looked for again every RESCAN_S, while nothing is playing (re-reading the device list
    resets PortAudio, so it only happens in silence).

    SATELLITE_OUT=office=pod+hdmi+bt            which outputs a room uses (speaker_out.parse_outputs)
    SATELLITE_OUT_HDMI / SATELLITE_OUT_BT       a part of each Windows output's name
    SATELLITE_OUT_DELAYS=hdmi=0.5,bt=0.4        each output's starting delay; sync.py then measures and corrects it from the PC's microphone

Thread-safe; the mixer runs in its own thread. `stream_factory` and `find` are the seams the tests replace.
"""
from __future__ import annotations

import collections
import itertools
import subprocess
import threading
import time

RATE = 48000
BLOCK_S = 0.05
BLOCK = int(RATE * BLOCK_S)
DUCK = 0.25
RAMP = 0.15                       # how far a layer's gain moves per block (about 0.3 s from full to ducked)
RESCAN_S = 20.0
SLACK_S = 0.4                     # a sink more than this past its delay (its device runs slow) drops a block to catch up


def _np():
    import numpy as np                                             # noqa: PLC0415
    return np


class Sink:
    """One PC output: an always-open stream fed from a FIFO that starts with `delay_s` of silence."""

    def __init__(self, kind: str, part: str, delay_s: float, find, stream_factory, log=print):
        self.kind, self.part, self.delay_s, self.find, self.factory, self.log = kind, part, delay_s, find, stream_factory, log
        self.stream = None
        self.name = ""
        self.fifo: collections.deque = collections.deque()
        self.buffered = 0
        self.lock = threading.Lock()
        self._partial = None
        self.pushed = 0                                            # frames pushed since it opened
        self._loud_at = 0                                          # `pushed` just after the last block that was not silence
        self.latency = 0.0                                         # the stream's own output latency (PortAudio's figure), seconds

    @property
    def open(self) -> bool:
        return self.stream is not None

    def try_open(self) -> bool:
        dev = None
        try:
            dev = self.find(self.part)
        except Exception:                                          # noqa: BLE001
            dev = None
        if dev is None:
            return False
        np = _np()
        with self.lock:
            self.fifo.clear()
            pad = int(RATE * self.delay_s)
            self.fifo.append(np.zeros((pad, 2), dtype="float32"))
            self.buffered, self._partial = pad, None
            self.pushed = self._loud_at = 0
        try:
            self.stream = self.factory(dev[0], self._callback)
            self.latency = float(getattr(self.stream, "latency", 0.0) or 0.0)
        except Exception as e:                                     # noqa: BLE001
            self.log(f"(router: output {self.kind} {dev[1]!r} would not open: {type(e).__name__}: {str(e)[:80]})")
            self.stream = None
            return False
        self.name = dev[1]
        self.log(f"(router: output {self.kind} is {dev[1]!r}, {self.delay_s:.2f} s behind)")
        return True

    def close(self) -> None:
        s, self.stream = self.stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:                                      # noqa: BLE001
                pass

    def push(self, block) -> None:
        loud = bool(block.any())
        with self.lock:
            self.fifo.append(block)
            self.buffered += len(block)
            self.pushed += len(block)
            if loud:
                self._loud_at = self.pushed
            while self.buffered > int(RATE * (self.delay_s + SLACK_S)) and len(self.fifo) > 1:
                self.buffered -= len(self.fifo.popleft())      # its clock runs slower than the mixer's: catch up by a block

    @property
    def idle(self) -> bool:
        """Only silence is queued (the last sound already went out to the device)."""
        with self.lock:
            return self.pushed - self._loud_at >= self.buffered

    def realign(self) -> float:
        """Just before a voice clip: put exactly `delay_s` of silence in front of it, so the device's clock running a little fast or slow
        against the mixer's (the FIFO drifting anywhere from empty to delay + SLACK_S) never shifts where Skippy lands against the pod.
        Only while idle (the queue is silence): a sound is never cut. Returns the queue's length in seconds before the clip."""
        np = _np()
        with self.lock:
            if self.pushed - self._loud_at < self.buffered:
                return self.buffered / RATE
            pad = int(RATE * self.delay_s)
            self.fifo.clear()
            self._partial = None
            if pad:
                self.fifo.append(np.zeros((pad, 2), dtype="float32"))
            self.buffered = pad
            return pad / RATE

    def take(self, frames: int):
        """`frames` frames from the FIFO, padded with silence when it runs dry."""
        np = _np()
        out = np.zeros((frames, 2), dtype="float32")
        got = 0
        with self.lock:
            while got < frames:
                if self._partial is None:
                    if not self.fifo:
                        break
                    self._partial = self.fifo.popleft()
                n = min(frames - got, len(self._partial))
                out[got:got + n] = self._partial[:n]
                got += n
                self.buffered -= n
                self._partial = self._partial[n:] if n < len(self._partial) else None
        return out

    def _callback(self, outdata, frames, _time, _status) -> None:
        outdata[:] = self.take(frames)


class ClipSource:
    """A whole clip, already decoded (Skippy's voice)."""

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


class PipeSource:
    """A long stream (a book): ffmpeg decodes into a pipe, a reader thread keeps up to a few seconds ready, the mixer takes blocks."""

    def __init__(self, args: list[str], popen=subprocess.Popen, ahead_s: float = 3.0):
        self.proc = popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.buf: collections.deque = collections.deque()
        self.have = 0
        self.ahead = int(RATE * ahead_s)
        self.eof = False
        self.lock = threading.Condition()
        self.stopped = False
        threading.Thread(target=self._reader, daemon=True, name="router-pipe").start()

    def _reader(self) -> None:
        np = _np()
        size = BLOCK * 8
        while not self.stopped:
            with self.lock:
                while self.have >= self.ahead and not self.stopped:
                    self.lock.wait(0.5)
            if self.stopped:
                break
            raw = self.proc.stdout.read(size)
            if not raw:
                break
            data = np.frombuffer(raw[: len(raw) // 8 * 8], dtype="<f4").reshape(-1, 2)
            with self.lock:
                self.buf.append(data)
                self.have += len(data)
        with self.lock:
            self.eof = True

    def read(self, frames: int):
        np = _np()
        parts, got = [], 0
        with self.lock:
            while got < frames and self.buf:
                head = self.buf[0]
                n = min(frames - got, len(head))
                parts.append(head[:n])
                got += n
                if n < len(head):
                    self.buf[0] = head[n:]
                else:
                    self.buf.popleft()
            self.have -= got
            self.lock.notify_all()
        return np.concatenate(parts) if parts else np.zeros((0, 2), dtype="float32")

    @property
    def done(self) -> bool:
        with self.lock:
            return self.eof and not self.buf

    def close(self) -> None:
        self.stopped = True
        with self.lock:
            self.lock.notify_all()
        try:
            self.proc.kill()
        except OSError:
            pass


STALE_VOICE_S = 2.0              # Skippy's voice that could not start within this long is dropped, never played late (2026-10-31: the first clip
                                 # after a restart waited 18 s for PortAudio's first device scan, and the room heard "News about what?" as his answer)


class Play:
    def __init__(self, pid: int, source, layer: str, targets: set[str], gain: float, created: float = 0.0):
        self.id, self.source, self.layer, self.targets, self.gain = pid, source, layer, set(targets), gain
        self.created, self.started = created, False
        self.marks: dict[str, tuple[float, float, float]] = {}    # kind -> (clock when its first block went in, seconds queued ahead of it, stream latency)
        self.finished = threading.Event()


class Router:
    def __init__(self, outputs: list[tuple[str, str, float]], find=None, stream_factory=None, reinit=None, log=print, clock=time.monotonic, autostart: bool = True):
        self.find = find or _find_device
        self.factory = stream_factory or _sd_stream
        self.reinit = reinit or _reinit_portaudio
        self.log, self.clock = log, clock
        self.sinks = {k: Sink(k, part, d, self.find, self.factory, log) for k, part, d in outputs}
        self.plays: dict[int, Play] = {}
        self.ids = itertools.count(1)
        self.lock = threading.RLock()
        self.layer_gain = {"voice": 1.0, "media": 1.0}
        self.outside_duck = 1.0                                   # set by duck(): the hub listening lowers media too
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_scan = -1e9
        self.autostart = autostart
        self.calibrator = None                                    # sync.Calibrator, set by shared(): measures each voice clip against the pod

    # ── what the hub calls ────────────────────────────────────────────────────────────────────────────────────────────────────
    def kinds(self) -> list[str]:
        return list(self.sinks)

    def available(self, kind: str) -> bool:
        s = self.sinks.get(kind)
        return bool(s and s.open)

    def play(self, source, layer: str = "voice", targets=None, gain: float = 1.0) -> Play:
        with self.lock:
            p = Play(next(self.ids), source, layer, targets if targets is not None else set(self.sinks), gain, created=self.clock())
            self.plays[p.id] = p
        if self.autostart:
            self.start()
        return p

    def stop(self, play: Play | None) -> None:
        if play is None:
            return
        with self.lock:
            self.plays.pop(play.id, None)
        play.source.close()
        play.finished.set()

    def duck(self, on: bool, percent: float = 25) -> None:
        self.outside_duck = max(0.0, min(1.0, percent / 100.0)) if on else 1.0

    # ── the mixer ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
    def start(self) -> None:
        with self.lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="audio-router")
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        for s in self.sinks.values():
            s.close()

    def _loop(self) -> None:
        nxt = self.clock()
        while not self._stop.is_set():
            try:
                self.mix_once()
            except Exception as e:                                # noqa: BLE001 — the router must outlive anything one block hits
                self.log(f"(router: {type(e).__name__}: {str(e)[:120]})")
            nxt += BLOCK_S
            pause = nxt - self.clock()
            if pause > 0:
                time.sleep(pause)
            elif pause < -1.0:
                nxt = self.clock()                                # fell far behind (the PC slept): start the clock again

    def scan(self) -> None:
        """Open the outputs that aren't open. A missing device makes PortAudio read the device list again, only while nothing plays.
        The sync microphone (sync.MicRing) is opened here too, after any re-read: re-reading closes every stream, its included."""
        mic = getattr(self.calibrator, "mic", None)
        missing = [s for s in self.sinks.values() if not s.open]
        if not missing:
            if mic is not None and mic.stream is None:
                mic.start()
            return
        with self.lock:
            quiet = not self.plays
        if quiet:                                                 # nothing to interrupt: read the device list again, open everything
            try:
                for s in self.sinks.values():
                    s.close()
                self.reinit()
            except Exception:                                     # noqa: BLE001
                pass
            for s in self.sinks.values():
                s.try_open()
            if mic is not None:
                mic.stream = None                                 # the re-read closed it
                mic.start()
        else:
            for s in missing:
                s.try_open()

    def mix_once(self) -> None:
        np = _np()
        now = self.clock()
        if now - self._last_scan >= RESCAN_S:
            self._last_scan = now
            self.scan()
        with self.lock:
            plays = list(self.plays.values())
        voice = any(p.layer == "voice" for p in plays)
        want = {"voice": 1.0, "media": min(DUCK if voice else 1.0, self.outside_duck)}
        for layer, target in want.items():                        # ramp, never jump: no clicks
            g = self.layer_gain[layer]
            self.layer_gain[layer] = target if abs(target - g) <= RAMP else g + (RAMP if target > g else -RAMP)
        mixes = {k: None for k, s in self.sinks.items() if s.open}
        for p in plays:
            if not p.started:
                if not any(k in mixes for k in p.targets):
                    if p.layer == "voice" and self.clock() - p.created > STALE_VOICE_S:
                        self.log(f"(router: a voice clip waited {self.clock() - p.created:.1f} s for an output: dropped, never played late)")
                        self.stop(p)
                    continue                                      # its outputs aren't open (yet): wait, don't spend it on nothing
                if p.layer == "voice" and self.clock() - p.created > STALE_VOICE_S:
                    self.log(f"(router: a voice clip started {self.clock() - p.created:.1f} s late: dropped, never played late)")
                    self.stop(p)
                    continue
                p.started = True
                if p.layer == "voice":                            # line it up: exactly its delay of silence ahead of it (sync.py measures the rest)
                    for k in p.targets:
                        if k in mixes:
                            sink = self.sinks[k]
                            p.marks[k] = (self.clock(), sink.realign(), sink.latency)
            chunk = p.source.read(BLOCK)
            if len(chunk):
                chunk = chunk * (p.gain * self.layer_gain.get(p.layer, 1.0))
                if len(chunk) < BLOCK:
                    chunk = np.concatenate([chunk, np.zeros((BLOCK - len(chunk), 2), dtype="float32")])
                for k in p.targets:
                    if k in mixes:
                        mixes[k] = chunk if mixes[k] is None else mixes[k] + chunk
            if p.source.done:
                self.stop(p)
        for k, block in mixes.items():
            out = np.zeros((BLOCK, 2), dtype="float32") if block is None else np.clip(block, -1.0, 1.0).astype("float32")
            self.sinks[k].push(out)


# ── the real devices ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _find_device(part: str):
    import speaker_out                                            # noqa: PLC0415
    return speaker_out.find_device(part)


def _sd_stream(device: int, callback):
    import sounddevice as sd                                      # noqa: PLC0415
    s = sd.OutputStream(samplerate=RATE, channels=2, dtype="float32", device=device, callback=callback, blocksize=BLOCK, latency="low")
    s.start()
    return s


def _reinit_portaudio() -> None:
    import sounddevice as sd                                      # noqa: PLC0415
    sd._terminate()                                               # noqa: SLF001 — the only way PortAudio reads the device list again
    sd._initialize()                                              # noqa: SLF001


_ROUTER: Router | None = None
_ROUTER_LOCK = threading.Lock()


def shared(env: dict, log=print) -> Router:
    """The one router of this hub process: every PC output any room names (SATELLITE_OUT), with its delay."""
    global _ROUTER
    import speaker_out                                            # noqa: PLC0415
    with _ROUTER_LOCK:
        if _ROUTER is None:
            parts, delays = speaker_out.output_parts(env), speaker_out.output_delays(env)
            used = set()
            for part in (env.get("SATELLITE_OUT") or "").split(";"):
                if "=" in part:
                    used |= {k.strip() for k in part.split("=", 1)[1].split("+")}
            import sync                                           # noqa: PLC0415
            delays.update(sync.saved_delays())                    # what the last calibration settled on beats the vault's starting guess
            _ROUTER = Router([(k, parts[k], delays.get(k, 0.0)) for k in parts if k in used], log=log)
            _ROUTER.calibrator = sync.for_router(_ROUTER, env, log)   # the PC's microphone hears the pod and these outputs: their offset, each reply
            if _ROUTER.sinks:
                _ROUTER.start()                                   # open the outputs now: PortAudio's first device scan takes seconds
        return _ROUTER
