"""Sends a frame to a room box as UDP datagrams of compressed tiles (only the tiles that changed), for the box's skippy_stream component.

Why: the box takes only about a thousand packets a second whatever is in them, so each changed 24x20 tile is deflated (a flat-coloured tile is a few
bytes) and as many as fit are packed into one datagram: b'T', flags (1 = last datagram of the frame), then records of x u16, y u16, w u8, h u8,
clen u16 and clen bytes of raw deflate holding w*h RGB565 pixels (big-endian: the panel driver's fast path).
"""
from __future__ import annotations

import socket
import struct
import zlib

import numpy as np

W = H = 480
TW, TH = 24, 20
MAX_DATAGRAM = 1400


def to565(rgb: np.ndarray) -> np.ndarray:
    r, g, b = rgb[..., 0].astype(np.uint16), rgb[..., 1].astype(np.uint16), rgb[..., 2].astype(np.uint16)
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


class TileSender:
    def __init__(self, host: str, port: int = 10803):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
        self.prev: np.ndarray | None = None
        self.bytes_last = 0

    def send(self, rgb: np.ndarray, full: bool = False) -> int:
        """Send what changed since the last frame (everything the first time, or when `full`). Returns the number of tiles sent."""
        cur = to565(rgb)
        if self.prev is None or full:
            changed = np.ones((H // TH, W // TW), bool)
        else:
            changed = (cur != self.prev).reshape(H // TH, TH, W // TW, TW).any(axis=(1, 3))
        self.prev = cur
        ys, xs = np.nonzero(changed)
        be = cur.astype(">u2")
        packet, sent = bytearray(b"T\x00"), 0
        self.bytes_last = 0

        def flush(last: bool):
            nonlocal packet
            packet[1] = 1 if last else 0
            self.sock.sendto(bytes(packet), self.addr)
            self.bytes_last += len(packet)
            packet = bytearray(b"T\x00")

        for ty, tx in zip(ys, xs):
            raw = np.ascontiguousarray(be[ty * TH:(ty + 1) * TH, tx * TW:(tx + 1) * TW]).tobytes()
            c = zlib.compressobj(1, zlib.DEFLATED, -15)
            data = c.compress(raw) + c.flush()
            rec = struct.pack("<HHBBH", tx * TW, ty * TH, TW, TH, len(data)) + data
            if len(packet) + len(rec) > MAX_DATAGRAM:
                flush(False)
            packet += rec
            sent += 1
        flush(True)                                  # the last datagram of the frame (empty when nothing changed)
        return sent
