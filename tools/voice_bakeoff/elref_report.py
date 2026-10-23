"""Report for the sweeps cloned from the ElevenLabs Skippy reference (state/voice-bakeoff/elref/<engine>/<setting>/): timing,
word errors (home Whisper), similarity to the ElevenLabs voice (sim_dirs.py), noise raw and polished (polish_noise.py). Prints a
Markdown table, writes elref/report.json and elref/skippy-voice-elref.html (every take playable, polished, beside ElevenLabs).

  python tools/voice_bakeoff/elref_report.py [--vb D:/Aaron/development/SkippyTel/state/voice-bakeoff]
"""
import argparse, base64, glob, html, json, os, subprocess, sys, tempfile

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wer import norm, wer  # noqa: E402

NAMES = {"f5": "F5-TTS", "xtts": "XTTS-v2", "chatterbox": "Chatterbox", "qwen3": "Qwen3-TTS"}


def b64(path):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "c.mp3")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-af", "loudnorm=I=-18:TP=-1.5:LRA=11", "-ac", "1", "-ar", "24000",
                        "-c:a", "libmp3lame", "-b:a", "96k", out], check=True)
        return base64.b64encode(open(out, "rb").read()).decode()


def heard(path):
    with open(path, "rb") as f:
        return requests.post("http://127.0.0.1:8000/transcribe", files={"audio": f}, timeout=120).json().get("english_text", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vb", default="D:/Aaron/development/SkippyTel/state/voice-bakeoff")
    a = ap.parse_args()
    R = os.path.join(a.vb, "elref")
    lines = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lines.json"), encoding="utf-8"))[:3]
    sim = json.load(open(os.path.join(R, "similarity.json")))
    noise = json.load(open(os.path.join(R, "noise.json")))
    el = json.load(open(os.path.join(a.vb, "rerun3", "elevenlabs", "chat_timing.json")))["runs"]
    el_files = [os.path.join(a.vb, "rerun3", "elevenlabs", f"chat_{i:02d}.mp3") for i in range(3)]
    el_noise = json.load(open(os.path.join(a.vb, "rerun3", "noise.json")))
    rows = [{"engine": "ElevenLabs", "setting": "the chat's voice", "ttfa": sum(r["ttfa_s"] for r in el) / 3,
             "wall": sum(r["total_s"] for r in el) / 3, "rtf": None, "wer": sum(wer(norm(t), norm(heard(f))) for t, f in zip(lines, el_files)) / 3,
             "sim": 1.0, "floor_raw": sum(el_noise[f"elevenlabs/chat_{i:02d}.mp3"]["raw"]["floor_db"] for i in range(3)) / 3,
             "floor_fx": sum(el_noise[f"elevenlabs/chat_{i:02d}.mp3"]["fx"]["floor_db"] for i in range(3)) / 3,
             "clips": [b64(os.path.splitext(f)[0] + "_fx.wav") for f in el_files]}]
    for t in sorted(glob.glob(os.path.join(R, "*", "*", "timing.json"))):
        d = os.path.dirname(t)
        key, tag = os.path.relpath(d, R).replace("\\", "/").split("/")
        runs = json.load(open(t))["runs"]
        files = [os.path.join(d, f"{i:02d}.wav") for i in range(3)]
        nz = [noise[f"{key}/{tag}/{i:02d}.wav"] for i in range(3)]
        rows.append({"engine": NAMES.get(key, key), "setting": tag, "ttfa": sum(r["ttfa_s"] for r in runs) / 3,
                     "wall": sum(r["wall_s"] for r in runs) / 3, "rtf": sum(r["wall_s"] for r in runs) / sum(r["audio_s"] for r in runs),
                     "wer": sum(wer(norm(x), norm(heard(f))) for x, f in zip(lines, files)) / 3,
                     "sim": sim.get(f"{key}/{tag}", {}).get("fx"), "sim_raw": sim.get(f"{key}/{tag}", {}).get("raw"),
                     "floor_raw": sum(n["raw"]["floor_db"] for n in nz) / 3, "floor_fx": sum(n["fx"]["floor_db"] for n in nz) / 3,
                     "clips": [b64(os.path.join(d, f"{i:02d}_fx.wav")) for i in range(3)]})
    f = lambda v, d=2, u="": "–" if v is None else f"{v:.{d}f}{u}"
    md = ["| engine | setting | first audio | total | RTF | word errors | like ElevenLabs (polished) | pause noise raw → polished |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['engine']} | {r['setting']} | {f(r['ttfa'], 2, ' s')} | {f(r['wall'], 2, ' s')} | {f(r['rtf'])} | {f(r['wer'] * 100, 0, ' %')} | "
                  f"{f(r['sim'])} | {f(r['floor_raw'], 0)} → {f(r['floor_fx'], 0)} dB |")
    print("\n".join(md))
    json.dump([{k: v for k, v in r.items() if k != "clips"} for r in rows], open(os.path.join(R, "report.json"), "w"), indent=1)
    trs = "".join(f"<tr><td>{html.escape(r['engine'])}</td><td>{html.escape(r['setting'])}</td><td>{f(r['ttfa'], 2, ' s')}</td><td>{f(r['wall'], 2, ' s')}</td>"
                  f"<td>{f(r['rtf'])}</td><td>{f(r['wer'] * 100, 0, ' %')}</td><td>{f(r['sim'])}</td><td>{f(r['floor_raw'], 0)} → {f(r['floor_fx'], 0)} dB</td></tr>" for r in rows)
    secs = ""
    for i, text in enumerate(lines):
        cells = "".join(f"<div class='take'><span>{html.escape(r['engine'])} · {html.escape(r['setting'])}</span><audio controls preload='none' "
                        f"src='data:audio/mpeg;base64,{r['clips'][i]}'></audio></div>" for r in rows)
        secs += f"<section><p class='line'><b>{i + 1}</b> {html.escape(text)}</p><div class='takes'>{cells}</div></section>"
    page = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "elref_page.html"), encoding="utf-8").read()
    out = os.path.join(R, "skippy-voice-elref.html")
    open(out, "w", encoding="utf-8").write(page.replace("<!--ROWS-->", trs).replace("<!--LINES-->", secs))
    print(out, f"{os.path.getsize(out) / 2**20:.1f} MiB")


if __name__ == "__main__":
    main()
