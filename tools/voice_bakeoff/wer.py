"""Intelligibility check: transcribe every bake-off clip with the home Whisper (skippytel-whisper, 127.0.0.1:8000) and score the
word error rate against the line it was meant to say. Numbers are normalised to words first, so "4 minutes" and "four minutes"
match. Writes <vb>/out/wer.json and prints a per-model mean.

  python tools/voice_bakeoff/wer.py [--vb D:/Aaron/development/SkippyTel/state/voice-bakeoff]
"""
import argparse, json, os, re, sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_page import SOURCES  # noqa: E402

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def say(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + say(n % 100))
    return say(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + say(n % 1000))


def norm(t):
    t = t.lower().replace(",", "").replace("%", " percent")
    t = re.sub(r"(\d+)\.(\d+)", lambda m: say(int(m[1])) + " point " + " ".join(ONES[int(d)] for d in m[2]), t)
    t = re.sub(r"\d+", lambda m: say(int(m[0])), t)
    t = re.sub(r"[^a-z' ]+", " ", t).replace("kilometers", "kilometres")
    return t.split()


def wer(ref, hyp):
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[-1] / max(len(ref), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vb", default="D:/Aaron/development/SkippyTel/state/voice-bakeoff")
    a = ap.parse_args()
    lines = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lines.json"), encoding="utf-8"))
    res = {}
    for key, folder, pat in SOURCES:
        rows = []
        for i, text in enumerate(lines):
            p = os.path.join(a.vb, "out", folder, pat.format(i=i))
            if not os.path.exists(p):
                break
            with open(p, "rb") as f:
                heard = requests.post("http://127.0.0.1:8000/transcribe", files={"audio": f}, timeout=120).json().get("english_text", "")
            rows.append({"i": i, "heard": heard, "wer": round(wer(norm(text), norm(heard)), 3)})
        if rows:
            res[key] = {"mean_wer": round(sum(r["wer"] for r in rows) / len(rows), 3), "rows": rows}
            print(f"{key:12s} mean WER {res[key]['mean_wer']:.3f}   worst {max(r['wer'] for r in rows):.2f}")
    json.dump(res, open(os.path.join(a.vb, "out", "wer.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
