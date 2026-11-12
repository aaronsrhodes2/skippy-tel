"""Line up several PC outputs in ONE room (the Captain, 2026-11-01: the Enclave on skippy-core's Bluetooth and the Sony on the HDMI, side by side).

sync.py lines each output up with the pod, or, when the PC output is the room's one voice, only learns its latency; it can't tell two PC outputs
apart when both play the same clip at nearly the same moment. So this measures them one at a time, from the same microphone: a chirp on each
output alone, a few rounds, the median time from the router's push to the sound in the room. Each output then waits for the slowest
(`align.wait` in state/audio-sync.json); router.shared() adds that to the one-voice cushion, so they sound together. Run it again whenever a
speaker changes (a new Bluetooth latency, the Enclave moved to another room: then it isn't needed for that pair any more).

    python services/satellite/sync_outputs.py enclave hdmi          # measure, print, save
    python services/satellite/sync_outputs.py enclave hdmi --dry    # measure and print only
    python services/satellite/sync_outputs.py enclave hdmi --ticks 8                    # BY EAR: clicks on all of them at once, as the hub would
    python services/satellite/sync_outputs.py enclave hdmi --ticks 8 --late enclave=20  # the Enclave sounds 20 ms later than measured: saved, then clicks
      (standing between the speakers, the Captain says which one is late; each --late adds to that output's saved latency, the others wait for it)

It opens its own router (the outputs are shared, so the hub keeps running) and its own microphone (SATELLITE_SYNC_MIC). The hub must NOT be
sending to a network output at the same time (snapserver takes one sender): run it before the hub's router opens `enclave`, or briefly
stop the hub. A restart of the hub picks the result up.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import router as rt  # noqa: E402
import speaker_out  # noqa: E402
import sync  # noqa: E402

ROUNDS = 4
CHIRP_S = 0.8
LISTEN_S = 2.5                    # after the chirp: the slowest output's sound must land inside this


def chirp(seconds: float = CHIRP_S, level: float = 0.35):
    """A log sweep 300 Hz -> 4 kHz (the correlation's band), faded in and out: one sharp peak in the room's response, no echoes of pitch."""
    import numpy as np                                            # noqa: PLC0415
    t = np.arange(int(rt.RATE * seconds)) / rt.RATE
    f0, f1 = 300.0, 4000.0
    k = np.log(f1 / f0)
    x = np.sin(2 * np.pi * f0 * seconds / k * (np.exp(t / seconds * k) - 1))
    fade = np.minimum(1.0, np.minimum(t, seconds - t) / 0.02)
    mono = (level * x * fade).astype("float32")
    return np.stack([mono, mono], axis=1)


def vault_env() -> dict:
    env = {}
    for p in (Path("G:/My Drive/vault/.env"), Path(__file__).resolve().parents[3] / ".env"):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"'))
        except OSError:
            continue
    return env


def measure(r: rt.Router, mic: sync.MicRing, kind: str, clip) -> float | None:
    """Seconds from the router's first push to the clip sounding in the room, on `kind` alone; None when the microphone heard no copy."""
    p = r.play(rt.ClipSource(clip), layer="voice", targets={kind})
    end = time.monotonic() + 3.0
    while not p.started and time.monotonic() < end:
        time.sleep(0.01)
    if not p.started or kind not in p.marks:
        print(f"  {kind}: did not start (its output isn't open)")
        return None
    t_push = p.marks[kind][0]
    time.sleep(len(clip) / rt.RATE + LISTEN_S)
    room, t0 = mic.slice(t_push - 0.2, t_push + len(clip) / rt.RATE + LISTEN_S)
    ref = sync.to_fs(clip)
    if room is None or len(room) < len(ref):
        print(f"  {kind}: the microphone did not cover it")
        return None
    found = sync.peaks(sync.correlate(room, ref))
    if not found:
        print(f"  {kind}: not heard")
        return None
    lag, h = found[0]
    got = t0 + lag / sync.FS - t_push
    print(f"  {kind}: {got * 1000:.0f} ms ({h:.0f}x)")
    return got


def click(level: float = 0.6):
    """A short sharp click (5 ms of a decaying 2 kHz burst): two copies a few ms apart are heard as a flam, so the ear can say which leads."""
    import numpy as np                                            # noqa: PLC0415
    n = int(rt.RATE * 0.005)
    t = np.arange(n) / rt.RATE
    mono = (level * np.sin(2 * np.pi * 2000 * t) * np.exp(-t / 0.0015)).astype("float32")
    return np.stack([mono, mono], axis=1)


SCALE = (523.3, 659.3, 784.0, 1046.5, 784.0, 659.3)              # C E G C' G E: each beep a different note, so a late copy is a different pitch


def tone(freq: float, seconds: float = 0.12, level: float = 0.5):
    """A short beep with a sharp start (the ear times the onset) and a quick fade (no click at the end)."""
    import numpy as np                                            # noqa: PLC0415
    t = np.arange(int(rt.RATE * seconds)) / rt.RATE
    env_ = np.minimum(1.0, (seconds - t) / 0.03)
    mono = (level * np.sin(2 * np.pi * freq * t) * env_).astype("float32")
    return np.stack([mono, mono], axis=1)


def by_ear(env: dict, parts: dict, args) -> int:
    """Clicks on all the outputs at once, each with the wait the hub gives it; --late first moves one output's saved latency."""
    data = sync.load()
    align = data.setdefault("align", {})
    lat = align.setdefault("latency", {})
    if args.late:
        kind, ms = args.late.split("=", 1)
        kind = kind.strip()
        if kind not in lat:
            print(f"no saved latency for {kind!r}: measure first (without --ticks)")
            return 2
        lat[kind] = round(float(lat[kind]) + float(ms) / 1000, 4)
        align["by_ear"] = time.strftime("%Y-%m-%d %H:%M:%S")
        sync.STATE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    wait = sync.saved_align(args.outputs)
    cushion = float(env.get("SATELLITE_SOLO_DELAY", str(rt.SOLO_DELAY_S)))
    print("latency " + ", ".join(f"{k} {float(lat.get(k, 0)) * 1000:.0f} ms" for k in args.outputs)
          + " -> waits " + ", ".join(f"{k} {wait.get(k, 0) * 1000:.0f} ms" for k in args.outputs))
    r = rt.Router([(k, parts[k], cushion + wait.get(k, 0.0)) for k in args.outputs], log=lambda m: print(" ", m))
    r.start()
    end = time.monotonic() + 15
    while time.monotonic() < end and not all(r.available(k) for k in args.outputs):
        time.sleep(0.5)
        r._last_scan = -1e9                                       # noqa: SLF001
    closed = [k for k in args.outputs if not r.available(k)]
    if closed:
        print(f"not open: {closed}")
        return 1
    time.sleep(1.0)
    for i in range(args.ticks):
        clip = tone(SCALE[i % len(SCALE)]) if args.tones else click()
        r.play(rt.ClipSource(clip), layer="voice", targets=set(args.outputs))
        time.sleep(args.every)
    time.sleep(1.0)
    r.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outputs", nargs="+", help="router kinds in one room, e.g. enclave hdmi")
    ap.add_argument("--rounds", type=int, default=ROUNDS)
    ap.add_argument("--dry", action="store_true", help="measure only, save nothing")
    ap.add_argument("--ticks", type=int, default=0, help="by ear: this many clicks on all the outputs at once, with the saved waits")
    ap.add_argument("--tones", action="store_true", help="by ear: beeps up and down a scale instead of clicks (walk between the floors)")
    ap.add_argument("--every", type=float, default=1.5, help="by ear: seconds between beeps")
    ap.add_argument("--late", default="", help="by ear: KIND=MS, that output sounds MS later (negative: earlier) than its saved latency")
    args = ap.parse_args()
    env = vault_env()
    parts = speaker_out.output_parts(env)
    unknown = [k for k in args.outputs if k not in parts]
    if unknown:
        print(f"unknown outputs {unknown}: one of {sorted(parts)}")
        return 2
    import types                                                  # noqa: PLC0415
    if args.ticks:
        return by_ear(env, parts, args)
    mic = sync.MicRing(env.get("SATELLITE_SYNC_MIC", "CMTECK"), log=lambda m: print(" ", m))
    r = rt.Router([(k, parts[k], 0.0) for k in args.outputs], log=lambda m: print(" ", m))
    r.calibrator = types.SimpleNamespace(mic=mic)                 # the router's scan opens the mic too: on Windows every stream must be opened
                                                                  # on the thread that (re)initialised PortAudio, the mixer's (as in the hub)
    r.start()                                                     # its first block scans; a network output's poller answers a moment later
    end = time.monotonic() + 15
    while time.monotonic() < end and not (all(r.available(k) for k in args.outputs) and mic.stream is not None):
        time.sleep(0.5)
        r._last_scan = -1e9                                       # noqa: SLF001 — scan again on the next block, not in RESCAN_S
    closed = [k for k in args.outputs if not r.available(k)] + ([] if mic.stream is not None else ["the microphone"])
    if closed:
        print(f"not open: {closed} (for enclave: is the speaker on? sudo bash core/install/enclave.sh --check on skippy-core)")
        return 1
    time.sleep(1.5)                                               # the outputs' queues settle, the mic ring fills
    clip = chirp()
    got: dict[str, list[float]] = {k: [] for k in args.outputs}
    for i in range(args.rounds):
        print(f"round {i + 1}/{args.rounds}")
        for k in args.outputs:
            v = measure(r, mic, k, clip)
            if v is not None:
                got[k].append(v)
            time.sleep(0.5)
    missing = [k for k, v in got.items() if len(v) < max(1, args.rounds // 2)]
    if missing:
        print(f"too few hearings of {missing}: nothing saved (louder, or the mic nearer?)")
        return 1
    latency = {k: statistics.median(v) for k, v in got.items()}
    spread = {k: (max(v) - min(v)) for k, v in got.items()}
    slowest = max(latency.values())
    wait = {k: round(slowest - v, 4) for k, v in latency.items()}
    for k in args.outputs:
        print(f"{k}: {latency[k] * 1000:.0f} ms (spread {spread[k] * 1000:.0f} ms) -> waits {wait[k] * 1000:.0f} ms")
    if args.dry:
        return 0
    data = sync.load()
    data["align"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "outputs": args.outputs,
                     "latency": {k: round(v, 4) for k, v in latency.items()}, "wait": wait}
    sync.STATE.parent.mkdir(parents=True, exist_ok=True)
    sync.STATE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"saved to {sync.STATE}; restart the hub to use it")
    r.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
