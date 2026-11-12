"""Skippy's can with flat cartoon shading: python services/satellite/rooms/avatar/flat_can.py

Takes the silhouettes of the five lean images in frames/ball (the brushed-aluminium renders) and writes frames/ball_flat: the same shapes filled in four flat
tones across the can's width (a light band, the body, a shadow band, a purple rim) with a dark outline. A flat area is a solid tile for the streamed face
(no data to send while the can sways: only its edges change), and the same images are the box's own fallback drawing, so both look the same.
"""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
SRC, DST = HERE / "frames" / "ball", HERE / "frames" / "ball_flat"
BASE, LIGHT, SHADOW, RIM, LINE = (150, 160, 172), (214, 221, 229), (88, 96, 112), (138, 92, 230), (22, 24, 34)


def flatten(img: np.ndarray) -> np.ndarray:
    mask = img[..., 3] > 128
    h, w = mask.shape
    out = np.zeros((h, w, 4), np.uint8)
    for y in range(h):
        xs = np.flatnonzero(mask[y])
        if xs.size < 4:
            continue
        u = (xs - xs[0]) / max(xs[-1] - xs[0], 1)
        col = np.tile(np.array(BASE), (xs.size, 1))
        col[(u > 0.16) & (u < 0.30)] = LIGHT
        col[(u > 0.74) & (u < 0.93)] = SHADOW
        col[u >= 0.93] = RIM
        out[y, xs, :3], out[y, xs, 3] = col, 255
    edge = mask & ~cv2.erode(mask.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    out[edge, :3] = LINE
    return out


def main() -> None:
    DST.mkdir(exist_ok=True)
    for i in range(5):
        Image.fromarray(flatten(np.asarray(Image.open(SRC / f"0{i}.png").convert("RGBA")))).save(DST / f"0{i}.png", optimize=True)
        print("wrote", DST / f"0{i}.png")


if __name__ == "__main__":
    main()
