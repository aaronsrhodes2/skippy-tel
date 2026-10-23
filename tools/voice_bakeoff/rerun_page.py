"""The 3-line re-run (2026-10-23, after flask-api's voice id fix): one self-contained page with every sample playable and the
benchmark table, names shown (not blind). Reads <vb>/rerun3/ (written by the re-run in docs/voice-bakeoff.md), writes
<vb>/rerun3/skippy-voice-rerun.html and prints the table as Markdown.

  python tools/voice_bakeoff/rerun_page.py [--vb D:/Aaron/development/SkippyTel/state/voice-bakeoff]
"""
import argparse, base64, html, json, os, re, subprocess, tempfile

ENGINES = [  # key, label, where
    ("elevenlabs", "ElevenLabs (the chat's voice now)", "cloud"),
    ("f5", "F5-TTS v1", "skippy-pc"),
    ("chatterbox", "Chatterbox", "skippy-pc"),
    ("xtts", "XTTS-v2", "skippy-pc"),
    ("qwen3", "Qwen3-TTS 1.7B", "skippy-pc"),
]


def b64(path):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "c.mp3")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-af", "loudnorm=I=-18:TP=-1.5:LRA=11", "-ac", "1",
                        "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "96k", out], check=True)
        return base64.b64encode(open(out, "rb").read()).decode()


def dur(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                capture_output=True, text=True).stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vb", default="D:/Aaron/development/SkippyTel/state/voice-bakeoff")
    a = ap.parse_args()
    R = os.path.join(a.vb, "rerun3")
    lines = json.load(open(os.path.join(R, "elevenlabs", "lines.json"), encoding="utf-8"))
    gpu = {}
    for ln in open(os.path.join(R, "gpu.txt")):
        m = re.match(r"(\w+) rc=0 (\d+), *(\d+) max_util= *(\d+)", ln)
        if m:
            gpu[m[1]] = {"card_peak_mib": int(m[2]), "util_max": int(m[4])}
    rows, clips = [], {}
    for key, label, where in ENGINES:
        if key == "elevenlabs":
            runs = json.load(open(os.path.join(R, key, "chat_timing.json")))["runs"]
            files = [os.path.join(R, key, f"chat_{i:02d}.mp3") for i in range(3)]
            rs = [{"ttfa": r["ttfa_s"], "wall": r["total_s"], "audio": dur(f)} for r, f in zip(runs, files)]
            vram = card = util = rtf1 = None
        else:
            p = os.path.join(R, key, "timing.json")
            if not os.path.exists(p):
                continue
            t = json.load(open(p))
            files = [os.path.join(R, key, f"{i:02d}.wav") for i in range(3)]
            rs = [{"ttfa": r["ttfa_s"], "wall": r["wall_s"], "audio": r["audio_s"]} for r in t["runs"]]
            vram = t["vram_peak_gib"]["reserved"]
            g = gpu.get(key, {})
            card = g["card_peak_mib"] / 1024 if g else None
            util = g.get("util_max")
            first = json.load(open(os.path.join(a.vb, "out", key, "timing.json")))["runs"][:3]   # the 10-line run, same 3 lines
            rtf1 = sum(x["wall_s"] for x in first) / sum(x["audio_s"] for x in first)
        n = len(rs)
        rows.append({"label": label, "where": where, "ttfa": sum(r["ttfa"] for r in rs) / n, "wall": sum(r["wall"] for r in rs) / n,
                     "audio": sum(r["audio"] for r in rs) / n, "rtf": sum(r["wall"] for r in rs) / sum(r["audio"] for r in rs),
                     "vram": vram, "card": card, "util": util, "rtf1": rtf1, "per": rs})
        clips[label] = [b64(f) for f in files]
    f = lambda v, d=2, u="": "–" if v is None else f"{v:.{d}f}{u}"
    md = ["| engine | runs on | first audio | total | audio | RTF | RTF, first run | VRAM (model) | whole card in use (peak) | GPU busy (max) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['label']} | {r['where']} | {f(r['ttfa'], 2, ' s')} | {f(r['wall'], 2, ' s')} | {f(r['audio'], 1, ' s')} | "
                  f"{f(r['rtf'])} | {f(r['rtf1'])} | {f(r['vram'], 1, ' GB')} | {f(r['card'], 1, ' GB')} | {f(r['util'], 0, ' %')} |")
    print("\n".join(md))
    trs = "".join(f"<tr><td>{html.escape(r['label'])}</td><td>{r['where']}</td><td>{f(r['ttfa'], 2, ' s')}</td><td>{f(r['wall'], 2, ' s')}</td>"
                  f"<td>{f(r['audio'], 1, ' s')}</td><td class='{'g' if r['rtf'] < 0.5 else 'a' if r['rtf'] < 1 else 'r'}'>{f(r['rtf'])}</td><td>{f(r['rtf1'])}</td>"
                  f"<td>{f(r['vram'], 1, ' GB')}</td><td>{f(r['card'], 1, ' GB')}</td><td>{f(r['util'], 0, ' %')}</td></tr>" for r in rows)
    sections = ""
    for i, text in enumerate(lines):
        cells = "".join(f"<div class='take'><span>{html.escape(lbl)}</span><audio controls preload='none' src='data:audio/mpeg;base64,{c[i]}'></audio></div>"
                        for lbl, c in clips.items())
        sections += f"<section><p class='line'><b>{i + 1}</b> {html.escape(text)}</p><div class='takes'>{cells}</div></section>"
    page = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rerun_page.html"), encoding="utf-8").read()
    page = page.replace("<!--ROWS-->", trs).replace("<!--LINES-->", sections)
    out = os.path.join(R, "skippy-voice-rerun.html")
    open(out, "w", encoding="utf-8").write(page)
    print(out, f"{os.path.getsize(out) / 2**20:.1f} MiB")


if __name__ == "__main__":
    main()
