"""Render the bake-off prompts on an image-studio and time them: python bench.py URL OUT_DIR LABEL [--warmup].

Stdlib only, so it runs on skippy-core's host python (python3 bench.py http://127.0.0.1:7347 /tmp/bo qwenimage). Writes
OUT_DIR/LABEL-<n>.png and OUT_DIR/LABEL.json (wall seconds per picture, plus the studio's own timing headers if it sends them).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

PROMPTS = [
    "A golden retriever wearing a small astronaut helmet, sitting on the grey dust of the Moon, the Earth rising behind "
    "it, photorealistic, dramatic low sun, fine fur detail",
    "A cozy cyberpunk ramen stall at night in the rain, neon signs reflecting in the puddles, a friendly robot chef "
    "handing a steaming bowl to a customer in a yellow raincoat, cinematic, shallow depth of field",
    "A vintage art deco travel poster with the words \"VISIT MARS\" in bold letters across the top, a red desert with "
    "tall rock arches, a silver rocket climbing into a dusty pink sky",
]
SEED = 42


def render(url: str, prompt: str) -> tuple[bytes, float, dict]:
    req = urllib.request.Request(f"{url}/generate", method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"prompt": prompt, "seed": SEED, "width": 1024, "height": 1024}).encode())
    t = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        png, headers = r.read(), {k: v for k, v in r.headers.items() if k.lower().startswith("x-")}
    return png, time.time() - t, headers


def main() -> None:
    url, out, label = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
    if "--warmup" in sys.argv:                       # the first call loads the weights: not what a warm chat waits for
        _, wall, headers = render(url, "a red apple on a wooden table")
        print(f"warm-up (load + render) {wall:.1f}s {headers}", flush=True)
    rows = []
    for n, prompt in enumerate(PROMPTS, 1):
        png, wall, headers = render(url, prompt)
        with open(f"{out}/{label}-{n}.png", "wb") as f:
            f.write(png)
        rows.append({"n": n, "prompt": prompt, "wall_s": round(wall, 2), **headers})
        print(f"{label} #{n}: {wall:.1f}s {headers}", flush=True)
    with open(f"{out}/{label}.json", "w") as f:
        json.dump(rows, f, indent=2)


if __name__ == "__main__":
    main()
