"""Copy the first part of openWakeWord's 17 GB pre-computed negative features onto the fast volume, once.

The trainer reads that file at random through numpy's mmap. From the bind-mounted D: the pages pile up in the Docker VM's cache (16 GB, shared with the
house's other containers) until Docker Desktop dies (twice, at 75-80% of the training). A 5 GB slice (~25% of the 2,000 hours) from the volume is enough
hours of everyday speech and music to learn "not Skippy" from, and cannot take the VM down. usage: slice_features.py SRC.npy DST.npy [FRACTION]"""
import sys
from pathlib import Path

import numpy as np


def main(src: str, dst: str, fraction: float = 0.25) -> int:
    if Path(dst).is_file():
        print(f"{dst}: already there")
        return 0
    a = np.load(src, mmap_mode="r")
    n = int(len(a) * fraction)
    out = np.lib.format.open_memmap(dst + ".part", mode="w+", dtype=a.dtype, shape=(n,) + a.shape[1:])
    for i in range(0, n, 20000):
        out[i:i + 20000] = a[i:min(n, i + 20000)]
    out.flush()
    del out
    Path(dst + ".part").rename(dst)
    print(f"{dst}: {n} of {len(a)} rows {a.shape[1:]} {a.dtype}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], *([float(sys.argv[3])] if len(sys.argv) > 3 else [])))
