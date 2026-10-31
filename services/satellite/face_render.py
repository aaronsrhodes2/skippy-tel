"""Skippy's animated face, drawn on skippy-pc (numpy + OpenCV) instead of on the room box.

This is a line-for-line port of the 50 ms face tick in rooms/skippy-room.yaml (the `interval: 50ms` lambda: expression targets eased every tick, emotion,
tints, the five lean images of the can, the hat's gravity physics, the effects, the thinking graphics, the ears), drawing the same widgets in the same order
as the yaml's lvgl page. Nothing about how he looks is decided here: change the look in the yaml's drawing (the on-box fallback) and keep this in step.

    f = Face(); f.set_state("speaking"); f.mouth = "nBCDBCE..."; f.talking = True
    for each frame: f.tick(now_ms); rgb = f.render()      # (480, 480, 3) uint8

The inputs are the ones the box has: `state` (the face action's state), `talking`, `mouth` ("<mood letter><one char per 60 ms>"), `emote`
("<reaction>|<one letter per 100 ms>"), `in_session`, and the conversation lines. `now` is milliseconds on any steady clock.
"""
from __future__ import annotations

import math
import os
import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W = H = 480
HERE = Path(__file__).resolve().parent
BALL_DIR = HERE / "rooms" / "avatar" / "frames" / "ball"
ANGS = (-0.1396, -0.0698, 0.0, 0.0698, 0.1396)


def rgb(h: int) -> tuple:
    return (h >> 16) & 255, (h >> 8) & 255, h & 255


def u32() -> int:
    return random.getrandbits(32)


def roundf(v: float) -> float:
    return math.floor(abs(v) + 0.5) * (1 if v >= 0 else -1)


def clampi(v: float) -> int:
    i = int(v)
    return 0 if i < 0 else (479 if i > 479 else i)


def _font(size: int):
    for p in (os.environ.get("SKIPPY_FACE_FONT", ""), r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf", "DejaVuSans.ttf"):
        try:
            if p:
                return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


class Line:
    def __init__(self, width: int, color: int, opa: int = 255, rounded: bool = False):
        self.pts: list[tuple[int, int]] = []
        self.width, self.color, self.opa, self.rounded = width, color, opa, rounded

    def put(self, pts) -> None:
        self.pts = [(int(p[0]), int(p[1])) for p in pts]


class Box:
    """An lv_obj: a rectangle, rounded by `radius` (100 = a pill, drawn as an ellipse), filled with `color` at `opa`; optional border."""
    def __init__(self, w: int, h: int, color: int, opa: int = 255, radius: int = 0, border: int = 0, border_color: int = 0, hidden: bool = False):
        self.x = self.y = 0
        self.w, self.h, self.color, self.opa, self.radius = w, h, color, opa, radius
        self.border, self.border_color, self.hidden = border, border_color, hidden


class Label:
    def __init__(self, color: int):
        self.text, self.color, self.opa, self.x, self.y = "", color, 0, 0, 0


def _blend(canvas: np.ndarray, x0: int, y0: int, x1: int, y1: int, opa: int, fn) -> None:
    """Run fn(img, ox, oy) on a copy of the box and mix it in at `opa` (so the drawing code never needs to know about transparency)."""
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 <= x0 or y1 <= y0 or opa <= 0:
        return
    if opa >= 255:
        tmp = canvas[y0:y1, x0:x1].copy()
        fn(tmp, x0, y0)
        canvas[y0:y1, x0:x1] = tmp
        return
    roi = canvas[y0:y1, x0:x1]
    tmp = roi.copy()
    fn(tmp, x0, y0)
    canvas[y0:y1, x0:x1] = ((tmp.astype(np.uint16) * opa + roi.astype(np.uint16) * (255 - opa)) // 255).astype(np.uint8)


def draw_line(canvas, ln: Line) -> None:
    if ln.opa <= 0 or len(ln.pts) < 2:
        return
    xs, ys = [p[0] for p in ln.pts], [p[1] for p in ln.pts]
    pad = ln.width // 2 + 2

    def fn(img, ox, oy):
        pts = np.array([(x - ox, y - oy) for x, y in ln.pts], np.int32).reshape(-1, 1, 2)
        cv2.polylines(img, [pts], False, rgb(ln.color), ln.width, cv2.LINE_AA)
    _blend(canvas, min(xs) - pad, min(ys) - pad, max(xs) + pad + 1, max(ys) + pad + 1, ln.opa, fn)


def draw_box(canvas, b: Box) -> None:
    if b.hidden or b.opa <= 0 or b.w <= 0 or b.h <= 0:
        return

    def fn(img, ox, oy):
        x, y = b.x - ox, b.y - oy
        if b.radius >= 100 or (b.radius * 2 >= min(b.w, b.h) and b.radius > 0):
            c = (x + b.w // 2, y + b.h // 2)
            cv2.ellipse(img, c, (max(1, b.w // 2), max(1, b.h // 2)), 0, 0, 360, rgb(b.border_color if b.border else b.color), -1, cv2.LINE_AA)
            if b.border:
                cv2.ellipse(img, c, (max(1, b.w // 2 - b.border), max(1, b.h // 2 - b.border)), 0, 0, 360, rgb(b.color), -1, cv2.LINE_AA)
        else:
            cv2.rectangle(img, (x, y), (x + b.w - 1, y + b.h - 1), rgb(b.color), -1)
    _blend(canvas, b.x - 1, b.y - 1, b.x + b.w + 1, b.y + b.h + 1, b.opa, fn)


class TextCache:
    """Rendered text as RGBA tiles, kept by (text, color) so a frame does not rasterise fonts."""
    def __init__(self):
        self.font = _font(20)
        self.cache: dict = {}

    def one(self, text: str, color: int) -> np.ndarray:
        key = (text, color)
        if key not in self.cache:
            if len(self.cache) > 200:
                self.cache.clear()
            w = int(self.font.getlength(text)) + 4
            im = Image.new("RGBA", (max(w, 4), 24), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((0, 0), text, font=self.font, fill=rgb(color) + (255,))
            self.cache[key] = np.asarray(im)
        return self.cache[key]

    def wrap(self, text: str, width: int) -> list[str]:
        out, line = [], ""
        for word in text.split(" "):
            t = (line + " " + word) if line else word
            if line and self.font.getlength(t) > width:
                out.append(line)
                line = word
            else:
                line = t
        out.append(line)
        return out


def blit_rgba(canvas: np.ndarray, tile: np.ndarray, x: int, y: int, opa: int) -> None:
    th, tw = tile.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + tw), min(H, y + th)
    if x1 <= x0 or y1 <= y0 or opa <= 0:
        return
    t = tile[y0 - y:y1 - y, x0 - x:x1 - x]
    a = (t[..., 3:4].astype(np.uint16) * opa) // 255
    roi = canvas[y0:y1, x0:x1]
    canvas[y0:y1, x0:x1] = ((t[..., :3].astype(np.uint16) * a + roi.astype(np.uint16) * (255 - a)) // 255).astype(np.uint8)


class Face:
    EQS = ("E = mc^2", "a^2 + b^2 = c^2", "F = ma", "PV = nRT", "e^(i*pi) = -1", "dx/dt = v", "sin(x) / x", "x = (-b +- sqrt(D)) / 2a",
           "S >= 0", "1 + 1 = 2?", "div B = 0", "f(x) = x^2")

    def __init__(self):
        # inputs (what the box has)
        self.state_now = ""
        self.talking = False
        self.talk_t0 = 0
        self.mouth = ""
        self.emote = ""
        self.in_session = False
        # the face script's globals
        self.face_rgb = 0x6a28c9
        self.last_activity = 0
        # the convo label
        self.convo: list[tuple[str, int]] = []          # (text, colour)
        self.last_reply = ""
        self.convo_target, self.convo_y, self.convo_h = 470.0, 480.0, 0
        self.text = TextCache()
        self._convo_layer: np.ndarray | None = None
        # the ring
        self.ring_opa = [int(255 * 0.4)] * 16
        self.breath = 0
        self._next_ring = 0
        # tick statics
        self.ph = self.rot = 0.0
        self.eo, self.bh, self.ba, self.bl, self.br, self.lx, self.ly, self.sm = 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        self.mw, self.mu, self.ml, self.teethv = 22.0, 0.5, 0.5, 0.0
        self.blink_at = self.glance_at = 0
        self.gx = self.gy = 0.0
        self.as_ = self.fb_ = self.fs_ = self.fv_ = self.fh_ = 0.0
        self.tr = self.tg = self.tb = self.ta = 0.0
        self.sent_sig = -1
        self.rc = (0, 0, 0, 0)
        self.think_t0 = 0
        self.pst = ""
        self.rk, self.ramp, self.bx_, self.by_, self.prev_bx = 0.0, 3.0, 0.0, 0.0, 0.0
        self.next_beat = self.next_idle = 0
        self.wobs = 0.4
        self.lean_k = 2
        self.mg = 2.1
        self.ha = self.hv = self.hx = self.hxv = self.hy = self.hyv = 0.0
        self.hc = 0
        self.pv = 0.0
        self.nudge_at = 0
        self.ear = 0.0
        self.sl = [dict(on=False, t0=0, x=0, y=0) for _ in range(6)]
        self.prev_state_think = ""
        self.next_spawn = self.bulb_t0 = 0
        self.bulb_was = False
        self.beat = False
        self.rkv = 0.0
        self.theta = 0.0
        # the widgets, in the yaml's z-order
        self.ball_pos = (90, 80)
        self._balls = [np.asarray(Image.open(BALL_DIR / f"0{i}.png").convert("RGBA")) for i in range(5)]
        self._ball_cache: dict = {}
        mk = Line
        self.ear_fl, self.ear_fr = mk(8, 0x7d8492, 0, True), mk(8, 0x7d8492, 0, True)
        self.ear_ol, self.ear_or = mk(3, 0xe6e9ef, 0, True), mk(3, 0xe6e9ef, 0, True)
        self.hat_cast = mk(20, 0x000000, int(255 * 0.55), True)
        self.hc = 0
        self.cloth = [Box(13, 2, 0x1a1f3a, hidden=True) for _ in range(40)]
        self.hat_crease_l, self.hat_crease_r = mk(12, 0x0a0d20, int(255 * 0.7), True), mk(12, 0x0a0d20, int(255 * 0.7), True)
        self.hat_top, self.hat_bot = mk(3, 0x2e3560, 255, True), mk(4, 0x3d4478, 255, True)
        self.rose = [Box(s, s, c, radius=s // 2) for s, c in ((52, 0xf2f2f2), (42, 0xcc2b3a), (30, 0xf2f2f2), (18, 0xcc2b3a))]
        self.lace_edge, self.lace = mk(19, 0x7a5a14, 255, True), mk(13, 0xe3b53c, 255, True)
        self.rose_btn = Box(16, 16, 0xe3b53c, radius=8, border=2, border_color=0x7a5a14)
        self.white = [Box(20, 20, 0xf7f7fa, radius=100, hidden=True) for _ in range(2)]
        self.blush = [Box(50, 24, 0xff6b81, opa=0, radius=100) for _ in range(2)]
        self.mfill = mk(7, 0x0b0b0e, 255)
        self.eye_l, self.eye_r = mk(5, 0x1b1d22, 255), mk(5, 0x1b1d22, 255)
        self.brow_l, self.brow_r = mk(5, 0x1b1d22, 255), mk(5, 0x1b1d22, 255)
        self.mouth_ln = mk(5, 0x1b1d22, 255)
        self.teeth = mk(3, 0xeceef2, 0)
        self.chin = mk(4, 0x1b1d22, 0, True)
        self.smile = [mk(4, 0x1b1d22, 0, True) for _ in range(2)]
        self.frown = [mk(4, 0x1b1d22, 0, True) for _ in range(2)]
        self.furrow = [mk(4, 0x1b1d22, 0, True) for _ in range(2)]
        self.iris = [Box(20, 20, 0x2f7bd9, radius=100, border=2, border_color=0x1d4fa0, hidden=True) for _ in range(2)]
        self.sweat = Box(14, 20, 0x7dd3fc, opa=0, radius=100)
        self.vein, self.heart = mk(5, 0xe11d48, 0, True), mk(4, 0xf472b6, 0, True)
        self.pup = [Box(14, 14, 0x0b0b0e, radius=7) for _ in range(2)]
        self.glint = [Box(5, 5, 0xffffff, radius=2) for _ in range(2)]
        self.eq = [Label(0xf59e0b) for _ in range(4)]
        self.gr = [mk(3, 0x22d3ee, 0, True), mk(3, 0xf59e0b, 0, True)]
        self.bulb_fill, self.bulb_out = mk(5, 0xfacc15, 0, True), mk(3, 0xfff7b0, 0, True)
        self.rays = [mk(4, 0xfde047, 0, True) for _ in range(5)]
        self.ball_recolor = (0, 0, 0, 0)

    # ── inputs ──
    def set_state(self, state: str) -> None:
        """The box's `face` script: the colour of the nimbus, and what the state is."""
        c = 0x22d3ee if self.in_session else 0x6a28c9
        table = {"listening": 0x22d3ee, "recording": 0xff3b6b, "thinking": 0xf59e0b, "speaking": 0xb05cff, "music": 0x22c55e, "muted": 0xef4444,
                 "offline": 0x475569, "sick": 0xd6c92a}
        if state in table:
            c = table[state]
        self.face_rgb, self.state_now = c, state

    def set_session(self, on: bool) -> None:
        self.in_session = on
        if self.state_now in ("idle", ""):
            self.set_state("idle")

    def add_convo(self, who: int, text: str) -> None:
        """convo_add: a line of the conversation flows up behind him: yours (cyan, with >) and Skippy's (lavender)."""
        t = text.replace("#", " ").replace("\n", " ")
        if not t:
            return
        if who == 1:
            if self.last_reply == t:
                return
            self.last_reply = t
        line = ("#d6c2ff " if who else "#8fe9ff > ") + t + "#\n"
        self.convo.append((line, 0xd6c2ff if who else 0x8fe9ff))
        while sum(len(s) for s, _ in self.convo) > 900 and len(self.convo) > 1:
            self.convo.pop(0)
        self._layout_convo()

    def _layout_convo(self) -> None:
        rows: list[tuple[str, int]] = []
        for line, color in self.convo:
            body = line[8:-2]                                           # the colour tag is 8 characters ("#d6c2ff " / "#8fe9ff"), the rest is the text
            for r in self.text.wrap(body, 440):
                rows.append((r, color))
        self.convo_h = 23 * len(rows)
        layer = np.zeros((max(self.convo_h, 1), W, 4), np.uint8)
        for i, (r, color) in enumerate(rows):
            t = self.text.one(r, color)
            h, w = t.shape[:2]
            hh = min(h, layer.shape[0] - i * 23)
            layer[i * 23:i * 23 + hh, 20:20 + min(w, W - 20)] = t[:hh, :min(w, W - 20)]
        self._convo_layer = layer
        self.convo_target = 474 - self.convo_h

    # ── the 250 ms lamp ──
    def _ring_tick(self, now: int) -> None:
        if now < self._next_ring:
            return
        self._next_ring = now + 250
        if self.talking:
            return
        busy = self.face_rgb != 0x6a28c9 and self.face_rgb != 0x475569
        self.breath = (self.breath + (6 if busy else 2)) % 360
        s = (math.sin(self.breath * 3.14159 / 180.0) + 1.0) / 2.0
        k = 0.65 + 0.35 * s if busy else 0.45 + 0.35 * s
        base = (230, 150, 90, 45)
        self.ring_opa = [int(base[i // 4] * k) for i in range(16)]

    # ── the 50 ms face tick (rooms/skippy-room.yaml, the lambda of `interval: 50ms`) ──
    def tick(self, now: int) -> None:
        self._ring_tick(now)
        st = self.state_now
        dead = st == "offline"
        talk = self.talking
        mood, vis = "n", "X"
        env = self.mouth
        if talk:
            i = (now - self.talk_t0) // 60 + 1
            if env and "a" <= env[0] <= "z":
                mood = env[0]
            vis = env[i] if i < len(env) else "BCDBCEA"[(now // 90) % 7]
            if "0" <= vis <= "9":
                vis = "X" if vis == "0" else ("B" if vis < "3" else ("C" if vis < "6" else "D"))
        ms = {"X": (22, 0.6, 0.6, 0), "A": (22, 0.3, 0.3, 0), "B": (24, 3, 3, 1), "C": (24, 8, 8, 1), "D": (26, 13, 17, 1),
              "E": (15, 10, 10, 0), "F": (10, 7, 7, 0), "G": (24, 3, 5, 1), "H": (20, 6, 6, 0)}
        tw, tu, tl, tt = ms.get(vis, (22, 0.6, 0.6, 0))
        t_eo, t_bh, t_ba, t_bl, t_br, t_lx, t_ly, t_sm = 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3.0
        if st == "listening":
            t_eo, t_bh, t_sm = 1.25, 10, 4
        elif st == "recording":
            t_eo, t_bh, t_sm = 1.3, 14, 1
        elif st == "thinking":
            t_eo, t_bl, t_ba, t_lx, t_ly, t_sm = 0.9, 10, 0.15, -0.8 + 0.2 * math.sin(self.ph * 0.7), -0.8, -2
        elif st == "music":
            t_eo, t_bh, t_sm = 0.55, 4, 9
        elif st == "muted":
            t_eo, t_sm = 0.08, 0
        elif st == "offline":
            t_eo, t_sm, t_bh = 0.0, -3, 0
        elif st == "speaking" or talk:
            t_sm = 4
            t_bh = 3 + 5 * (vis in "DC")
            if mood == "h":
                t_eo, t_sm, t_bh = 0.75, 11, 7
            elif mood == "a":
                t_eo, t_ba, t_sm, t_br = 0.8, 0.4, 3, -4
            elif mood == "s":
                t_eo, t_bh = 1.4, 14
            elif mood == "q":
                t_bl, t_ba, t_sm, t_ly = 11, -0.1, 2, -0.3
            elif mood == "d":
                t_sm, t_ba, t_eo, t_bh = -6, -0.35, 0.85, 2
        else:
            if not dead and now > self.glance_at:
                self.gx = ((u32() % 100) / 50.0 - 1) * 0.6
                self.gy = ((u32() % 100) / 50.0 - 1) * 0.3
                self.glance_at = now + 1500 + u32() % 3500
            t_lx, t_ly = self.gx, self.gy
            if self.in_session:
                t_eo, t_bh, t_sm = 1.12, 4, 4
        # emotion
        emo, ef, estrong = "n", 0.0, False
        em = self.emote
        react = em[0] if len(em) >= 2 and em[1] == "|" else "n"
        etl = len(em) - 2 if len(em) >= 2 and em[1] == "|" else 0
        if talk and etl > 0:
            i = (now - self.talk_t0) // 100
            if i >= etl:
                i = etl - 1
            c = em[2 + i]
            estrong = "A" <= c <= "Z"
            emo = c.lower() if estrong else c
            ef = 1.0 if estrong else 0.7
        elif talk and mood != "n":
            emo, ef = mood, 0.8
        elif react != "n" and (st in ("thinking", "listening") or (st == "idle" and self.in_session)):
            emo, ef = react, 0.55
        if st == "sick":
            emo, ef = "k", 1.0
        t_as = 0.0
        g_b = g_s = g_v = g_h = 0.0
        if emo != "n":
            tab = {"h": (0.78, 7, 0, 0, 0, 11, 0, 0, 0), "e": (1.35, 16, 0, 0, 0, 12, 0, 0, 0), "l": (0.28, 10, 0, 0, 0, 14, 0, 0, 0),
                   "s": (1.45, 18, 0, 0, 0, 0, 0, 0, 0), "q": (1.05, 4, -0.08, 12, 0, 2, 0, 0.7, -0.3), "a": (0.78, 0, 0.45, 0, 0, -3, 0, 0, 0),
                   "m": (0.62, 5, 0.1, 0, 12, 8, 5, 0.6, 0), "d": (0.85, 3, -0.4, 0, 0, -7, 0, 0, 0.7), "w": (1.1, 10, -0.35, 0, 0, -4, 0, 0, 0),
                   "b": (0.45, 0, 0, 0, 0, -1, 0, 0.8, 0.2), "r": (0.9, 6, -0.2, 0, 0, 3, 0, -0.7, 0.5), "v": (1.2, 8, 0, 0, 0, 11, 0, 0, 0),
                   "k": (0.7, 2, -0.3, 0, 0, -5, 0, 0, 0.5)}
            if emo in tab:
                eo2, bh2, ba2, bl2, br2, sm2, as2, lx2, ly2 = tab[emo]
                t_eo += (eo2 - t_eo) * ef; t_bh += (bh2 - t_bh) * ef; t_ba += (ba2 - t_ba) * ef; t_bl += (bl2 - t_bl) * ef; t_br += (br2 - t_br) * ef
                t_sm += (sm2 - t_sm) * ef; t_as = as2 * ef; t_lx += (lx2 - t_lx) * ef; t_ly += (ly2 - t_ly) * ef
            if emo == "r":
                g_b = ef
            if emo == "v":
                g_b, g_h = 0.6 * ef, ef
            if emo in ("w", "k"):
                g_s = ef
            if emo == "a":
                g_v = 1.0 if estrong else 0.45 * ef
        self.fb_ += (g_b - self.fb_) * 0.25; self.fs_ += (g_s - self.fs_) * 0.25; self.fv_ += (g_v - self.fv_) * 0.3
        self.fh_ += (g_h - self.fh_) * 0.25; self.as_ += (t_as - self.as_) * 0.3
        # the can's tint
        if st == "thinking" and self.pst != "thinking":
            self.think_t0 = now
        self.pst = st
        R = G = B = A = 0.0
        if st == "offline":
            R, G, B, A = 47, 107, 255, 120
        elif st == "sick" or emo == "k":
            R, G, B, A = 222, 215, 40, 105
        elif st == "thinking":
            k = min((now - self.think_t0) / 4000.0, 1.0)
            R, G, B, A = 255, 138, 31, 30 + 90 * k
        elif emo == "a":
            R, G, B, A = 255, 42, 42, (130 if estrong else 80)
        if A > 0:
            self.tr += (R - self.tr) * 0.3; self.tg += (G - self.tg) * 0.3; self.tb += (B - self.tb) * 0.3
        self.ta += (A - self.ta) * 0.15
        qa = int(self.ta / 12) * 12
        sig = qa + (int(self.tr) >> 5) * 1000 + (int(self.tg) >> 5) * 10000 + (int(self.tb) >> 5) * 100000
        if sig != self.sent_sig:
            self.sent_sig = sig
            self.rc = (int(self.tr), int(self.tg), int(self.tb), qa)
        # blink and ease
        if now > self.blink_at:
            self.blink_at = now + 2500 + u32() % 3500
            self.bl = 0
        blinking = 0.1 if (not dead and now + 150 > self.blink_at and now < self.blink_at) else 1.0
        e = 0.35
        self.eo += (t_eo * 0.8 - self.eo) * 0.3; self.bh += (t_bh - self.bh) * e; self.ba += (t_ba - self.ba) * e
        self.bl += (t_bl - self.bl) * e; self.br += (t_br - self.br) * e
        self.lx += (t_lx - self.lx) * 0.25; self.ly += (t_ly - self.ly) * 0.25; self.sm += (t_sm - self.sm) * 0.3
        self.mw += (tw - self.mw) * 0.55; self.mu += (tu - self.mu) * 0.55; self.ml += (tl - self.ml) * 0.55; self.teethv += (tt - self.teethv) * 0.5
        self.ph += 0.13 if talk else 0.06
        # the can's rocking
        beat = False
        wob = 0.4
        if talk:
            c0 = "n"
            if len(em) >= 3 and em[1] == "|":
                tl0 = len(em) - 2
                i0 = (now - self.talk_t0) // 100
                if i0 >= tl0:
                    i0 = tl0 - 1
                c0 = em[2 + i0]
            st0 = "A" <= c0 <= "Z"
            lc = c0.lower() if st0 else c0
            wob = {"e": 1.0, "l": 1.0, "a": 0.9, "s": 0.8, "w": 0.7, "h": 0.6, "r": 0.6, "v": 0.6, "q": 0.5, "m": 0.5, "d": 0.25, "b": 0.2}.get(lc, 0.35)
            wob *= 1.0 if st0 else 0.7
        self.wobs += (wob - self.wobs) * 0.2
        self.ramp += ((0.0 if dead else (4.0 + 19.0 * self.wobs if talk else 3.0)) - self.ramp) * 0.15
        if talk:
            if now > self.next_beat:
                self.next_beat = now + int(270 - 120 * self.wobs) + u32() % 70
                self.rk += 1.7 + (u32() % 100) / 60.0
                self.bx_ = math.sin(self.rk) * self.ramp
                self.by_ = -abs(math.sin(self.rk * 0.7)) * self.ramp * 0.55
                beat = True
            ox, oy = self.bx_, self.by_
        else:
            if dead:
                self.bx_ = self.by_ = 0.0
            else:
                self.rk += 0.05
            if not dead and now > self.next_idle:
                self.next_idle = now + 450
                self.bx_ = roundf(math.sin(self.rk * 9) * self.ramp)
                self.by_ = roundf(-abs(math.sin(self.rk * 9)) * self.ramp * 0.35)
            ox, oy = self.bx_, self.by_
        rkv = ox - self.prev_bx
        self.prev_bx = ox
        k = 2 + int(roundf(self.bx_ / max(self.ramp, 1.0) * 2.0))
        self.lean_k = min(4, max(0, k))
        theta = ANGS[self.lean_k]
        lt = math.tan(theta)
        cx, cy = 240 + ox, 245 + oy
        self.ball_pos = (90 + int(ox), 80 + int(oy))
        pivotY = cy + 140
        self.convo_y += (self.convo_target - self.convo_y) * 0.18
        # eyes
        eo = self.eo

        def eye(ex, ey):
            w, h = 25.0, 22.0 * eo * blinking
            h = max(h, 2.0)
            return [(clampi(ex + w * math.cos(a)), clampi(ey + h * math.sin(a))) for a in (i * 6.2832 / 17 for i in range(18))]
        ey = cy - 36
        cxe = cx + (pivotY - ey) * lt
        exl, exr = cxe - 52, cxe + 52
        self.eye_l.put(eye(exl, ey)); self.eye_r.put(eye(exr, ey))
        opn = eo * blinking > 0.3
        ew, eh = 25.0, 22.0 * eo * blinking
        for s2 in range(2):
            wh, ir, exx = self.white[s2], self.iris[s2], (exr if s2 else exl)
            if eh > 6:
                wh.hidden = False; wh.w, wh.h = int(2 * ew - 4), int(2 * eh - 4); wh.x, wh.y = int(exx - ew + 2), int(ey - eh + 2)
                d = int(32 * min(1.0, eh / (22 * 0.9)))
                ir.hidden = False; ir.w = ir.h = d; ir.x, ir.y = int(exx + self.lx * 8 - d / 2), int(ey + self.ly * 6 - d / 2)
            else:
                wh.hidden = ir.hidden = True
        for s2 in range(2):
            pu, gl = self.pup[s2], self.glint[s2]
            exx = exr if s2 else exl
            if opn:
                pu.hidden = gl.hidden = False
                pu.x, pu.y = int(exx + self.lx * 8 - 7), int(ey + self.ly * 6 - 7)
                gl.x, gl.y = int(exx + self.lx * 8 - 1), int(ey + self.ly * 8 - 7)
            else:
                pu.hidden = gl.hidden = True
        # brows
        def brow(bx, side, extra):
            y0 = cy - (36 + 36 * eo * 0.45 + 14 + self.bh + extra)
            sl = self.ba * 18 * side
            return [(clampi(bx - 26), clampi(y0 + (sl if side < 0 else -sl) * -1)),
                    (clampi(bx - 9), clampi(y0 + (-sl if side < 0 else sl) * 0.3 - 3)),
                    (clampi(bx + 9), clampi(y0 + (sl if side < 0 else -sl) * 0.3 - 3)),
                    (clampi(bx + 26), clampi(y0 + (sl if side < 0 else -sl) * -1 * -1))]
        self.brow_l.put(brow(exl, -1, self.bl)); self.brow_r.put(brow(exr, 1, self.br))
        # mouth
        self.mg += ((3.1 if talk else 2.1) - self.mg) * 0.3
        my = cy + 52
        MW, MU, ML = self.mw * self.mg, self.mu * self.mg, self.ml * self.mg
        cxm = cx + (pivotY - my) * lt
        sm = self.sm
        mo = []
        for i in range(12):
            u = -1 + i * 2.0 / 11
            edge = 1 - u * u
            mo.append((clampi(cxm + MW * u), clampi(my - MU * edge - sm * (1 - edge) * 0.5 - self.as_ * u)))
        for i in range(12):
            u = 1 - i * 2.0 / 11
            edge = 1 - u * u
            mo.append((clampi(cxm + MW * u), clampi(my + ML * edge - sm * (1 - edge) * 0.5 - self.as_ * u)))
        mo.append(mo[0])
        self.mouth_ln.put(mo)
        mf = []
        if MU + ML > 3:
            top, bot, ltr = my - MU, my + ML, True
            yy = top + 1.5
            while yy < bot - 1 and len(mf) < 62:
                v = (my - yy) / MU if (yy - my) < 0 else (yy - my) / ML
                v = min(v, 1.0)
                half = MW * math.sqrt(max(1 - v * v, 0)) * 0.97
                mf.append((clampi(cxm + (-half if ltr else half)), clampi(yy)))
                mf.append((clampi(cxm + (half if ltr else -half)), clampi(yy)))
                ltr = not ltr
                yy += 5.5
        if len(mf) >= 2:
            self.mfill.put(mf); self.mfill.opa = 255
        else:
            self.mfill.opa = 0
        ty = clampi(my - MU * 0.35)
        self.teeth.put([(clampi(cxm - MW * 0.7), ty), (clampi(cxm + MW * 0.7), ty)])
        self.teeth.opa = 150 if (self.teethv > 0.5 and self.mu + self.ml > 4) else 0
        # extra pen strokes
        sc = min((sm - 3) / 8.0, 1.0) if sm > 3 else 0
        fc2 = min((-sm - 2) / 6.0, 1.0) if sm < -2 else 0
        cw, cyy = 14 + MW * 0.18, my + ML + 13
        self.chin.put([(clampi(cxm + cw * (-1 + i / 3.0)), clampi(cyy + 5 * (1 - (-1 + i / 3.0) ** 2))) for i in range(7)])
        self.chin.opa = 0 if st == "muted" else 230
        for sd in range(2):
            side = 1.0 if sd else -1.0
            kx, ky = cxm + side * MW, my - sm * 0.5
            if sc > 0.05:
                half = (MU + ML) * 0.5 + 10 + 12 * sc
                mid = my + (ML - MU) * 0.5 - sm * 0.2
                pts = []
                for i in range(7):
                    t = -1 + i / 3.0
                    pts.append((clampi(kx + side * (7 + 11 * (1 - t * t) * (0.5 + 0.5 * sc))), clampi(mid + t * half)))
                self.smile[sd].put(pts); self.smile[sd].opa = int(255 * sc)
            else:
                self.smile[sd].opa = 0
            if fc2 > 0.05:
                pts = []
                for i in range(7):
                    t = i / 6.0
                    pts.append((clampi(kx + side * (6 + 9 * math.sin(3.14159 * t * 0.8))), clampi(ky + 6 + 40 * fc2 * t)))
                self.frown[sd].put(pts); self.frown[sd].opa = int(255 * fc2)
            else:
                self.frown[sd].opa = 0
        fu_a = min(self.ba / 0.4, 1.0) if self.ba > 0.12 else 0
        for sd in range(2):
            if fu_a > 0.05:
                fx = cxe + (8 if sd else -8)
                fy = cy - (36 + 36 * eo * 0.45 + 14 + self.bh) - 4
                self.furrow[sd].put([(clampi(fx), clampi(fy)), (clampi(fx + (2 if sd else -2)), clampi(fy + 24 * fu_a))])
                self.furrow[sd].opa = int(255 * fu_a)
            else:
                self.furrow[sd].opa = 0
        self._hat(now, talk, beat, rkv, theta, lt, cx, cy, pivotY, dead)
        self._effects(now, cx, cy)
        self._thinking(now, st)
        self._ears(st, talk, cx, cy, pivotY, lt)

    def _hat(self, now, talk, beat, rkv, theta, lt, cx, cy, pivotY, dead) -> None:
        self.hc += 1
        if dead or not (self.hc % 4 == 0 or beat):
            return
        ax = rkv - self.pv
        self.pv = rkv
        for sub in range(4):
            lean = theta
            tq = 0.032 * math.sin(self.ha + lean) - (ax if sub == 0 else 0) * 0.0035 * math.cos(self.ha)
            if beat and sub == 0:
                tq += (int(u32() % 200) - 100) / 100.0 * 0.012 * (0.4 + 1.2 * self.wobs)
            self.hv += tq - self.hv * 0.02
            self.ha += self.hv
            if not talk and now > self.nudge_at:
                self.nudge_at = now + 5000 + u32() % 6000
                self.hv += (-0.1 if self.ha > 0 else 0.1)
            if self.ha > 0.45:
                self.ha = 0.45
                if self.hv > 0.04:
                    self.hyv += 3
                self.hv = -self.hv * 0.3
            if self.ha < -0.45:
                self.ha = -0.45
                if self.hv < -0.04:
                    self.hyv += 3
                self.hv = -self.hv * 0.3
            self.hxv += (self.ha * 60 - self.hx) * 0.08 - self.hxv * 0.2 - (ax if sub == 0 else 0) * 0.35
            self.hx += self.hxv
            self.hyv += (-self.hy) * 0.14 - self.hyv * 0.22
            self.hy += self.hyv
            if self.hx > 40:
                self.hx = 40; self.hxv = -self.hxv * 0.5
            if self.hx < -40:
                self.hx = -40; self.hxv = -self.hxv * 0.5
            if self.hy > 6:
                self.hy = 6
            if self.hy < -10:
                self.hy = -10
        hw = 285.0
        py = cy - 62 + self.hy
        px = cx + (pivotY - (cy - 62)) * lt + self.hx
        ca, sa = math.cos(self.ha + theta), math.sin(self.ha + theta)

        def rot(lx, ly):
            return (int(px + lx * ca - ly * sa), int(py + lx * sa + ly * ca))
        ht, hb = [], []
        for i in range(41):
            u = -1 + i * 2.0 / 40
            au, tip = abs(u), 44 * u * u
            top = tip - 152 * math.pow(1 - math.pow(au, 2.3), 1.25) - 42 * math.exp(-(u * u) / 0.11)
            bot = tip - 24 * (1 - u * u)
            ht.append(rot(u * hw, top)); hb.append(rot(u * hw, bot))
        self.hat_cast.put([(p[0], p[1] + 13) for p in hb])
        cr = []
        for sd in range(2):
            uu = 0.30 if sd else -0.30
            au2, tip2 = abs(uu), 44 * uu * uu
            tp = tip2 - 152 * math.pow(1 - math.pow(au2, 2.3), 1.25) - 42 * math.exp(-(uu * uu) / 0.11)
            bt2 = tip2 - 24 * (1 - uu * uu)
            cr.append([rot(uu * hw * (1 + 0.10 * (i / 9.0)), bt2 + (tp - bt2) * (0.92 * (i / 9.0))) for i in range(10)])

        def yat(p, x):
            for i in range(len(p) - 1):
                x0, x1 = p[i][0], p[i + 1][0]
                if (x0 <= x < x1) or (x1 <= x < x0):
                    return p[i][1] + (x - x0) / (x1 - x0) * (p[i + 1][1] - p[i][1])
            return math.nan
        for c in range(40):
            xc = c * 12 + 6
            yt, yb = yat(ht, xc), yat(hb, xc)
            b = self.cloth[c]
            if math.isnan(yt) or math.isnan(yb):
                b.hidden = True
                continue
            if yt > yb:
                yt, yb = yb, yt
            b.hidden = False; b.x, b.y, b.w, b.h = c * 12, int(yt) - 1, 13, int(yb - yt) + 2
        self.hat_crease_l.put(cr[0]); self.hat_crease_r.put(cr[1])
        self.hat_top.put(ht); self.hat_bot.put(hb)
        c0 = rot(hw * 0.34, -52)
        for i, rs in enumerate((52, 42, 30, 18)):
            self.rose[i].x, self.rose[i].y = c0[0] - rs // 2, c0[1] - rs // 2
        p0, p1 = rot(hw * 0.34 - 30, -52 + 36), rot(hw * 0.34 + 36, -52 - 44)
        self.lace_edge.put([p0, p1]); self.lace.put([p0, p1])
        self.rose_btn.x, self.rose_btn.y = p0[0] - 8, p0[1] - 8

    def _effects(self, now, cx, cy) -> None:
        for sd in range(2):
            b = self.blush[sd]
            b.x, b.y, b.opa = int(cx + (62 if sd else -112)), int(cy + 6), int(170 * self.fb_)
        self.sweat.x, self.sweat.y, self.sweat.opa = int(cx + 96), int(cy - 64 + (now // 120) % 8), int(235 * self.fs_)
        vs, vx, vy = 1 + 0.25 * math.sin(now / 90.0), cx - 104, cy - 92
        self.vein.put([(clampi(vx), clampi(vy)), (clampi(vx + 18 * vs), clampi(vy + 18 * vs)), (clampi(vx + 9 * vs), clampi(vy + 9 * vs)),
                       (clampi(vx + 18 * vs), clampi(vy)), (clampi(vx), clampi(vy + 18 * vs))])
        self.vein.opa = int(255 * self.fv_)
        if self.fh_ > 0.05:
            hr = (now % 1800) / 1800.0
            hx0, hy0 = cx + 100 + 8 * math.sin(hr * 9), cy - 70 - 90 * hr
            pts = []
            for i in range(26):
                t = i * 6.2832 / 25
                pts.append((int(hx0 + 0.9 * 16 * math.pow(math.sin(t), 3)),
                            int(hy0 - 0.9 * (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)))))
            self.heart.put(pts); self.heart.opa = int(255 * self.fh_ * (1 - hr * 0.8))
        else:
            self.heart.opa = 0

    def _thinking(self, now, st) -> None:
        thinking = st == "thinking"
        if self.prev_state_think == "thinking" and not thinking:
            self.bulb_t0 = now
        self.prev_state_think = st
        if thinking and now > self.next_spawn:
            for k in range(6):
                s = self.sl[k]
                if not s["on"]:
                    s["on"], s["t0"] = True, now
                    if k < 4:
                        t = self.EQS[u32() % 12]
                        self.eq[k].text = t
                        w = min(len(t) * 11, 440)
                        s["x"] = 10 + int(u32() % (460 - w))
                        s["y"] = 404 + int(u32() % 36)
                    else:
                        s["x"] = 14 if k == 4 else 402
                        s["y"] = 90 + int(u32() % 190)
                    self.next_spawn = now + 300 + u32() % 300
                    break
        for k in range(6):
            s = self.sl[k]
            if not s["on"]:
                continue
            age, life = now - s["t0"], 1700
            if age > life:
                s["on"] = False
                if k < 4:
                    self.eq[k].opa = 0
                else:
                    self.gr[k - 4].opa = 0
                continue
            a = age / 250.0 if age < 250 else ((life - age) / 800.0 if age > life - 800 else 1.0)
            rise = int(age * 0.012)
            if k < 4:
                self.eq[k].x, self.eq[k].y = s["x"], s["y"] + (-rise if s["y"] < 200 else rise)
                self.eq[k].opa = int(255 * a)
            else:
                g, typ, w, h = k - 4, int((s["t0"] // 7) % 4), 64, 44
                draw = min(age / 900.0, 1.0)
                ox, oy = s["x"], s["y"] - rise
                pts = [(0, 0), (0, h), (w, h), (0, h)]
                m = int(22 * draw)
                for i in range(m):
                    if len(pts) >= 28:
                        break
                    u = i / 21.0
                    v = (0.5 + 0.4 * math.sin(u * 9.0) if typ == 0 else 1 - 4 * (u - 0.5) ** 2 if typ == 1 else u * u if typ == 2
                         else (0.3 + 0.1 * (i % 6) if i % 6 < 3 else 0.9 - 0.15 * (i % 6)))
                    pts.append((int(u * w), int(h - v * (h - 4))))
                self.gr[g].put([(clampi(ox + x), clampi(oy + y)) for x, y in pts])
                self.gr[g].opa = int(230 * a)
        bage = now - self.bulb_t0
        if self.bulb_t0 != 0 and bage < 2000:
            k = (bage / 280.0) * 1.25 if bage < 280 else (1.25 - (bage - 280) / 120.0 * 0.25 if bage < 400 else 1.0)
            fade = (2000 - bage) / 500.0 if bage > 1500 else 1.0
            bx, by, r = 372, 96, 22 * k
            bo = [(clampi(bx + r * math.cos(i * 6.2832 / 18)), clampi(by + r * math.sin(i * 6.2832 / 18))) for i in range(19)]
            bo += [(clampi(bx - r * 0.38), clampi(by + r * 1.4)), (clampi(bx + r * 0.38), clampi(by + r * 1.4)), (clampi(bx + r * 0.38), clampi(by + r * 0.85))]
            bf, ltr, yy = [], True, by - r + 3
            while yy < by + r - 2 and len(bf) < 22:
                half = math.sqrt(max(r * r - (yy - by) ** 2, 0)) * 0.9
                bf.append((clampi(bx + (-half if ltr else half)), clampi(yy)))
                bf.append((clampi(bx + (half if ltr else -half)), clampi(yy)))
                ltr = not ltr
                yy += 5
            self.bulb_out.put(bo); self.bulb_fill.put(bf)
            self.bulb_out.opa, self.bulb_fill.opa = int(255 * fade), int(220 * fade)
            for i, ang in enumerate((-2.9, -2.3, -1.5708, -0.85, -0.25)):
                c2, s3 = math.cos(ang), math.sin(ang)
                self.rays[i].put([(clampi(bx + c2 * (r + 8)), clampi(by + s3 * (r + 8))), (clampi(bx + c2 * (r + 8 + 14 * k)), clampi(by + s3 * (r + 8 + 14 * k)))])
                self.rays[i].opa = int(255 * fade * (1 if bage > 150 else 0))
            self.bulb_was = True
        elif self.bulb_was:
            self.bulb_was = False
            self.bulb_out.opa = self.bulb_fill.opa = 0
            for ry in self.rays:
                ry.opa = 0

    def _ears(self, st, talk, cx, cy, pivotY, lt) -> None:
        hearing = st in ("listening", "recording") or (st == "idle" and self.in_session and not talk) or (st == "" and self.in_session)
        self.ear += ((1.0 if hearing else 0.0) - self.ear) * 0.3
        fo = (self.ear_fl, self.ear_fr, self.ear_ol, self.ear_or)
        if self.ear < 0.06:
            for ln in fo:
                ln.opa = 0
            return
        for sd in range(2):
            s2 = 1.0 if sd else -1.0
            a = (0.95 if sd else 0.45) + 0.12 * math.sin(self.ph * 2.3 + sd * 2.0) + 0.2 * (st == "recording")
            rot = s2 * a
            by = cy - 4
            bx = cx + (pivotY - by) * lt + s2 * 104
            ew, eh = 21 * self.ear, 44 * self.ear
            ux, uy, rx, ry = math.sin(rot), -math.cos(rot), math.cos(rot), math.sin(rot)
            c0x, c0y = bx + ux * eh * 0.7, by + uy * eh * 0.7
            outl = []
            for i in range(26):
                t = i * 6.2832 / 25
                lx, lu = ew * math.cos(t), eh * math.sin(t)
                outl.append((int(c0x + rx * lx + ux * lu), int(c0y + ry * lx + uy * lu)))
            fill, lr, u = [], True, -(eh - 8)
            while u <= eh - 8 and len(fill) < 24:
                half = max((ew - 7) * math.sqrt(max(1 - (u / (eh - 5)) ** 2, 0.0)), 1.0)
                l0, l1 = (-half, half) if lr else (half, -half)
                lr = not lr
                fill.append((int(c0x + rx * l0 + ux * u), int(c0y + ry * l0 + uy * u)))
                fill.append((int(c0x + rx * l1 + ux * u), int(c0y + ry * l1 + uy * u)))
                u += 7
            fo[sd].put(fill); fo[2 + sd].put(outl)
            fo[sd].opa = fo[2 + sd].opa = 255

    # ── drawing, in the yaml's order ──
    def _ball(self) -> np.ndarray:
        r, g, b, qa = self.rc
        key = (self.lean_k, r, g, b, qa)
        img = self._ball_cache.get(key)
        if img is None:
            if len(self._ball_cache) > 40:
                self._ball_cache.clear()
            img = self._balls[self.lean_k].copy()
            if qa > 0:
                rgbp = img[..., :3].astype(np.uint16)
                img[..., :3] = ((rgbp * (255 - qa) + np.array((r, g, b), np.uint16) * qa) // 255).astype(np.uint8)
            self._ball_cache[key] = img
        return img

    def render(self) -> np.ndarray:
        c = np.zeros((H, W, 3), np.uint8)
        col = rgb(self.face_rgb)
        for i in range(16):                                          # the nimbus: four nested frames of four bars
            lv, kind = i // 4, i % 4
            o, n = 10 * lv, 480 - 20 * lv
            if lv == 0:
                rect = ((0, 0, 480, 10), (0, 470, 480, 10), (0, 10, 10, 460), (470, 10, 10, 460))
            else:
                rect = ((o, o, n, 10), (o, 470 - o, n, 10), (o, o + 10, 10, n - 20), (470 - o, o + 10, 10, n - 20))
            x, y, w, h = rect[kind]
            a = self.ring_opa[i]
            roi = c[y:y + h, x:x + w]
            roi[:] = (roi.astype(np.uint16) * (255 - a) // 255 + np.array(col, np.uint16) * a // 255).astype(np.uint8)
        if self._convo_layer is not None and self.convo:                # the conversation flows behind him
            y = int(self.convo_y)
            L = self._convo_layer
            top = max(0, -y)
            if y < H and y + L.shape[0] > 0:
                blit_rgba(c, L[top:min(L.shape[0], H - y)], 0, y + top, 204)
        for ln in (self.ear_fl, self.ear_fr, self.ear_ol, self.ear_or):
            draw_line(c, ln)
        blit_rgba(c, self._ball(), self.ball_pos[0], self.ball_pos[1], 255)
        draw_line(c, self.hat_cast)
        for b in self.cloth:
            draw_box(c, b)
        for ln in (self.hat_crease_l, self.hat_crease_r, self.hat_top, self.hat_bot):
            draw_line(c, ln)
        for b in self.rose:
            draw_box(c, b)
        draw_line(c, self.lace_edge); draw_line(c, self.lace); draw_box(c, self.rose_btn)
        for b in self.white:
            draw_box(c, b)
        for b in self.blush:
            draw_box(c, b)
        for ln in (self.mfill, self.eye_l, self.eye_r, self.brow_l, self.brow_r, self.mouth_ln, self.teeth, self.chin, *self.smile, *self.frown, *self.furrow):
            draw_line(c, ln)
        for b in self.iris:
            draw_box(c, b)
        draw_box(c, self.sweat); draw_line(c, self.vein); draw_line(c, self.heart)
        for b in self.pup:
            draw_box(c, b)
        for b in self.glint:
            draw_box(c, b)
        for lb in self.eq:
            if lb.opa > 0 and lb.text:
                blit_rgba(c, self.text.one(lb.text, lb.color), lb.x, lb.y, lb.opa)
        for ln in (*self.gr, self.bulb_fill, self.bulb_out, *self.rays):
            draw_line(c, ln)
        return c


def to565(img: np.ndarray) -> np.ndarray:
    """(H, W, 3) uint8 -> (H, W) uint16 RGB565."""
    r, g, b = img[..., 0].astype(np.uint16), img[..., 1].astype(np.uint16), img[..., 2].astype(np.uint16)
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
