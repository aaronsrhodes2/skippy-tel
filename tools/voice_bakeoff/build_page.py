"""Build the blind A/B page from the bake-off output: one self-contained HTML file (clips embedded), for the Captain to rate by ear.

  python tools/voice_bakeoff/build_page.py [--vb D:/Aaron/development/SkippyTel/state/voice-bakeoff] [--seed 7]

Every clip, ElevenLabs included, is loudness-normalised (EBU R128, -18 LUFS) and re-encoded the same way (MP3 96 kb/s mono 24 kHz),
so neither level nor codec gives the source away. Labels are shuffled per line. Writes <vb>/skippy-voice-bakeoff.html.
The clips are the personal-use Skippy voice: the page is for the Captain, never for the repo.
"""
import argparse, base64, html, json, os, random, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = [  # (key, folder under out/, file pattern)
    ("elevenlabs", "elevenlabs", "el_{i:02d}.mp3"),
    ("el_chat", "elevenlabs-chat", "chat_{i:02d}.mp3"),
    ("qwen3", "qwen3", "{i:02d}.wav"),
    ("chatterbox", "chatterbox", "{i:02d}.wav"),
    ("f5", "f5", "{i:02d}.wav"),
    ("xtts", "xtts", "{i:02d}.wav"),
]
NAMES = {"elevenlabs": "ElevenLabs \u00b7 Skippy voice (vault)", "el_chat": "ElevenLabs \u00b7 what the chat speaks today", "qwen3": "Qwen3-TTS 1.7B Base", "chatterbox": "Chatterbox",
         "f5": "F5-TTS v1", "xtts": "XTTS-v2"}


def mp3_b64(path):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "c.mp3")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-af", "loudnorm=I=-18:TP=-1.5:LRA=11",
                        "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "96k", out], check=True)
        return base64.b64encode(open(out, "rb").read()).decode()


def speed_rows(vb):
    rows = []
    el = json.load(open(f"{vb}/out/elevenlabs/el_timing.json"))
    r = el["runs"]
    rows.append({"key": "elevenlabs", "name": NAMES["elevenlabs"], "where": "ElevenLabs cloud (" + el["model"] + ")",
                 "ttfa": sum(x["ttfa_s"] for x in r) / len(r), "rtf": None, "wall": sum(x["total_s"] for x in r) / len(r),
                 "vram": None, "load": None, "streamed": True})
    if os.path.exists(f"{vb}/out/elevenlabs-chat/chat_timing.json"):
        c = json.load(open(f"{vb}/out/elevenlabs-chat/chat_timing.json"))["runs"]
        rows.append({"key": "el_chat", "name": NAMES["el_chat"], "where": "ElevenLabs cloud (" + el["model"] + ")",
                     "ttfa": sum(x["ttfa_s"] for x in c) / len(c), "rtf": None, "wall": sum(x["total_s"] for x in c) / len(c),
                     "vram": None, "load": None, "streamed": True})
    for key, folder, _ in SOURCES[2:]:
        p = f"{vb}/out/{folder}/timing.json"
        if not os.path.exists(p):
            continue
        t = json.load(open(p))
        r = t["runs"]
        rows.append({"key": key, "name": NAMES[key], "where": "skippy-pc \u00b7 " + t["gpu"].replace("NVIDIA GeForce ", ""),
                     "ttfa": sum(x["ttfa_s"] for x in r) / len(r), "rtf": sum(x["wall_s"] for x in r) / sum(x["audio_s"] for x in r),
                     "wall": sum(x["wall_s"] for x in r) / len(r), "vram": t["vram_peak_gib"]["reserved"], "load": t["load_s"],
                     "streamed": r[0]["streamed"]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vb", default="D:/Aaron/development/SkippyTel/state/voice-bakeoff")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    lines = json.load(open(os.path.join(HERE, "lines.json"), encoding="utf-8"))
    present = [s for s in SOURCES if os.path.exists(os.path.join(a.vb, "out", s[1], s[2].format(i=0)))]
    rng = random.Random(a.seed)
    items = []
    for i, text in enumerate(lines):
        order = present[:]
        rng.shuffle(order)
        clips = [{"label": "ABCDEFG"[j], "src": k, "b64": mp3_b64(os.path.join(a.vb, "out", folder, pat.format(i=i)))}
                 for j, (k, folder, pat) in enumerate(order)]
        items.append({"text": text, "clips": clips})
    data = {"ref": mp3_b64(os.path.join(a.vb, "ref", "ref.wav")), "refText": open(os.path.join(a.vb, "ref", "ref.txt"), encoding="utf-8").read().strip(),
            "lines": items, "names": NAMES, "speed": speed_rows(a.vb),
            "sim": json.load(open(os.path.join(a.vb, "out", "similarity.json"))), "wer": {k: v["mean_wer"] for k, v in json.load(open(os.path.join(a.vb, "out", "wer.json"))).items()}}
    page = open(os.path.join(HERE, "page.html"), encoding="utf-8").read().replace("/*DATA*/null", json.dumps(data))
    out = os.path.join(a.vb, "skippy-voice-bakeoff.html")
    open(out, "w", encoding="utf-8").write(page)
    print(out, f"{os.path.getsize(out) / 2**20:.1f} MiB", [s[0] for s in present])


if __name__ == "__main__":
    main()
