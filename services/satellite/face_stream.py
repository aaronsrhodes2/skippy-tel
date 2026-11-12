"""Streams Skippy's face to a room box: the hub draws it (face_render.py) and sends the box only the tiles that changed (rooms/components/skippy_stream).

Wire format v2 (little-endian), one frame = one or more UDP datagrams of at most MAX_DATAGRAM bytes:
    'K', 2, flags (bit 0: the last datagram of the frame), frame u16, seq u16 (counts every datagram: the box counts the gaps), then records
    x u16, y u16, w u8, h u8, n u16, data:  n & 0x8000: raw, w*h*2 bytes follow (n & 0x7fff == w*h*2), RGB565 big-endian words (the panel's own order);
                                            n & 0x4000: solid, 2 bytes follow (n & 0x3fff == 2): one word for the whole tile (no inflating on the box);
                                            n & 0x2000: runs, (count u8, word u16 big-endian) triples covering exactly w*h words, used when it is under half the raw size;
                                            else: n bytes of raw deflate (the sender no longer uses it: inflating was the box's slowest step, runs cost it almost nothing).
The box keeps a frame's records until its last datagram and draws them together; if a datagram is lost it draws what it has, so the sender also resends a
rolling slice of the screen every frame (a lost tile heals in a few seconds) and a full keyframe whenever the box says it took the screen over.
A frame is sent at least every HEARTBEAT_S even when nothing changed (the box hands the screen back to its own drawing 1.2 s after the last datagram).
`decode_datagram` is the same validation the box does, kept here so the format is tested without a box.
"""
from __future__ import annotations

import json
import socket
import struct
import sys
import threading
import time
import zlib
from collections import deque
from pathlib import Path

import numpy as np

from face_render import Face, W, H, to565

TW = TH = 16
COLS, ROWS = W // TW, H // TH
MAX_DATAGRAM = 1400
HEARTBEAT_S = 0.3
REFRESH_TILES = 8                                  # resent every frame, round robin over the whole screen
STREAM_PORT = 10803
LOSS_HOLD_S = 3.0                                  # how long after the last lost datagram the stream resends (at full rate: the Captain, 2026-11-12, wants the frame rate kept)
RESEND_FRAMES = 3                                  # while losing: the tiles of this many past frames go again with each new one
LOSS_REFRESH_TILES = 16                            # and this many more of the round-robin refresh


def deflate(raw: bytes) -> bytes:
    return zlib.compress(raw, 1)[2:-4]            # zlib minus its 2-byte header and 4-byte adler: raw deflate


def rle(tile: np.ndarray) -> bytes:
    """A tile (h x w words) as (count u8, word u16 big-endian) triples; a run is cut at 255."""
    flat = tile.reshape(-1)
    starts = np.concatenate(([0], np.flatnonzero(flat[1:] != flat[:-1]) + 1))
    lens = np.diff(np.concatenate((starts, [flat.size])))
    vals = flat[starts]
    if lens.max() > 255:                                                            # (a run that long is a solid tile: cut anyway so it is always valid)
        cut_l, cut_v = [], []
        for ln, v in zip(lens.tolist(), vals.tolist()):
            while ln > 255:
                cut_l.append(255); cut_v.append(v); ln -= 255
            cut_l.append(ln); cut_v.append(v)
        lens, vals = np.array(cut_l), np.array(cut_v)
    out = np.empty((len(lens), 3), np.uint8)
    out[:, 0], out[:, 1], out[:, 2] = lens, (vals >> 8) & 255, vals & 255
    return out.tobytes()


class Encoder:
    """Turns successive frames (H x W RGB565) into datagrams carrying only what changed since the last one that was sent."""

    def __init__(self):
        self.sent: np.ndarray | None = None       # what the box shows (as far as we know)
        self.frame = 0
        self.cursor = 0
        self.keyframes = 0                          # frames still to be sent whole
        self.seq = 0
        self.recent: deque = deque(maxlen=RESEND_FRAMES)   # the tiles each of the last few frames carried (a lost datagram is one of these)
        self.resend = False                                # the air is dropping datagrams: send those again, and refresh faster

    def keyframe(self, n: int = 1) -> None:
        self.keyframes = max(self.keyframes, n)

    def encode(self, cur565: np.ndarray) -> list[bytes]:
        if self.sent is None:
            self.sent = np.zeros_like(cur565)
            self.keyframes = max(self.keyframes, 1)
        if self.keyframes > 0:
            self.keyframes -= 1
            mask = np.ones((ROWS, COLS), bool)
        else:
            mask = (cur565 != self.sent).reshape(ROWS, TH, COLS, TW).any(axis=(1, 3))
            new = mask.copy()
            if self.resend:                                # loss: the frame rate stays, the last frames' tiles go again (a lost one heals in a frame or two)
                for m in self.recent:
                    mask |= m
            for _ in range(REFRESH_TILES + (LOSS_REFRESH_TILES if self.resend else 0)):
                mask[self.cursor // COLS, self.cursor % COLS] = True
                self.cursor = (self.cursor + 1) % (ROWS * COLS)
            self.recent.append(new)
        be = cur565.astype(">u2")
        tiles = cur565.reshape(ROWS, TH, COLS, TW)
        solid = tiles.min(axis=(1, 3)) == tiles.max(axis=(1, 3))
        self.frame = (self.frame + 1) & 0xFFFF
        out: list[bytearray] = []
        def header() -> bytearray:
            h = bytearray(b"K\x02\x00" + struct.pack("<HH", self.frame, self.seq))
            self.seq = (self.seq + 1) & 0xFFFF
            return h
        cur = header()
        for ty, tx in zip(*np.nonzero(mask)):
            tile = be[ty * TH:(ty + 1) * TH, tx * TW:(tx + 1) * TW]
            raw = np.ascontiguousarray(tile).tobytes()
            if solid[ty, tx]:
                n, data = 0x4000 | 2, raw[:2]
            else:
                runs = rle(tile)
                if len(runs) * 2 < len(raw):
                    n, data = 0x2000 | len(runs), runs
                else:
                    n, data = 0x8000 | len(raw), raw
            rec = struct.pack("<HHBBH", tx * TW, ty * TH, TW, TH, n) + data
            if len(cur) + len(rec) > MAX_DATAGRAM:
                out.append(cur)
                cur = header()
            cur += rec
        cur[2] |= 1                                   # the last datagram of the frame (just the header when nothing changed)
        out.append(cur)
        ys, xs = np.nonzero(mask)
        for ty, tx in zip(ys, xs):
            self.sent[ty * TH:(ty + 1) * TH, tx * TW:(tx + 1) * TW] = cur565[ty * TH:(ty + 1) * TH, tx * TW:(tx + 1) * TW]
        return [bytes(d) for d in out]


def decode_datagram(buf: bytes, fb: np.ndarray, stage: list | None = None) -> dict:
    """What the box does with one datagram, on `fb` (H x W uint16 holding the screen's words as the sender wrote them). Returns counts; never raises."""
    res = {"tiles": 0, "bad": 0, "last": False}
    if len(buf) < 7 or buf[0:1] != b"K" or buf[1] != 2:
        res["bad"] += 1
        return res
    res["last"] = bool(buf[2] & 1)
    p, end = 7, len(buf)
    while end - p >= 8:
        x, y, w, h, n = struct.unpack_from("<HHBBH", buf, p)
        p += 8
        raw = bool(n & 0x8000)
        solid = not raw and bool(n & 0x4000)
        runs = not raw and not solid and bool(n & 0x2000)
        ln = n & 0x7FFF if raw else (n & 0x3FFF if solid else (n & 0x1FFF if runs else n))
        if ln > end - p:
            res["bad"] += 1
            break
        ok = 0 < w <= 64 and 0 < h <= 64 and x + w <= W and y + h <= H
        data = buf[p:p + ln]
        p += ln
        if ok and raw:
            ok = ln == w * h * 2
        if ok and solid:
            ok = ln == 2
        if ok and runs:
            ok = ln % 3 == 0 and sum(data[i] for i in range(0, ln, 3)) == w * h and all(data[i] for i in range(0, ln, 3))
        if ok:
            try:
                if raw:
                    px = data
                elif solid:
                    px = data * (w * h)
                elif runs:
                    px = b"".join(data[i + 1:i + 3] * data[i] for i in range(0, ln, 3))
                else:
                    px = zlib.decompressobj(-15).decompress(data, w * h * 2 + 1)
                ok = len(px) == w * h * 2
            except zlib.error:
                ok = False
        if ok:
            fb[y:y + h, x:x + w] = np.frombuffer(px, ">u2").reshape(h, w)
            res["tiles"] += 1
        else:
            res["bad"] += 1
    return res


class FaceStreamer(threading.Thread):
    """One box's face: ticks the renderer every 50 ms (as the box's own drawing does), sends what changed, paces the datagrams so the Wi-Fi is not flooded."""

    def __init__(self, host: str, log=print, name: str = "", port: int = STREAM_PORT, rate: float = 900_000.0, send=None):
        super().__init__(daemon=True, name=f"face-stream-{name}")
        self.host, self.port, self.log, self.label, self.rate = host, port, log, name, rate
        self.face = Face()
        self.enc = Encoder()
        self.stop_flag = threading.Event()
        self.sock = None if send else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.send = send or (lambda d: self.sock.sendto(d, (self.host, self.port)))
        self.stats = {"frames": 0, "bytes": 0, "render_ms": 0.0, "late": 0}
        self.live = False
        self.loss_total: int | None = None                # the box's count of datagrams that never arrived (its `Stream loss` sensor)
        self.loss_until = 0.0                              # while the air is bad (loss seen lately) the stream runs at half rate
        self.degraded = False
        self.recoveries = 0
        self._start_keys = [0.0, 0.2, 0.6, 1.5]

    # ── inputs, from the hub's event loop ──
    def set_box_state(self, state: str, talking: bool, mic_off: bool = False, spk_off: bool = False) -> None:
        """The box's `Face` text sensor ("<state>,<talking>"): the box knows when the audio starts, so the mouth follows it."""
        f = self.face
        f.set_keys(mic_off, spk_off)
        if state != f.state_now:
            f.set_state(state)
        if talking and not f.talking:
            f.talk_t0 = self.now_ms()
        if f.talking and not talking:
            f.mouth = ""
        f.talking = talking

    def note_loss(self, total: int, now: float | None = None) -> None:
        """The box's loss counter grew: the Wi-Fi is dropping datagrams. Slow down for LOSS_HOLD_S after the last loss."""
        now = time.monotonic() if now is None else now
        if self.loss_total is not None and total != self.loss_total and (total - self.loss_total) % 4294967296 < 2147483648:
            self.loss_until = now + LOSS_HOLD_S
        self.loss_total = total

    def slow_now(self, now: float) -> bool:
        """True while the air is dropping datagrams: the encoder resends recent tiles and refreshes faster (the frame rate is NOT cut). The first call after the
        burst ends sends the whole face again (a half-drawn face never lingers)."""
        bad = now < self.loss_until
        if self.degraded and not bad:
            self.enc.keyframe(2)
            self.recoveries += 1
        self.degraded = bad
        self.enc.resend = bad
        return bad

    def set_music(self, heard: bool) -> None:
        """The room has music on and nothing else (the hub knows): in rest mode he dances to it now and then."""
        self.face.music_heard = bool(heard)

    def set_live(self, live: bool) -> None:
        """The box says it has taken the screen over: send it the whole picture now."""
        if live and not self.live:
            self.enc.keyframe(2)
        self.live = live

    @staticmethod
    def now_ms() -> int:
        return int(time.monotonic() * 1000)

    def stop(self) -> None:
        self.stop_flag.set()

    # ── the loop ──
    def run(self) -> None:
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)             # sleeps of a millisecond, not fifteen
        except Exception:                                      # noqa: BLE001 — not Windows
            pass
        t_start = time.monotonic()
        next_tick = t_start
        last_send = 0.0
        last_log = t_start
        keys = list(self._start_keys)
        while not self.stop_flag.is_set():
            now = time.monotonic()
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.01))
                continue
            next_tick += 0.05
            self.slow_now(now)
            self._tick_n = getattr(self, "_tick_n", 0) + 1
            if now - next_tick > 0.25:                         # fell far behind (a stall): do not run ticks to catch up
                next_tick = now + 0.05
                self.stats["late"] += 1
            t0 = time.perf_counter()
            try:
                self.face.tick(self.now_ms())
                img = to565(self.face.render())
                while keys and now - t_start >= keys[0]:
                    keys.pop(0)
                    self.enc.keyframe(1)
                dg = self.enc.encode(img)
            except Exception as e:                             # noqa: BLE001 — a drawing bug must not stop the stream's heartbeat
                self.log(f"[{self.label}] (face stream: {type(e).__name__}: {e})")
                time.sleep(0.5)
                continue
            self.stats["render_ms"] += (time.perf_counter() - t0) * 1000
            only_header = len(dg) == 1 and len(dg[0]) == 7
            if only_header and now - last_send < HEARTBEAT_S:
                continue
            last_send = now
            self._send_paced(dg)
            self.stats["frames"] += 1
            if now - last_log >= 10:
                s = self.stats
                self.log(f"[{self.label}] face stream: {s['frames'] / (now - last_log):.1f} frames/s, {s['bytes'] / 1024 / (now - last_log):.0f} KB/s, "
                         f"render {s['render_ms'] / max(1, s['frames']):.1f} ms, late {s['late']}")
                self.stats.update(frames=0, bytes=0, render_ms=0.0, late=0)
                last_log = now

    def _send_paced(self, datagrams: list[bytes]) -> None:
        t = time.perf_counter()
        for d in datagrams:
            try:
                self.send(d)
            except OSError:
                return
            self.stats["bytes"] += len(d)
            t += len(d) / self.rate
            wait = t - time.perf_counter()
            if wait > 0.002:
                time.sleep(wait)


class _RemoteFace:
    """What the link does to `streamer.face`, forwarded to the child that really draws it."""

    def __init__(self, owner: "RemoteFaceStreamer"):
        object.__setattr__(self, "_o", owner)

    def __setattr__(self, name: str, value) -> None:
        if name in ("mouth", "emote"):
            self._o._send(name, value)
        else:
            raise AttributeError(name)

    def set_session(self, on: bool) -> None:
        self._o._send("session", bool(on))

    def set_window(self, ms: int) -> None:
        self._o._send("window", int(ms))

    def set_level(self, level: float) -> None:
        self._o._send("level", round(float(level), 4))

    def add_convo(self, who: int, text: str) -> None:
        self._o._send("convo", int(who), str(text))


class RemoteFaceStreamer:
    """The same surface as FaceStreamer for the link, but the face is drawn and sent by a child process of its own.

    Why: the drawing runs 20 times a second and the hub's own work (a clip starting: the voice, the router, the PC outputs) held the hub's Python
    long enough to make frames late, which shows as the can jumping when Skippy starts to speak. In its own process nothing of the hub's holds it up.
    The child reads one JSON line per input from its stdin and ends when stdin closes, so a hub that is killed takes the child with it (no stray
    second stream); its log lines go to the same place as the hub's."""

    def __init__(self, host: str, log=print, name: str = "", port: int = STREAM_PORT, rate: float = 900_000.0):
        self.host, self.port, self.log, self.label, self.rate = host, port, log, name, rate
        self.face = _RemoteFace(self)
        self.proc = None
        self._lock = threading.Lock()

    def start(self) -> None:
        import subprocess
        import sys
        self.proc = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "--child", self.host, str(self.port), self.label, str(self.rate)],
                                     stdin=subprocess.PIPE, cwd=str(Path(__file__).resolve().parent))

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _send(self, *msg) -> None:
        if not self.alive:
            return
        with self._lock:
            try:
                self.proc.stdin.write((json.dumps(msg) + "\n").encode())
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass

    def set_box_state(self, state: str, talking: bool, mic_off: bool = False, spk_off: bool = False) -> None:
        self._send("state", state, bool(talking), bool(mic_off), bool(spk_off))

    def set_live(self, live: bool) -> None:
        self._send("live", bool(live))

    def set_music(self, heard: bool) -> None:
        self._send("music", bool(heard))

    def note_loss(self, total: int) -> None:
        self._send("loss", int(total))

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()                       # the child sees the end of its input and exits
        except (OSError, ValueError):
            pass
        try:
            self.proc.wait(timeout=3)
        except Exception:                                  # noqa: BLE001 — it did not leave in time: end it
            self.proc.terminate()


def _child_main(host: str, port: int, name: str, rate: float) -> None:
    s = FaceStreamer(host, log=lambda m: print(time.strftime("%H:%M:%S ") + m, flush=True), name=name, port=port, rate=rate)
    s.start()
    for line in sys.stdin:
        try:
            msg = json.loads(line)
            kind = msg[0]
            if kind == "state":
                s.set_box_state(msg[1], msg[2], msg[3], msg[4])
            elif kind == "live":
                s.set_live(msg[1])
            elif kind == "loss":
                s.note_loss(msg[1])
            elif kind == "music":
                s.set_music(msg[1])
            elif kind == "mouth":
                s.face.mouth = msg[1]
            elif kind == "emote":
                s.face.emote = msg[1]
            elif kind == "session":
                s.face.set_session(msg[1])
            elif kind == "window":
                s.face.set_window(msg[1])
            elif kind == "level":
                s.face.set_level(msg[1])
            elif kind == "convo":
                s.face.add_convo(msg[1], msg[2])
        except Exception as e:                             # noqa: BLE001 — one bad message must not end the stream
            print(f"[{name}] (face stream child: {type(e).__name__}: {e})", flush=True)
    s.stop()


if __name__ == "__main__":
    if len(sys.argv) >= 6 and sys.argv[1] == "--child":
        _child_main(sys.argv[2], int(sys.argv[3]), sys.argv[4], float(sys.argv[5]))
