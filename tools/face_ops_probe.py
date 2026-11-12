"""How many bytes would a frame cost as a draw list (puppet mode) instead of changed pixel tiles?

Replays face_render.Face with its three drawing calls recorded as ops, then sizes each frame as the ops that changed since the last one, using the
wire sizes puppet mode would use (docs/puppet-mode.md), and compares with the pixel-tile encoder (face_stream.Encoder) on the same frames.

    services/satellite/.venv/Scripts/python tools/face_ops_probe.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "satellite"))

import numpy as np  # noqa: E402

import face_render as fr  # noqa: E402
from face_stream import Encoder  # noqa: E402

ops: list = []
_line, _box, _blit = fr.draw_line, fr.draw_box, fr.blit_rgba


def rec_line(canvas, ln):
    if ln.opa > 0 and len(ln.pts) >= 2:
        ops.append(("L", tuple(ln.pts), ln.width, ln.color, ln.opa, ln.rounded))
    return _line(canvas, ln)


def rec_box(canvas, b):
    if not (b.hidden or b.opa <= 0 or b.w <= 0 or b.h <= 0):
        ops.append(("B", b.x, b.y, b.w, b.h, b.color, b.opa, b.radius, b.border, b.border_color))
    return _box(canvas, b)


def rec_blit(canvas, tile, x, y, opa):
    if opa > 0:
        ops.append(("I", id(tile) if tile.size > 4000 else tile.tobytes(), x, y, opa))
    return _blit(canvas, tile, x, y, opa)


fr.draw_line, fr.draw_box, fr.blit_rgba = rec_line, rec_box, rec_blit


def size_of(op) -> int:
    """Bytes on the wire: line = kind, slot, npts, width, color(3), opa, then 2 x i16 per point; box = kind, slot, 4 x i16, color(3), opa, radius, border;
    image = kind, slot, sprite id (2), x, y (4), opa. (Text is a sprite too: the box caches the first send of each.)"""
    if op[0] == "L":
        return 9 + 4 * len(op[1])
    if op[0] == "B":
        return 16
    return 9


def run(state: str, frames: int = 120, talking: bool = False) -> None:
    face = fr.Face()
    face.set_state(state)
    face.talking = talking
    enc = Encoder()
    tile_bytes = op_bytes = full_bytes = 0
    prev: dict = {}
    n = 0
    for i in range(frames):
        t = 10_000 + i * 50
        face.tick(t)
        ops.clear()
        img = face.render()
        if i < 30:
            enc.encode(fr.to565(img))
            continue                                           # settle (the box's first frames are keyframes)
        tile_bytes += sum(len(d) for d in enc.encode(fr.to565(img)))
        cur = {}
        for k, op in enumerate(ops):
            cur[(op[0], k)] = op
        changed = [op for key, op in cur.items() if prev.get(key) != op]
        gone = [key for key in prev if key not in cur]
        op_bytes += sum(size_of(op) for op in changed) + 2 * len(gone) + 7
        full_bytes += sum(size_of(op) for op in ops) + 7
        prev = cur
        n += 1
    print(f"{state:10s} talking={talking!s:5s}  tiles {tile_bytes / n / 1024:6.1f} KB/frame   ops-changed {op_bytes / n:7.0f} B/frame   whole scene {full_bytes / n:7.0f} B   ops/frame {len(ops)}")


if __name__ == "__main__":
    for st in ("idle", "listening", "recording", "thinking", "speaking"):
        try:
            run(st, talking=(st == "speaking"))
        except Exception as e:                                  # noqa: BLE001
            print(st, "->", type(e).__name__, e)
