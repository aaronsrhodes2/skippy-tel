"""Background noise, raw and as the chat plays it: run each clip through voice-gate's own polish chain (highpass, RNNoise bd.rnnn,
the warmth EQ: gate.fx_chain_for) and measure both. Writes <name>_fx.wav beside each clip and <dir>/noise.json.

Measured on 20 ms frames: floor = 10th-percentile frame level (the pauses and breaths), speech = 90th percentile, gap = speech - floor
(bigger is cleaner). Same idea as state/voice-lab/voice_lab.py, which tuned the chain on ElevenLabs.

  python tools/voice_bakeoff/polish_noise.py <dir with *.wav or *.mp3, searched recursively> [--model D:/models/tools-audio/rnnoise/bd.rnnn]
"""
import argparse, glob, json, os, subprocess

import numpy as np

TAIL = ",equalizer=f=140:t=h:width=140:g=2.5,equalizer=f=9500:t=h:width=2000:g=-3"   # = services/voice-gate/gate.py FX_TAIL


def chain(model):
    m = model.replace("\\", "/").replace(":", "\\\\:")          # ffmpeg filter-arg escaping for a Windows drive letter
    return f"highpass=f=70,arnndn=m={m}" + TAIL


def decode(path):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", "24000", "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype=np.float32)


def measure(path):
    x = decode(path)
    n = int(0.020 * 24000)
    f = x[: len(x) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((f ** 2).mean(axis=1)) + 1e-9)
    floor, speech = float(np.percentile(db, 10)), float(np.percentile(db, 90))
    return {"floor_db": round(floor, 1), "speech_db": round(speech, 1), "gap_db": round(speech - floor, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--model", default="D:/models/tools-audio/rnnoise/bd.rnnn")
    a = ap.parse_args()
    res = {}
    files = [f for f in glob.glob(os.path.join(a.root, "**", "*"), recursive=True)
             if f.endswith((".wav", ".mp3")) and "_fx." not in f]
    for f in sorted(files):
        fx = os.path.splitext(f)[0] + "_fx.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f, "-af", chain(a.model), "-ar", "24000", "-ac", "1", fx], check=True)
        res[os.path.relpath(f, a.root).replace("\\", "/")] = {"raw": measure(f), "fx": measure(fx)}
    json.dump(res, open(os.path.join(a.root, "noise.json"), "w"), indent=1)
    groups = {}
    for k, v in res.items():
        groups.setdefault(os.path.dirname(k), []).append(v)
    for g, vs in groups.items():
        m = lambda s, q: sum(v[s][q] for v in vs) / len(vs)
        print(f"{g:34s} raw floor {m('raw', 'floor_db'):6.1f} gap {m('raw', 'gap_db'):5.1f} | polished floor {m('fx', 'floor_db'):6.1f} gap {m('fx', 'gap_db'):5.1f}")


if __name__ == "__main__":
    main()
