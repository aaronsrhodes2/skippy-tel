"""Streams Skippy's face to a room box: the hub draws it (face_render.py) and sends the box only the tiles that changed (rooms/components/skippy_stream).

Wire format v2 (little-endian), one frame = one or more UDP datagrams of at most MAX_DATAGRAM bytes:
    'K', 2, flags (bit 0: the last datagram of the frame; bit 1: the records that follow are raw-deflated, this datagram by itself), frame u16,
    seq u16 (counts every datagram: the box counts the gaps), then records
    x u16, y u16, w u8, h u8, n u16, data:  n & 0x8000: raw, w*h*2 bytes follow (n & 0x7fff == w*h*2), RGB565 big-endian words (the panel's own order);
                                            n & 0x4000: solid, 2 bytes follow (n & 0x3fff == 2): one word for the whole tile (no inflating on the box);
                                            n & 0x2000: runs, (count u8, word u16 big-endian) triples covering exactly w*h words, used when it is under half the raw size;
                                            else: n bytes of raw deflate (the sender no longer uses it: inflating was the box's slowest step, runs cost it almost nothing).
The box keeps a frame's records until its last datagram and draws them together; if a datagram is lost it draws what it has, so the sender also resends a
rolling slice of the screen every frame (a lost tile heals in a few seconds) and a full keyframe whenever the box says it took the screen over.
A frame is sent at least every HEARTBEAT_S even when nothing changed (the box hands the screen back to its own drawing 1.2 s after the last datagram).
A box whose firmware says it can (`Stream caps` bit 0) gets deflated datagrams (2026-11-12): tiles go raw or solid and each datagram is deflated by
itself (a lost one still costs only its own tiles), which is about 2.7 times fewer bytes and datagrams for the same frames (tools/face_codec_probe.py).
`decode_datagram` is the same validation the box does, kept here so the format is tested without a box.
"""
from __future__ import annotations

import json
import queue
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
RATE_START, RATE_MIN, RATE_STEP = 300_000.0, 100_000.0, 15_000.0   # the pace (bytes a second): where it starts, its floor, how fast it creeps back up
BURST_S = 0.1                                      # the allowance may save up this much of the pace (one frame goes out in a burst, the average holds)
CEILING_S = 600.0                                  # how long the pace remembers where the link last overflowed
MILD_LOSS = 0.03
HEAVY_LOSS, LIGHT_LOSS = 0.08, 0.01              # share of datagrams lost in a second: over HEAVY the link is full (slow down); up to LIGHT the pace may rise
ZRAW_MAX = 11000                                  # the most raw record bytes in one deflated datagram (the box inflates into 12 KB)
ZLEVEL = 6
COARSE_S = 300.0                                  # after the link overflowed, a talking face is drawn in 4 bits a colour for this long (a frame a fifth smaller)
COARSE_MASK = 0xF79E                               # RGB565 with the low bit of each colour dropped (white stays white to the eye)


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
        self.zip = False                                   # the box reads deflated datagrams (its `Stream caps`)
        self.zper = 0.35                                   # deflated bytes per raw byte, lately (how many records to try in one datagram)

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
        if self.zip:
            recs = []
            for ty, tx in zip(*np.nonzero(mask)):
                raw = np.ascontiguousarray(be[ty * TH:(ty + 1) * TH, tx * TW:(tx + 1) * TW]).tobytes()
                n, data = (0x4000 | 2, raw[:2]) if solid[ty, tx] else (0x8000 | len(raw), raw)      # deflate does better on raw pixels than on runs
                recs.append(struct.pack("<HHBBH", tx * TW, ty * TH, TW, TH, n) + data)
            for body in self._zchunks(recs):
                h = header()
                h[2] |= 2
                out.append(h + body)
            if not out:
                out.append(header())
            out[-1][2] |= 1
            ys, xs = np.nonzero(mask)
            for ty, tx in zip(ys, xs):
                self.sent[ty * TH:(ty + 1) * TH, tx * TW:(tx + 1) * TW] = cur565[ty * TH:(ty + 1) * TH, tx * TW:(tx + 1) * TW]
            return [bytes(d) for d in out]
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


    def _zchunks(self, recs: list[bytes]) -> list[bytes]:
        """The records as deflated bodies, each fitting one datagram and inflating to at most ZRAW_MAX: as many records as the recent ratio says will
        fit, fewer when they do not."""
        cap = MAX_DATAGRAM - 7
        out, i = [], 0
        while i < len(recs):
            budget = min(ZRAW_MAX, int(cap / max(self.zper, 0.02) * 0.85))
            j, size = i, 0
            while j < len(recs) and (j == i or size + len(recs[j]) <= budget):
                size += len(recs[j])
                j += 1
            while True:
                raw = b"".join(recs[i:j])
                c = zlib.compressobj(ZLEVEL, zlib.DEFLATED, -15)
                body = c.compress(raw) + c.flush()
                if len(body) <= cap or j - i == 1:
                    break
                j = i + max(1, (j - i) * 2 // 3)
            self.zper = 0.8 * self.zper + 0.2 * len(body) / max(1, len(raw))
            out.append(body)
            i = j
        return out


def decode_datagram(buf: bytes, fb: np.ndarray, stage: list | None = None) -> dict:
    """What the box does with one datagram, on `fb` (H x W uint16 holding the screen's words as the sender wrote them). Returns counts; never raises."""
    res = {"tiles": 0, "bad": 0, "last": False}
    if len(buf) < 7 or buf[0:1] != b"K" or buf[1] != 2:
        res["bad"] += 1
        return res
    res["last"] = bool(buf[2] & 1)
    if buf[2] & 2:                                                # a deflated datagram: inflate it (to at most what the box holds), then the same records
        try:
            d = zlib.decompressobj(-15)
            body = d.decompress(buf[7:], 12 * 1024 + 1)
            if len(body) > 12 * 1024 or not d.eof:
                raise zlib.error("too big or cut short")
        except zlib.error:
            res["bad"] += 1
            return res
        buf = buf[:7] + body
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
        self.host, self.port, self.log, self.label = host, port, log, name
        self.rate_max = rate                               # bytes a second at most; `rate` is the pace now, steered by the box's loss reports (note_loss)
        self.rate = min(rate, RATE_START)
        self.sent_dg, self._sent_mark = 0, 0
        self.sent_bytes, self._bytes_mark, self._t_mark = 0, 0, 0.0
        self._cut_until = 0.0
        self.coarse_until = -1e9                           # while now < this and he is talking, the face is drawn in coarser colour
        self.coarse_frames = 0
        self.tokens, self._tok_t = 0.0, 0.0                # the allowance (bytes) the pace has saved up, and when it was last topped up
        self._out: queue.Queue = queue.Queue()             # datagrams on their way out, sent evenly by a thread of their own (_sender)
        self.ceiling, self.ceiling_at = rate, -1e9        # the bytes a second at which the link last overflowed, and when (forgotten after CEILING_S)
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
        """The box's loss counter, about once a second. Measured 2026-11-12 on the living pod: the link carries about 250 datagrams a second and everything
        sent beyond that is simply lost (280 lost for 240 received), so loss is mostly OUR sending too much, and sending more to repair it makes it worse.
        So the share lost since the last report steers the pace. A lot lost (over HEAVY_LOSS): the link is full: go a little under what was really sent,
        remember that as the ceiling, do not resend (once per overflow). Some lost (over MILD_LOSS) while sending flat out: hold. A little lost while
        there was room is the air: keep the pace and send the recent tiles again for LOSS_HOLD_S. A clean second at a pace that was really used: creep
        up (slowly once near the remembered ceiling, so the stream sits just under what the link carries instead of overflowing it every few seconds)."""
        now = time.monotonic() if now is None else now
        sent = self.sent_dg - self._sent_mark
        self._sent_mark = self.sent_dg
        lost = 0
        if self.loss_total is not None and total != self.loss_total and (total - self.loss_total) % 4294967296 < 2147483648:
            lost = (total - self.loss_total) % 4294967296
        self.loss_total = total
        share = min(1.0, lost / sent) if sent >= 20 else (1.0 if lost and not sent else 0.0)
        used = (self.sent_bytes - self._bytes_mark) / max(0.2, now - self._t_mark) if self._t_mark else 0.0     # bytes a second really sent since the last report
        self._bytes_mark, self._t_mark = self.sent_bytes, now
        if sent < 20 and not lost:
            return                                             # nothing much was sent: no verdict on the link
        self.stats["lost"] = self.stats.get("lost", 0) + lost
        near = used >= 0.75 * self.rate                        # the stream was sending about as fast as its pace allows
        if sent >= 20 and share > HEAVY_LOSS:                  # the link is full: what was really sent is its ceiling; go a little under it, do not resend into it
            if now >= self._cut_until:                         # (one cut per overflow: the next report still shows losses from before the cut took hold)
                base = max(RATE_MIN, min(self.rate, used) if used > 0 else self.rate)
                self.ceiling, self.ceiling_at = base, now
                self.rate = max(RATE_MIN, base * max(0.7, 1.0 - share) * 0.95)
                self._cut_until = now + 1.5
                self.coarse_until = now + COARSE_S
            self.loss_until = 0.0
        elif lost and share > MILD_LOSS and near:
            pass                                               # some lost while sending flat out: the link is about full: hold the pace, do not add to it
        elif lost:
            self.loss_until = now + LOSS_HOLD_S                # the air dropped a few while the link had room: repair them
        elif sent >= 20 and used >= 0.8 * self.rate:           # only a pace that was really used and came through clean is evidence the link carries more
            close = now - self.ceiling_at < CEILING_S and self.rate >= 0.9 * self.ceiling
            self.rate = min(self.rate_max, self.rate + (RATE_STEP / 15 if close else RATE_STEP))   # near where it last overflowed: feel the way up, do not jump

    def can_send(self, now: float) -> bool:
        """Top up the allowance by the pace and say whether a frame may go now. A frame is sent in one quick burst and its bytes are taken off the
        allowance; while that is under zero the tick sends nothing (the next frame carries what changed since). So the average stays at the pace
        without the loop ever waiting on it: waiting made every frame late by the time its bytes took (13 frames a second when the link had room for
        18). The datagrams themselves leave evenly, from a thread of their own (_sender): sent in bursts, the same bytes lost three times as many."""
        dt = min(0.5, now - self._tok_t) if self._tok_t else 0.05
        self._tok_t = now
        self.tokens = min(self.rate * BURST_S, self.tokens + self.rate * dt)
        return self.tokens > 0

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

    def set_caps(self, caps: int) -> None:
        """What the box's firmware can read (its `Stream caps` sensor): bit 0 = deflated datagrams. The whole face goes again in the new form."""
        z = bool(int(caps) & 1)
        if z != self.enc.zip:
            self.enc.zip = z
            self.enc.keyframe(1)
            self.log(f"[{self.label}] face stream: {'deflated' if z else 'plain'} datagrams")

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
        threading.Thread(target=self._sender, daemon=True, name=f"face-send-{self.label}").start()
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
                if not self.can_send(now):                     # the link's allowance is spent: this tick's picture is skipped, the next one carries the change
                    self.stats["held"] = self.stats.get("held", 0) + 1
                    continue
                img = to565(self.face.render())
                if now < self.coarse_until and self.face.talking:
                    img = img & np.uint16(COARSE_MASK)
                    self.coarse_frames += 1
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
            self.tokens -= sum(len(d) for d in dg)
            for d in dg:
                self._out.put(d)
            self.stats["frames"] += 1
            if now - last_log >= 10:
                s = self.stats
                self.log(f"[{self.label}] face stream: {s['frames'] / (now - last_log):.1f} frames/s, {s['bytes'] / 1024 / (now - last_log):.0f} KB/s, "
                         f"render {s['render_ms'] / max(1, s['frames']):.1f} ms, late {s['late']}, "
                         f"lost {s.get('lost', 0)} of {s.get('dg', 0)} datagrams, pace {self.rate / 1024:.0f} KB/s, {s.get('held', 0)} ticks held")
                self.stats.update(frames=0, bytes=0, render_ms=0.0, late=0, lost=0, dg=0, held=0)
                last_log = now

    def _sender(self) -> None:
        """Sends the queued datagrams evenly, a little faster than the pace (so the queue, never more than about a frame, always drains): the link
        to a pod drops bursts it would have carried as a steady flow."""
        t = time.perf_counter()
        while not self.stop_flag.is_set():
            try:
                d = self._out.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.send(d)
            except OSError:
                continue
            self.stats["bytes"] += len(d)
            self.stats["dg"] = self.stats.get("dg", 0) + 1
            self.sent_dg += 1
            self.sent_bytes += len(d)
            t = max(t, time.perf_counter() - 0.01) + len(d) / (self.rate * 1.2)
            wait = t - time.perf_counter()
            if wait > 0.001:
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

    def set_caps(self, caps: int) -> None:
        self._send("caps", int(caps))

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
            elif kind == "caps":
                s.set_caps(msg[1])
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
