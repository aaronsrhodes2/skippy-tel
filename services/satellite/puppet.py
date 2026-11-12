"""Puppet mode (docs/puppet-mode.md): Skippy's face as a draw list that the box rasterizes, instead of pixel tiles.

`record(face)` runs `Face.render()` once with its three drawing calls recorded, and returns the image it drew plus the ops in painter's order;
`encode_scene(ops, sprites)` packs them in the wire format the box's `skippy_puppet` component reads (little-endian):

    'P', 3, frame u16, nops u16, then ops:
      1 polyline : 1, npts u8, width u8, rounded u8, r g b, opa, then npts x (x i16, y i16)
      2 box      : 2, x i16, y i16, w u16, h u16, radius u16, border u8, r g b, opa, br bg bb   (radius >= 100: an ellipse; border > 0: a ring of `border` px in br bg bb)
      3 sprite   : 3, id u8, x i16, y i16, opa u8                                              (ids from `sprites`: the images the box holds)
"""
from __future__ import annotations

import struct

import numpy as np

import face_render as fr

MAX_POINTS = 64


class Recorder:
    """Patches face_render's draw_line / draw_box / blit_rgba to note what they draw (and still draw it), for the duration of a `with`."""

    def __init__(self):
        self.ops: list = []
        self._orig = None

    def __enter__(self):
        self._orig = (fr.draw_line, fr.draw_box, fr.blit_rgba)
        line, box, blit = self._orig

        def rec_line(canvas, ln):
            if ln.opa > 0 and len(ln.pts) >= 2:
                self.ops.append(("L", tuple(ln.pts), ln.width, ln.color, ln.opa, ln.rounded))
            return line(canvas, ln)

        def rec_box(canvas, b):
            if not (b.hidden or b.opa <= 0 or b.w <= 0 or b.h <= 0):
                self.ops.append(("B", b.x, b.y, b.w, b.h, b.color, b.opa, b.radius, b.border, b.border_color))
            return box(canvas, b)

        def rec_blit(canvas, tile, x, y, opa):
            if opa > 0:
                self.ops.append(("I", tile, x, y, opa))
            return blit(canvas, tile, x, y, opa)

        fr.draw_line, fr.draw_box, fr.blit_rgba = rec_line, rec_box, rec_blit
        return self

    def __exit__(self, *exc):
        fr.draw_line, fr.draw_box, fr.blit_rgba = self._orig
        return False


def record(face: "fr.Face") -> tuple[np.ndarray, list]:
    with Recorder() as rec:
        img = face.render()
    return img, rec.ops


def encode_scene(ops: list, sprites: dict, frame: int = 0) -> bytes:
    """`sprites` maps id(tile array) -> sprite id (what the box holds); an image op whose tile is not in it (text, so far) is left out."""
    out = bytearray()
    n = 0
    for op in ops:
        if op[0] == "L":
            _, pts, width, color, opa, rounded = op
            pts = pts[:MAX_POINTS]
            out += struct.pack("<BBBBBBBB", 1, len(pts), width, 1 if rounded else 0, (color >> 16) & 255, (color >> 8) & 255, color & 255, opa)
            for x, y in pts:
                out += struct.pack("<hh", x, y)
        elif op[0] == "B":
            _, x, y, w, h, color, opa, radius, border, bcol = op
            out += struct.pack("<BhhHHHBBBBBBBB", 2, x, y, w, h, radius, border, (color >> 16) & 255, (color >> 8) & 255, color & 255, opa,
                               (bcol >> 16) & 255, (bcol >> 8) & 255, bcol & 255)
        else:
            _, tile, x, y, opa = op
            sid = sprites.get(id(tile))
            if sid is None:
                continue
            out += struct.pack("<BBhhB", 3, sid, x, y, opa)
        n += 1
    return b"P\x03" + struct.pack("<HH", frame & 0xFFFF, n) + bytes(out)
