"""Skippy's face, drawn on skippy-pc instead of on the room box, and sent to the box as JPEG frames (prototype).

The box's processor can only draw its animated face at about 5-10 frames a second; a PC draws it in a few milliseconds. The box's side is
just "fetch the next picture and show it" (the `stream_start` action in rooms/skippy-room.yaml). `feed.jpeg(name)` makes the picture
for one moment; media.py serves it at /avatar/<name>.jpg.
"""
from __future__ import annotations

import io
import math
import time

from PIL import Image, ImageDraw

W = H = 480


class AvatarFeed:
    def __init__(self, log=print):
        self.log = log
        self.n = 0
        self.t0 = time.time()
        self.window = (time.time(), 0)

    def jpeg(self, name: str = "test", quality: int = 72) -> bytes:
        now = time.time()
        self.n += 1
        t, c = self.window
        if now - t >= 5:
            self.log(f"[avatar] {name}: {c / (now - t):.1f} frames/s served, last frame {self.last_ms:.1f} ms to make" if c else f"[avatar] {name}: idle")
            self.window = (now, 0)
        self.window = (self.window[0], self.window[1] + 1)
        t0 = time.perf_counter()
        im = Image.new("RGB", (W, H), (8, 4, 20))
        d = ImageDraw.Draw(im)
        x = int((math.sin(now * 2.2) * 0.5 + 0.5) * (W - 120))
        d.rectangle((x, 160, x + 120, 320), fill=(176, 92, 255))
        d.ellipse((x + 20, 200, x + 100, 280), fill=(255, 255, 255))
        d.text((12, 12), f"frame {self.n}", fill=(255, 255, 255))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        self.last_ms = (time.perf_counter() - t0) * 1000
        return buf.getvalue()
