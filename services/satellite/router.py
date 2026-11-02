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

    SATELLITE_OUT=theater=pod+hdmi+bt            which outputs a room uses (speaker_out.parse_outputs)
    SATELLITE_OUT_HDMI / SATELLITE_OUT_BT       a part of each Windows output's name
    SATELLITE_OUT_DELAYS=hdmi=0.5,bt=0.4        each output's starting delay; sync.py then measures and corrects it from the PC's microphone
    SATELLITE_OUT_ENCLAVE=tcp://skippy-core:4953   a NETWORK output (2026-11-01): the Enclave speakers on skippy-core's Bluetooth, fed over
                                                the tailnet to its snapserver (core/install/enclave.sh, NetStream below); open only while
                                                skippy-core says the speaker is on and playing
    SATELLITE_OUT=theater=pod+enclave>hdmi       `a>b`: a preference, not both: b plays only while a is not open (the Enclave, else the
                                                projector); SKIPPY_STEREO_OUT takes the same form for music
    Several outputs in one room line up with each other by `align` (sync_outputs.py measures each one's latency from the microphone).

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
SOLO_DELAY_S = 0.15               # a room's only voice: the queue kept ahead of it, so a late mixer block never stutters (SATELLITE_SOLO_DELAY)


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
        return self.stream is not None and getattr(self.stream, "alive", True)

    @property
    def net(self) -> bool:
        return is_net(self.part)

    def try_open(self) -> bool:
        if self.stream is not None:                                # a network output that went away: let go of the old connection first
            self.close()
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
    def __init__(self, outputs: list[tuple[str, str, float]], find=None, stream_factory=None, reinit=None, log=print, clock=time.monotonic, autostart: bool = True,
                 prefer: list[list[str]] | None = None):
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
        self.prefer = [c for c in (prefer or []) if len(c) > 1]   # ["enclave", "hdmi"]: the first of them that is open plays, the rest don't

    # ── what the hub calls ────────────────────────────────────────────────────────────────────────────────────────────────────
    def kinds(self) -> list[str]:
        return list(self.sinks)

    def available(self, kind: str) -> bool:
        s = self.sinks.get(kind)
        return bool(s and s.open)

    def effective(self, targets) -> set[str]:
        """A play's targets after the preferences: in each chain, only the first output that is open (none open: left as they are)."""
        out = set(targets)
        for chain in self.prefer:
            first = next((k for k in chain if k in out and self.available(k)), None)
            if first is not None:
                out -= set(chain) - {first}
        return out

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

    def retarget(self, old, new, layer: str = "media") -> int:
        """Move what plays on `old` outputs to `new` ones, at once (music: "now upstairs too"). -> how many plays moved."""
        old, moved = set(old), 0
        with self.lock:
            for p in self.plays.values():
                if p.layer == layer and set(p.targets) == old:
                    p.targets = set(new)                          # a new set object: the mixer reads one or the other, never half
                    moved += 1
        return moved

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
        for s in [s for s in missing if s.net]:                   # a network output: no device list to re-read, never a reason to reset the others
            s.try_open()
        missing = [s for s in missing if not s.net]
        if not missing:
            if mic is not None and mic.stream is None:
                mic.start()
            return
        with self.lock:
            quiet = not self.plays
        if quiet:                                                 # nothing to interrupt: read the device list again, open everything
            try:
                for s in self.sinks.values():
                    if not s.net:
                        s.close()
                self.reinit()
            except Exception:                                     # noqa: BLE001
                pass
            for s in self.sinks.values():
                if not s.net:
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
            targets = self.effective(p.targets) if self.prefer else p.targets
            if not p.started:
                if not any(k in mixes for k in targets):
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
                    for k in targets:
                        if k in mixes:
                            sink = self.sinks[k]
                            p.marks[k] = (self.clock(), sink.realign(), sink.latency)
            chunk = p.source.read(BLOCK)
            if len(chunk):
                chunk = chunk * (p.gain * self.layer_gain.get(p.layer, 1.0))
                if len(chunk) < BLOCK:
                    chunk = np.concatenate([chunk, np.zeros((BLOCK - len(chunk), 2), dtype="float32")])
                for k in targets:
                    if k in mixes:
                        mixes[k] = chunk if mixes[k] is None else mixes[k] + chunk
            if p.source.done:
                self.stop(p)
        for k, block in mixes.items():
            out = np.zeros((BLOCK, 2), dtype="float32") if block is None else np.clip(block, -1.0, 1.0).astype("float32")
            self.sinks[k].push(out)


# ── the real devices ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _find_device(part: str):
    if is_net(part):
        return net_find(part)
    import speaker_out                                            # noqa: PLC0415
    return speaker_out.find_device(part)


def _sd_stream(device, callback):
    if is_net(device):
        return NetStream(device, callback)
    import sounddevice as sd                                      # noqa: PLC0415
    s = sd.OutputStream(samplerate=RATE, channels=2, dtype="float32", device=device, callback=callback, blocksize=BLOCK, latency="low")
    s.start()
    return s


def _reinit_portaudio() -> None:
    import sounddevice as sd                                      # noqa: PLC0415
    sd._terminate()                                               # noqa: SLF001 — the only way PortAudio reads the device list again
    sd._initialize()                                              # noqa: SLF001


# ── a network output: the Enclave on skippy-core (core/install/enclave.sh) ─────────────────────────────────────────────────────
#   hub ──raw PCM, 48 kHz s16 stereo, tcp :4953──▶ snapserver ──▶ snapclient ──▶ PipeWire ──A2DP──▶ Enclave
# skippy-core's keeper runs snapclient only while the speaker is connected, so "snapserver has a connected client" = "it plays".
# That answer is kept fresh by a poller thread: net_find never blocks the mixer, which calls it from its own thread.
NET_STATUS_PORT = 1780            # snapserver's JSON-RPC (tailnet only)
NET_POLL_S = 3.0
NET_LATENCY_S = 0.3               # snapserver's buffer (core/install/enclave.sh): the part known up front; the rest is measured
NET_DITHER_LSB = 3                # a noise floor ~80 dB down, never exact digital zero: the Enclave's amp gated itself on silence and woke with a
                                  # scratch before every sound (the Captain, 2026-11-01). SATELLITE_NET_DITHER=0 turns it off


def is_net(part) -> bool:
    return isinstance(part, str) and part.startswith("tcp://")


def _host_port(url: str) -> tuple[str, int]:
    hp = url[len("tcp://"):].split("/", 1)[0].split("?", 1)[0]
    host, _, port = hp.rpartition(":")
    return host, int(port)


def snap_playing(host: str, timeout: float = 2.0) -> str | None:
    """The name of a snapclient connected to the snapserver on `host` (the speaker plays), or None."""
    import json                                                   # noqa: PLC0415
    import urllib.request                                         # noqa: PLC0415
    body = json.dumps({"id": 1, "jsonrpc": "2.0", "method": "Server.GetStatus"}).encode()
    req = urllib.request.Request(f"http://{host}:{NET_STATUS_PORT}/jsonrpc", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 — a tailnet host from the vault
        status = json.loads(r.read())
    for g in status.get("result", {}).get("server", {}).get("groups", []):
        for c in g.get("clients", []):
            if c.get("connected"):
                return (c.get("host") or {}).get("name") or c.get("id") or "snapclient"
    return None


class NetWatch:
    """One poller per host: whether its speaker plays, refreshed every NET_POLL_S in the background."""
    _all: dict = {}
    _lock = threading.Lock()

    def __init__(self, host: str, check=snap_playing, start: bool = True):
        self.host, self.check = host, check
        self.playing: str | None = None
        self.ready = threading.Event()                            # set after the first answer
        if start:
            threading.Thread(target=self._loop, daemon=True, name=f"net-watch-{host}").start()

    def _loop(self) -> None:
        while True:
            try:
                self.playing = self.check(self.host)
            except Exception:                                     # noqa: BLE001 — unreachable is just "not playing"
                self.playing = None
            self.ready.set()
            time.sleep(NET_POLL_S)

    @classmethod
    def of(cls, host: str) -> "NetWatch":
        with cls._lock:
            if host not in cls._all:
                cls._all[host] = cls(host)
            return cls._all[host]


def net_find(part: str):
    """(the url, a name) while the speaker behind it plays; None otherwise. Never waits: the first call starts the poller."""
    host, _ = _host_port(part)
    w = NetWatch.of(host)
    w.ready.wait(2.5)                                             # only the first call waits (the hub's start), for the first answer
    return (part, f"{w.playing} on {host}") if w.playing else None


class NetStream:
    """An output stream to a network speaker: sounddevice's callback contract, paced by this PC's clock (snapclient keeps the speaker on
    snapserver's; the drift between them is snapcast's to absorb). Goes dead (`alive` False: the router reopens it later) when the
    connection breaks or the speaker stops playing."""

    def __init__(self, url: str, callback, connect=None, clock=time.monotonic):
        import socket                                             # noqa: PLC0415
        self.url, self.callback, self.clock = url, callback, clock
        self.host, self.port = _host_port(url)
        self.sock = (connect or (lambda: socket.create_connection((self.host, self.port), timeout=3)))()
        self.sock.settimeout(1.0)
        self.latency = NET_LATENCY_S
        self.alive = True
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="net-out").start()

    def _loop(self) -> None:
        import os                                                 # noqa: PLC0415
        np = _np()
        buf = np.zeros((BLOCK, 2), dtype="float32")
        lsb = int(os.environ.get("SATELLITE_NET_DITHER", NET_DITHER_LSB))
        rng = np.random.default_rng()
        nxt = self.clock()
        while not self._stop.is_set():
            self.callback(buf, BLOCK, None, None)
            try:
                pcm = np.clip(buf, -1.0, 1.0) * 32767
                if lsb:                                           # triangular dither: two uniform draws, mean zero
                    pcm += rng.uniform(-lsb, lsb, pcm.shape) + rng.uniform(-lsb, lsb, pcm.shape)
                self.sock.sendall(np.clip(pcm, -32768, 32767).astype("<i2").tobytes())
            except OSError:
                break
            if not NetWatch.of(self.host).playing:
                break                                             # the speaker went off: closed, so the room's voice moves elsewhere
            nxt += BLOCK_S
            pause = nxt - self.clock()
            if pause > 0:
                time.sleep(pause)
            elif pause < -1.0:
                nxt = self.clock()
        self.alive = False

    def stop(self) -> None:
        self._stop.set()
        self.alive = False

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


_ROUTER: Router | None = None
_ROUTER_LOCK = threading.Lock()


def used_kinds(env: dict) -> set[str]:
    """Every output the house plays on: each room's voice (SATELLITE_OUT), the routines' (SATELLITE_ANNOUNCE_OUT), the house stereo
    (SKIPPY_STEREO_OUT) and every place music can be sent to (SKIPPY_PLACES): the Sony stays "downstairs" with no pod there (2026-11-01)."""
    import speaker_out                                            # noqa: PLC0415
    used = set()
    for part in (env.get("SATELLITE_OUT") or "").split(";"):
        if "=" in part:
            used |= set(speaker_out.split_kinds(part.split("=", 1)[1]))
    used |= set(speaker_out.announce_kinds(env))
    used |= set(speaker_out.split_kinds(env.get("SKIPPY_STEREO_OUT", "hdmi")))
    return used | {k for ks in speaker_out.places(env).values() for k in ks}


def shared(env: dict, log=print) -> Router:
    """The one router of this hub process: every PC output any room names (SATELLITE_OUT), with its delay."""
    global _ROUTER
    import speaker_out                                            # noqa: PLC0415
    with _ROUTER_LOCK:
        if _ROUTER is None:
            parts, delays = speaker_out.output_parts(env), speaker_out.output_delays(env)
            used = used_kinds(env)
            import sync                                           # noqa: PLC0415
            solo = speaker_out.one_voice(env)
            if solo:                                              # the PC output is the room's only voice: nothing to wait for, just a jitter cushion
                align = sync.saved_align(used)                    # several outputs in use: the faster ones wait for the slowest (sync_outputs.py)
                for k in used:                                    # every output the house plays on, so 'everywhere' lines up too
                    delays[k] = float(env.get("SATELLITE_SOLO_DELAY", str(SOLO_DELAY_S))) + align.get(k, 0.0)
            else:
                delays.update(sync.saved_delays())                # what the last calibration settled on beats the vault's starting guess
            _ROUTER = Router([(k, parts[k], delays.get(k, 0.0)) for k in parts if k in used], log=log, prefer=speaker_out.prefer_chains(env))
            _ROUTER.calibrator = sync.for_router(_ROUTER, env, log, solo=solo)   # the PC's microphone hears each reply: when it really sounded
            if _ROUTER.sinks:
                _ROUTER.start()                                   # open the outputs now: PortAudio's first device scan takes seconds
        return _ROUTER
