"""Skippy's wake word, at home: record the Captain saying it (and saying other things), test the trained model on those recordings, watch it live.

    ./.venv/Scripts/python wakectl.py record positive 50       say "Skippy" (or "Hey Skippy") each time it asks: 50 clips of the real thing
    ./.venv/Scripts/python wakectl.py record negative 30       say anything else: "Skipper", "skip it", a sentence, a laugh
    ./.venv/Scripts/python wakectl.py test                     how the model does on them, at several thresholds; the one to use
    ./.venv/Scripts/python wakectl.py listen                   its score live, and WOKE when it fires (Ctrl+C to stop)

Clips go to state/wakeword/real/{positive,negative}/ (git-ignored; 16 kHz mono WAV). train.sh mixes the positives into the training data, so record
before training; test needs a trained state/wakeword/skippy.onnx. Uses the same microphone settings as ear.py (SATELLITE_MIC, SATELLITE_GAIN).
"""
from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

from vad import Vad, rms
from voicegate import wav_bytes
from wakeword import FRAME, Spotter, default_model

THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def real_dir(env: dict) -> Path:
    return default_model(env).parent / "real"


def read_wav(path: Path) -> list[float]:
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getsampwidth(), w.getnchannels()) != (16000, 2, 1):
            raise ValueError(f"{path.name} is not 16 kHz 16-bit mono")
        raw = w.readframes(w.getnframes())
    return [int.from_bytes(raw[i:i + 2], "little", signed=True) / 32768 for i in range(0, len(raw) - 1, 2)]


def peak(model, samples: list[float]) -> float:
    """The highest score the model gives a clip, streamed the way the listener streams (a second of quiet before and after, as openWakeWord does)."""
    reset = getattr(model, "reset", None)
    if reset:
        reset()
    spot = Spotter(model, threshold=2.0)                      # never fires: we only want the scores
    best = 0.0
    pad = [0.0] * 16000
    stream = pad + list(samples) + pad
    for i in range(0, len(stream) - FRAME + 1, FRAME):
        spot.feed(stream[i:i + FRAME])
        best = max(best, spot.last)
    return best


def score(model, real: Path) -> tuple[list[float], list[float]]:
    held = real / "test"                                        # wakerec.py holds every 5th clip out of training: those are the honest test
    base = held if any(held.glob("*/*.wav")) else real
    pos = [peak(model, read_wav(p)) for p in sorted((base / "positive").glob("*.wav"))]
    neg = [peak(model, read_wav(p)) for p in sorted((base / "negative").glob("*.wav"))]
    return pos, neg


def report(pos: list[float], neg: list[float], out=print) -> float | None:
    """Recall and false wakes at each threshold; the suggestion is the highest threshold that still hears 90% of his "Skippy"s with no false wake."""
    if not pos:
        out("No recordings of him saying it: wakectl.py record positive 50")
        return None
    out(f"{len(pos)} of him saying it, {len(neg)} of other things")
    out("threshold  heard  false wakes")
    best = None
    for th in THRESHOLDS:
        heard = sum(s >= th for s in pos)
        false = sum(s >= th for s in neg)
        out(f"   {th:.1f}     {heard * 100 // len(pos):>3}%   {false}")
        if heard >= 0.9 * len(pos) and false == 0:
            best = th
    if best is None:
        out("No threshold hears 9 in 10 with no false wake: record more positives and train again (train.sh), or accept a lower bar.")
    else:
        out(f"Use SATELLITE_WAKE_THRESHOLD={best} (in the vault .env).")
    return best


def record(kind: str, count: int, env: dict, out=print) -> int:
    import audio                                              # noqa: PLC0415 — only a machine with a microphone
    from ear import _device                                   # noqa: PLC0415
    folder = real_dir(env) / kind
    folder.mkdir(parents=True, exist_ok=True)
    prompt = 'Say "Skippy" (sometimes "Hey Skippy"), as you would to the house' if kind == "positive" else 'Say something that is NOT his name ("Skipper", "skip it", anything)'
    with audio.Mic(_device(env.get("SATELLITE_MIC")), float(env.get("SATELLITE_GAIN", "1"))) as mic:
        for n in range(count):
            out(f"[{n + 1}/{count}] {prompt} ...")
            mic.flush()
            vad, pre, buf = Vad(floor_min=float(env.get("SATELLITE_FLOOR", "0.012")), end_silence_ms=500), [], None
            for t, samples in mic.blocks():
                pre = (pre + [samples])[-8:]
                ev = vad.push(rms(samples), t)
                if buf is not None and ev != "speech":
                    buf.append(samples)
                if ev == "speech":
                    buf = list(pre)
                elif ev == "end" and buf is not None:
                    break
            clip = [float(x) for block in buf for x in block]
            name = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{n:03}.wav"
            name.write_bytes(wav_bytes(clip))
            out(f"   kept {len(clip) / 16000:.1f} s")
    out(f"{count} clips in {folder}")
    return 0


def listen(env: dict, out=print) -> int:
    import audio                                              # noqa: PLC0415
    from ear import _device                                   # noqa: PLC0415
    spot = Spotter.load(default_model(env), threshold=float(env.get("SATELLITE_WAKE_THRESHOLD", "0.5")))
    with audio.Mic(_device(env.get("SATELLITE_MIC")), float(env.get("SATELLITE_GAIN", "1"))) as mic:
        out(f"listening with {default_model(env).name} at threshold {spot.threshold}. Say Skippy.")
        for _, samples in mic.blocks():
            if spot.feed(samples):
                out(f"WOKE   (score {spot.last:.2f})")
            elif spot.last > 0.1:
                out(f"{spot.last:5.2f} " + "#" * int(spot.last * 50))
    return 0


def main(argv=None, out=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record"); p.add_argument("kind", choices=("positive", "negative")); p.add_argument("count", type=int, nargs="?", default=30)
    sub.add_parser("test")
    sub.add_parser("listen")
    args = ap.parse_args(argv)
    from ear import load_env                                  # noqa: PLC0415
    env = load_env()
    if args.cmd == "record":
        return record(args.kind, args.count, env, out)
    if args.cmd == "listen":
        return listen(env, out)
    spot = Spotter.load(default_model(env))
    pos, neg = score(spot.model, real_dir(env))
    return 0 if report(pos, neg, out) is not None else 1


if __name__ == "__main__":
    sys.exit(main())
