"""Copy the Captain's recorded clips into a training folder 20 times, each at a random gain (x2 .. x5, clipped).

The hub applies SATELLITE_MIC_GAIN (3.0) to the pod's quiet microphone before the model hears it, so the model must learn from that loudness, not
from the raw clips. usage: mix_real.py SRC_DIR DST_DIR   (16 kHz mono wav in, same out; names real_<i>_<name>)"""
import random
import sys
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wf

COPIES, GAINS = 20, (2.0, 5.0)


def main(src: str, dst: str) -> int:
    rng = random.Random(7)
    Path(dst).mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(Path(src).glob("*.wav")):
        rate, x = wf.read(f)
        x = x.astype(np.float32)
        for i in range(1, COPIES + 1):
            y = np.clip(x * rng.uniform(*GAINS), -32768, 32767).astype(np.int16)
            wf.write(Path(dst) / f"real_{i}_{f.name}", rate, y)
        n += 1
    print(f"mixed in {n} of his clips x{COPIES} at gains {GAINS}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
