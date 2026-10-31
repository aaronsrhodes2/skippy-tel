"""Streams Skippy's face to a room box by hand, without the hub (a diagnostic, run by hand; the hub does the same itself in esphome_link.py).

    services/satellite/.venv/Scripts/python tools/face_stream_demo.py [host] pattern            a known-good test picture (colour bars), one keyframe, held 6 s
    services/satellite/.venv/Scripts/python tools/face_stream_demo.py [host] run [seconds]      the face through idle, speaking, thinking, listening, idle
    services/satellite/.venv/Scripts/python tools/face_stream_demo.py [host] rate [KB/s ...]    raw throughput: whole-screen noise frames at the given rates, to see what the box takes

While it runs, `services/satellite/rooms/shot.py` takes the picture from the panel itself and tools/box_fps.py's log reader shows the box's own
"skippy_stream: .. frames/s, .. tiles/s, .. bad, .. lost frames" line. The box falls back to its own drawing 1.2 s after the last datagram.
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "satellite"))
import face_stream as fs  # noqa: E402
from face_render import to565  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1] in ("pattern", "run", "rate") else "192.168.0.84"
MODE = next((a for a in sys.argv[1:] if a in ("pattern", "run", "rate")), "run")
ARGS = [a for a in sys.argv[1:] if a not in (HOST, MODE)]


def pattern() -> np.ndarray:
    img = np.zeros((480, 480, 3), np.uint8)
    for i, c in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255), (128, 128, 128))):
        img[:, i * 60:(i + 1) * 60] = c
    img[400:480, :, :] = np.linspace(0, 255, 480, dtype=np.uint8)[None, :, None]       # a grey ramp along the bottom
    img[0:8, :, :] = 255                                                               # a white line at the very top: is the frame the right way up and unclipped?
    return img


def main() -> None:
    sent: list[int] = []
    s = fs.FaceStreamer(HOST, name="demo", log=print)
    if MODE == "pattern":
        enc = fs.Encoder()
        img = to565(pattern())
        t0 = time.time()
        while time.time() - t0 < 6:
            for d in enc.encode(img):
                s.send(d)
                time.sleep(0.001)
            time.sleep(0.1)
        return
    if MODE == "rate":
        rng = np.random.default_rng(1)
        for kb in [float(a) for a in ARGS] or [200, 400, 600, 800, 1000, 1400]:
            enc = fs.Encoder()
            t0, n, nbytes = time.time(), 0, 0
            while time.time() - t0 < 6:
                enc.keyframe(1)                                                          # noise: every tile raw, 1 KB per 16x16 tile
                f = to565(rng.integers(0, 255, (480, 480, 3), dtype=np.uint8))
                t = time.perf_counter()
                for d in enc.encode(f):
                    s.send(d)
                    nbytes += len(d)
                    t += len(d) / (kb * 1024)
                    while time.perf_counter() < t:
                        pass
                n += 1
            print(f"offered {kb:6.0f} KB/s: {n / 6:.2f} full frames/s ({nbytes / 6 / 1024:.0f} KB/s)")
        return
    seconds = float(ARGS[0]) if ARGS else 30
    f = s.face
    f.set_state("idle")
    script = [(0.0, "idle", False), (0.2, "speaking", True), (0.5, "thinking", False), (0.62, "listening", False), (0.78, "idle", False)]
    s.start()
    t0 = time.time()
    f.add_convo(0, "Skippy, tell me about beer cans")
    mouth, emote = "n" + "BCDBCEAXBDCBCAXDBC" * 12, "n|" + "nnnhhhHHHnnn" * 8
    done = set()
    while time.time() - t0 < seconds:
        frac = (time.time() - t0) / seconds
        for at, state, talking in script:
            if frac >= at and at not in done:
                done.add(at)
                f.mouth, f.emote = (mouth, emote) if talking else ("", "")
                s.set_box_state(state, talking)
                if talking:
                    f.add_convo(1, "Oh, a request. How delightful. I am a magnificent beer can, obviously.")
                print(f"{time.time() - t0:5.1f}s  {state}")
        time.sleep(0.05)
    s.stop()
    s.join(2)


main()
