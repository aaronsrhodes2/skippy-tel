"""How much of the screen does a frame of Skippy's face actually change, as a draw list? (the number that decides puppet mode's speed)

For each frame: the ops whose parameters changed since the last frame (or that appeared / vanished), each at its old and new bounding box, grouped into
rectangles that overlap, and the area of those rectangles. Prints mean / 95th percentile / worst per state, and the box's redraw time at the cost measured
on the pod (`tools/puppet_bench.py`: US_PER_PX).

    services/satellite/.venv/Scripts/python tools/face_dirty_probe.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "satellite"))

import numpy as np  # noqa: E402

import face_render as fr  # noqa: E402
import puppet  # noqa: E402

US_PER_PX = 15586 / (128 * 96)               # measured on the living pod, 2026-11-11: a 128x96 patch redrawn in 15.6 ms


def bbox(op):
    if op[0] == "L":
        xs, ys = [p[0] for p in op[1]], [p[1] for p in op[1]]
        pad = op[2] // 2 + 2
        return min(xs) - pad, min(ys) - pad, max(xs) + pad + 1, max(ys) + pad + 1
    if op[0] == "B":
        return op[1] - 1, op[2] - 1, op[1] + op[3] + 1, op[2] + op[4] + 1
    h, w = op[1].shape[:2]
    return op[2], op[3], op[2] + w, op[3] + h


def key(op):
    return (op[0],) + tuple(op[1:]) if op[0] != "I" else ("I", id(op[1]), op[2], op[3], op[4])


def merge(rects):
    rects = [list(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out = []
        for r in rects:
            for o in out:
                if r[0] < o[2] and o[0] < r[2] and r[1] < o[3] and o[1] < r[3]:
                    o[0], o[1], o[2], o[3] = min(o[0], r[0]), min(o[1], r[1]), max(o[2], r[2]), max(o[3], r[3])
                    changed = True
                    break
            else:
                out.append(r)
        rects = out
    return rects


def run(state, frames=200, talking=False):
    face = fr.Face()
    face.set_state(state)
    face.talking = talking
    prev = None
    areas = []
    px_areas = []
    prev_img = None
    for i in range(frames):
        face.tick(10_000 + i * 50)
        img, ops = puppet.record(face)
        img565 = fr.to565(img)
        if i < 30:
            prev, prev_img = ops, img565
            continue
        tiles = (img565 != prev_img).reshape(30, 16, 30, 16).any(axis=(1, 3))          # what really changed, in 16 px tiles
        px_areas.append(int(tiles.sum()) * 256)
        prev_img = img565
        rects = []
        old = {}
        for k, op in enumerate(prev):
            old.setdefault((op[0], k), op)
        for k, op in enumerate(ops):
            o = old.get((op[0], k))
            if o is None or key(o) != key(op):
                rects.append(bbox(op))
                if o is not None:
                    rects.append(bbox(o))
        for (kind, k), o in old.items():
            if k >= len(ops) or ops[k][0] != kind:
                rects.append(bbox(o))
        clipped = [(max(0, a), max(0, b), min(480, c), min(480, d)) for a, b, c, d in rects]
        clipped = [r for r in clipped if r[2] > r[0] and r[3] > r[1]]
        areas.append(sum((r[2] - r[0]) * (r[3] - r[1]) for r in merge(clipped)))
        prev = ops
    a = np.array(areas)
    ms = lambda px: px * US_PER_PX / 1000
    b = np.array(px_areas)
    print(f"{'':10s} changed-pixel tiles: mean {b.mean():6.0f} px ({ms(b.mean()):5.1f} ms)  p95 {np.percentile(b, 95):6.0f} px ({ms(np.percentile(b, 95)):5.1f} ms)")
    print(f"{state:10s} dirty px mean {a.mean():7.0f} p95 {np.percentile(a, 95):7.0f} max {a.max():7.0f}   ->  ms mean {ms(a.mean()):5.1f} p95 {ms(np.percentile(a, 95)):5.1f} max {ms(a.max()):5.1f}")


if __name__ == "__main__":
    for st in ("idle", "listening", "recording", "thinking", "speaking"):
        run(st, talking=(st == "speaking"))
