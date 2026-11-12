"""What would the face stream cost with other tile encodings? (offline: the real animation, the real changed tiles, bytes per frame per idea)

    services/satellite/.venv/Scripts/python tools/face_codec_probe.py

For each state: 170 frames of `Face`, the 16 px tiles that changed since the frame before (as `face_stream.Encoder` finds them, without the rolling
refresh), and the mean bytes per frame under:
  now      what the stream sends today: solid / runs / raw per tile, 8 bytes of header each
  pal      the smallest of solid, runs and an indexed tile (2 colours: 1 bit a pixel, 4: 2 bits, 16: 4 bits; its palette sent with it), else raw
  move     as pal, but a tile that is the previous frame's pixels shifted by up to +-MOVE px is sent as that shift (3 bytes)
  t8       as pal on 8 px tiles (a finer idea of "what changed"), 4 bytes of header each
  delta    runs over (this XOR previous) per tile, when smaller
  z1 / z6  the whole frame's `pal` bytes deflated at level 1 / 6 (the box would inflate one stream per frame)
"""
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "satellite"))

import numpy as np  # noqa: E402

import face_render as fr  # noqa: E402
from face_stream import rle  # noqa: E402

T = 16
MOVE = 3


def enc_now(tile):
    if tile.min() == tile.max():
        return 2
    r = len(rle(tile))
    return r if r * 2 < tile.size * 2 else tile.size * 2


def enc_pal(tile):
    best = enc_now(tile)
    n = len(np.unique(tile))
    px = tile.size
    for colours, bits in ((2, 1), (4, 2), (16, 4)):
        if n <= colours:
            best = min(best, n * 2 + 1 + (px * bits + 7) // 8)
            break
    return best


def frames(state, talking, n=200):
    face = fr.Face()
    face.set_state(state)
    face.talking = talking
    out = []
    for i in range(n):
        face.tick(10_000 + i * 50)
        out.append(fr.to565(face.render()))
    return out[30:]


def study(state, talking=False):
    fs = frames(state, talking)
    tot = {k: 0 for k in ("tiles", "now", "pal", "move", "moved", "t8", "delta", "z1", "z6", "kinds")}
    kinds = {"solid": 0, "runs": 0, "raw": 0}
    ncol = []
    for prev, cur in zip(fs, fs[1:]):
        H, W = cur.shape
        mask = (cur != prev).reshape(H // T, T, W // T, T).any(axis=(1, 3))
        blob = bytearray()
        for ty, tx in zip(*np.nonzero(mask)):
            y, x = ty * T, tx * T
            tile = cur[y:y + T, x:x + T]
            tot["tiles"] += 1
            now = enc_now(tile)
            kinds["solid" if now == 2 else ("raw" if now == 512 else "runs")] += 1
            ncol.append(len(np.unique(tile)))
            tot["now"] += 8 + now
            pal = enc_pal(tile)
            tot["pal"] += 8 + pal
            blob += tile.astype(">u2").tobytes() if pal >= 512 else bytes(pal)        # (size only matters for the deflate estimate below: use the real pixels)
            moved = False
            for dy in range(-MOVE, MOVE + 1):
                for dx in range(-MOVE, MOVE + 1):
                    if (dx or dy) and 0 <= y + dy and y + dy + T <= H and 0 <= x + dx and x + dx + T <= W and np.array_equal(prev[y + dy:y + dy + T, x + dx:x + dx + T], tile):
                        moved = True
                        break
                if moved:
                    break
            tot["moved"] += moved
            tot["move"] += 8 + (3 if moved else pal)
            d = tile ^ prev[y:y + T, x:x + T]
            tot["delta"] += 8 + min(pal, len(rle(d)))
        m8 = (cur != prev).reshape(H // 8, 8, W // 8, 8).any(axis=(1, 3))
        for ty, tx in zip(*np.nonzero(m8)):
            tot["t8"] += 4 + enc_pal(cur[ty * 8:ty * 8 + 8, tx * 8:tx * 8 + 8])
        raw_frame = b"".join(cur[ty * T:ty * T + T, tx * T:tx * T + T].astype(">u2").tobytes() for ty, tx in zip(*np.nonzero(mask)))
        tot["z1"] += len(zlib.compress(raw_frame, 1)) + 2 * int(mask.sum())
        tot["z6"] += len(zlib.compress(raw_frame, 6)) + 2 * int(mask.sum())
    n = len(fs) - 1
    c = np.array(ncol)
    print(f"{state:10s} tiles/frame {tot['tiles'] / n:5.0f} (solid {kinds['solid'] / n:4.0f}, runs {kinds['runs'] / n:4.0f}, raw {kinds['raw'] / n:4.0f}; colours/tile median {np.median(c):.0f}, <=4: {(c <= 4).mean():.0%}, <=16: {(c <= 16).mean():.0%})")
    print(f"{'':10s} KB/frame: now {tot['now'] / n / 1024:5.1f}  pal {tot['pal'] / n / 1024:5.1f}  move {tot['move'] / n / 1024:5.1f} ({tot['moved'] / max(1, tot['tiles']):.0%} moved)  "
          f"t8 {tot['t8'] / n / 1024:5.1f}  delta {tot['delta'] / n / 1024:5.1f}  z1 {tot['z1'] / n / 1024:5.1f}  z6 {tot['z6'] / n / 1024:5.1f}")


if __name__ == "__main__":
    for st in ("idle", "listening", "recording", "thinking", "speaking"):
        study(st, talking=(st == "speaking"))
