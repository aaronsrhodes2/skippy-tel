"""Put the search pop-up's two models into its Docker volume, as float16 safetensors (run once on skippy-pc's host, and again only
when a model changes).

    python services/search-popups/seed_weights.py            # D:/models -> D:/models/derived/search-popups -> the volume

Why not mount D:/models straight in, as tts-studio does: Docker reads the D: drive at about 8 MB/s (the image-studio session's
measurement), so each load of a 2.2 GB model would take minutes; the volume lives on Docker's own disk. Why convert: bge-m3 ships
only a float32 pytorch_model.bin, which torch.load reads whole into RAM (2.3 GB) inside Docker's 8 GB VM, and the VM froze twice on
2026-10-23 when the card spilled into it. float16 safetensors are half the size and load straight onto the card, tensor by tensor.
The converted copies stay on D: (models/derived/search-popups) beside the pinned originals.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

MODELS = Path(os.environ.get("MODELS_ROOT", "D:/models"))
STAGE = MODELS / "derived" / "search-popups"
VOLUME = os.environ.get("SEARCH_WEIGHTS_VOLUME", "skippytel_search-popups-weights")
WANT = {"bge-m3": ("BAAI__bge-m3", "AutoModel"), "bge-reranker-v2-m3": ("BAAI__bge-reranker-v2-m3", "AutoModelForSequenceClassification")}


def convert(name: str, repo: str, cls: str) -> Path:
    import torch
    import transformers
    src, out = MODELS / "huggingface" / repo, STAGE / name
    if (out / "model.safetensors").is_file():
        print(f"{name}: already converted")
        return out
    out.mkdir(parents=True, exist_ok=True)
    model = getattr(transformers, cls).from_pretrained(str(src), dtype=torch.float16)
    model.save_pretrained(str(out), safe_serialization=True)
    transformers.AutoTokenizer.from_pretrained(str(src)).save_pretrained(str(out))
    for extra in ("SOURCE.json", "README.md"):
        if (src / extra).is_file():
            shutil.copy2(src / extra, out / f"original-{extra}")
    print(f"{name}: converted to {out}")
    return out


def main() -> int:
    for name, (repo, cls) in WANT.items():
        convert(name, repo, cls)
    subprocess.run(["docker", "volume", "create", VOLUME], check=True, capture_output=True)
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    cmd = ["docker", "run", "--rm", "-v", f"{STAGE.as_posix()}:/s:ro", "-v", f"{VOLUME}:/w", "alpine",
           "sh", "-c", "cp -r /s/. /w/ && du -sh /w/*"]
    return subprocess.run(cmd, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
