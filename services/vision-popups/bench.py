"""Benchmark every pop-up model inside the container: cold (seed + load + first run) and warm seconds, and the peak the
PyTorch allocator reserved on the card. Run on skippy-pc with the card as quiet as the reporter makes it (Ollama unloaded):

    docker compose -f services/vision-popups/compose.yml run --rm -v <inputs>:/bench vision-popups python /popups/bench.py

Writes /bench/results.json and prints a table. The inputs folder holds the pictures named in CASES; ocr cases with a
matching <name>.txt are scored by character error rate (CER) against it."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import imagekit as ik
import models as m
from tools import TOOLS

BENCH = Path(sys.argv[1] if len(sys.argv) > 1 else "/bench")

CASES = [
    ("remove_background", {"model": "birefnet"}, "person.png"),
    ("remove_background", {"model": "birefnet"}, "product.png"),
    ("remove_background", {"model": "birefnet"}, "person_2k.png"),
    ("remove_background", {"model": "birefnet_matting"}, "person.png"),
    ("upscale", {}, "small.png"),
    ("upscale", {}, "medium.png"),
    ("depth", {"model": "depth_anything_v2_large"}, "room.png"),
    ("depth", {"model": "depth_anything_v2_small"}, "room.png"),
    ("segment", {"model": "sam2.1_large", "point": [400, 200]}, "person.png"),
    ("segment", {"model": "sam2.1_large"}, "room.png"),
    ("segment", {"model": "sam2.1_base_plus"}, "room.png"),
    ("segment", {"model": "sam2.1_large", "text": "the remote"}, "desk.png"),
    ("detect", {"model": "grounding_dino_base", "text": "find the remotes and the cats"}, "desk.png"),
    ("detect", {"model": "grounding_dino_tiny", "text": "find the remotes and the cats"}, "desk.png"),
    ("pose", {}, "people.png"),
]


def cer(got: str, want: str) -> float:
    """Character error rate: edit distance over the reference's length (whitespace collapsed)."""
    a, b = " ".join(got.split()), " ".join(want.split())
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / max(1, len(b))


def ocr_cases() -> list:
    out = []
    for img in sorted(BENCH.glob("ocr_*.png")):
        task = "table" if "table" in img.stem else "text"
        for model in TOOLS["ocr"]["models"]:
            out.append(("ocr", {"model": model, "task": task}, img.name))
    return out


def one(tool: str, params: dict, picture: str) -> dict:
    import torch
    model = params.get("model") or TOOLS[tool]["default"]
    rgb, alpha = ik.decode((BENCH / picture).read_bytes())
    for n in m.loaded():
        m.unload(n)
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    first = TOOLS[tool]["run"](rgb, alpha, dict(params))
    cold = time.time() - t0
    warm = []
    for _ in range(2):
        t0 = time.time()
        res = TOOLS[tool]["run"](rgb, alpha, dict(params))
        warm.append(time.time() - t0)
    peak = int(torch.cuda.max_memory_reserved() // 2**20)
    row = {"tool": tool, "model": model, "input": picture, "size": list(rgb.size), "cold_s": round(cold, 2),
           "warm_s": round(min(warm), 2), "peak_mb": peak, "text": res["text"][:300], "data": res.get("data")}
    stem = f"{tool}-{model}-{Path(picture).stem}"
    for o in first["outputs"]:
        (BENCH / "out").mkdir(exist_ok=True)
        (BENCH / "out" / f"{stem}-{o['name']}.{o['mime'].split('/')[1]}").write_bytes(o["bytes"])
    ref = BENCH / (Path(picture).stem + ".txt")
    if tool == "ocr":
        (BENCH / "out" / f"{stem}.txt").write_text(res["text"], encoding="utf-8")
        if ref.exists():
            got = re.sub(r"<[^>]+>|\|", " ", res["text"]) if params.get("task") == "table" else res["text"]
            row["cer"] = round(cer(got, ref.read_text(encoding="utf-8")), 4)
    return row


def main() -> None:
    import torch
    torch.cuda.init()
    only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else None
    m.HEADROOM_MB = 300
    rows = []
    for tool, params, picture in CASES + ocr_cases():
        if only and tool not in only:
            continue
        if not (BENCH / picture).exists():
            print(f"skip {tool} {picture}: no such input", flush=True)
            continue
        try:
            row = one(tool, params, picture)
        except Exception as exc:  # noqa: BLE001 — one failure shouldn't end the run
            row = {"tool": tool, "model": params.get("model") or TOOLS[tool]["default"], "input": picture, "error": repr(exc)[:400]}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False)[:600], flush=True)
    for n in m.loaded():
        m.unload(n)
    name = "results.json" if not only else f"results-{'-'.join(sorted(only))}.json"
    (BENCH / name).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{'tool':18} {'model':26} {'input':14} {'cold s':>7} {'warm s':>7} {'peak MB':>8} cer")
    for r in rows:
        print(f"{r['tool']:18} {r['model']:26} {r['input'][:14]:14} {r.get('cold_s', '-'):>7} {r.get('warm_s', '-'):>7} "
              f"{r.get('peak_mb', '-'):>8} {r.get('cer', r.get('error', ''))}")


if __name__ == "__main__":
    main()
