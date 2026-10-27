#!/usr/bin/env python3
"""Time audio-popups against the real service with real production prompts, and keep every output for the Captain to hear.

    python services/audio-popups/bench.py --url http://127.0.0.1:18004 --clip "<a track>" --out ../../state/audio-popups

Per model: one cold call (includes the load), then warm calls. Seconds per output come from the service's X-Seconds (the work
alone) and the wall clock (what a job waits); peak VRAM is the service's own torch peak (X-Peak-VRAM-MB) and, beside it, the
whole card's peak from nvidia-smi sampled every 0.25 s (what the neighbours see). results.json + the audio land in --out.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

SFX_QUICK = [
    ("kick-808", "punchy 808 kick drum, one shot, deep sub tail", 2),
    ("hat-closed", "crisp closed hi-hat, one shot, tight, techno", 1),
    ("snare-plate", "metallic snare hit with plate reverb, one shot", 2),
    ("riser", "white noise riser sweep, build up FX, EDM", 3),
    ("impact", "sub drop impact boom, cinematic downlifter", 3),
    ("glitch", "glitchy digital stutter FX, bitcrushed", 2),
]
SFX_LOOP = [
    ("techhouse-hats", {"prompt": "tech-house hi-hat loop, shuffled, crisp", "bpm": 128, "bars": 4, "key": "A minor"}),
    ("dnb-break", {"prompt": "liquid drum and bass break, rolling, amen style", "bpm": 174, "bars": 4}),
    ("house-chords", {"prompt": "deep house chord stab loop, warm Rhodes, filtered", "bpm": 124, "bars": 4, "key": "F minor"}),
    ("dubstep-bass", {"prompt": "dubstep wobble bass loop, growling, heavy", "bpm": 140, "bars": 2, "key": "E minor"}),
    ("synth-arp", {"prompt": "analog synth arpeggio loop, Juno, bright", "bpm": 120, "bars": 4, "key": "C minor"}),
    ("lofi-drums", {"prompt": "lo-fi hip hop drum loop, dusty vinyl, swing", "bpm": 90, "bars": 2}),
    ("ambient-pad", {"prompt": "evolving ambient synth pad texture, lush", "seconds": 20, "key": "D minor"}),
]
MELODY = [
    ("melodic-techno", "melodic techno, 124 BPM, analog synth lead, driving bassline, four on the floor"),
    ("deep-house", "deep house, 120 BPM, warm pads, shuffled hats, soulful"),
    ("synthwave", "synthwave, retro 80s, gated drums, bright arpeggios"),
]


class CardPeak:
    """The whole card's peak used memory (MiB) while a call runs, from nvidia-smi."""

    def __init__(self):
        self.peak, self._stop = 0, threading.Event()

    def _run(self):
        while not self._stop.is_set():
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=5).stdout
                self.peak = max(self.peak, int(out.split()[0]))
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(0.25)

    def __enter__(self):
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()


def call(url: str, route: str, json_body: dict | None = None, form: dict | None = None, clip: bytes | None = None,
         clip_name: str = "clip.wav", timeout: int = 1800):
    if json_body is not None:
        data, ctype = json.dumps(json_body).encode(), "application/json"
    else:
        boundary = uuid.uuid4().hex
        parts = []
        for k, v in (form or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        if clip is not None:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="audio"; filename="{clip_name}"\r\n'
                         f"Content-Type: application/octet-stream\r\n\r\n".encode() + clip + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data, ctype = b"".join(parts), f"multipart/form-data; boundary={boundary}"
    req = urllib.request.Request(url + route, data=data, method="POST", headers={"Content-Type": ctype})
    started = time.time()
    with CardPeak() as card, urllib.request.urlopen(req, timeout=timeout) as r:
        body, headers = r.read(), dict(r.headers)
    return body, {"wall_s": round(time.time() - started, 2), "work_s": float(headers.get("X-Seconds", 0)),
                  "peak_vram_mb": int(headers.get("X-Peak-VRAM-MB", 0)), "card_peak_mb": card.peak,
                  "seed": headers.get("X-Seed"), "notes": headers.get("X-Notes"), "bytes": len(body)}


def excerpt(src: Path, start: float, seconds: float, out: Path) -> bytes:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(start), "-t", str(seconds), "-i", str(src),
                    "-ac", "2", "-ar", "44100", str(out)], check=True)
    return out.read_bytes()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:18004")
    ap.add_argument("--clip", required=True, help="a real track: stems use all of it, melody/MIDI an excerpt")
    ap.add_argument("--clip-start", type=float, default=0.0)
    ap.add_argument("--out", default="state/audio-popups")
    ap.add_argument("--only", nargs="*", help="sfx_quick sfx_loop melody_variations stems audio_to_midi")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results_file = out / "results.json"
    results = json.loads(results_file.read_text()) if results_file.exists() else {}
    want = set(args.only or ["sfx_quick", "sfx_loop", "melody_variations", "stems", "audio_to_midi"])
    track = Path(args.clip)
    ref = excerpt(track, args.clip_start, 15, out / "input-excerpt.wav")

    def keep(model, name, body, stats, ext, extra=None):
        (out / model).mkdir(exist_ok=True)
        (out / model / f"{name}.{ext}").write_bytes(body)
        row = {"name": name, "file": f"{model}/{name}.{ext}", **stats, **(extra or {})}
        print(f"{model:18} {name:16} work {stats['work_s']:7.2f}s  wall {stats['wall_s']:7.2f}s  "
              f"vram {stats['peak_vram_mb']:6d} MB  card {stats['card_peak_mb']:6d} MB", flush=True)
        results.setdefault(model, []).append(row)
        results_file.write_text(json.dumps(results, indent=2))

    for model in ("sfx_quick", "sfx_loop", "melody_variations", "stems", "audio_to_midi"):
        if model not in want:
            continue
        results[model] = []
        if model == "sfx_quick":
            for name, prompt, secs in SFX_QUICK:
                body, st = call(args.url, "/sfx_quick", {"prompt": prompt, "seconds": secs})
                keep(model, name, body, st, "wav", {"prompt": prompt, "seconds": secs})
        elif model == "sfx_loop":
            for name, ask in SFX_LOOP:
                body, st = call(args.url, "/sfx_loop", ask)
                keep(model, name, body, st, "wav", {"ask": ask})
        elif model == "melody_variations":
            for name, prompt in MELODY:
                body, st = call(args.url, "/melody_variations", form={"prompt": prompt, "seconds": 15}, clip=ref)
                keep(model, name, body, st, "wav", {"prompt": prompt, "seconds": 15})
        elif model == "stems":
            full = track.read_bytes()
            duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                             "csv=p=0", str(track)], capture_output=True, text=True).stdout or 0)
            for quality in ("fast", "ft"):
                body, st = call(args.url, "/stems", form={"quality": quality, "format": "mp3"}, clip=full,
                                clip_name=track.name)
                keep(model, f"stems-{quality}", body, st, "zip", {"quality": quality, "track_s": round(duration, 1)})
        elif model == "audio_to_midi":
            body, st = call(args.url, "/audio_to_midi", form={"bpm": 120}, clip=ref)
            keep(model, "excerpt", body, st, "mid")
            bass = out / "stems" / "stems-ft.zip"
            if bass.exists():
                import io
                import zipfile
                clip = zipfile.ZipFile(io.BytesIO(bass.read_bytes())).read("bass.mp3")
                body, st = call(args.url, "/audio_to_midi", form={"bpm": 120}, clip=clip, clip_name="bass.mp3")
                keep(model, "bass-stem", body, st, "mid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
