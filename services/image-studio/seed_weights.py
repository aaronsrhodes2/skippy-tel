#!/usr/bin/env python3
"""Put the studio's weights in its Docker volume: python services/image-studio/seed_weights.py [REPO_DIR] [--volume NAME]
                                                    [--prepare IMAGE]

Why: Docker Desktop reads D: (the models archive, an HDD shared into its VM) at about 8 MB/s, so loading the weights
straight from the archive took 11 minutes for SDXL and would take an hour for Z-Image. The archive stays the master copy;
this copies one model out of it into a Docker volume (ext4 on the SSD), with any float32 tensor stored as bfloat16 on the
way (the studio computes in bf16 anyway: Z-Image's transformer shrinks from 24.6 GB to 12.3 GB). --prepare then runs the
studio's own image once (`app.py prepare`, on the GPU) to save its 4-bit text encoder into the volume, which cuts a cold
start by a minute or more. Re-run it after the archive's copy changes; it replaces the volume's contents. Needs torch +
safetensors on the host (skippy-pc has both).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_REPO = Path(os.environ.get("MODELS_ROOT", r"D:\models")) / "huggingface" / "black-forest-labs__FLUX.2-klein-4B"
SKIP = {".cache", "assets"}


def convert(src: Path, dst: Path) -> None:
    import torch
    from safetensors import safe_open
    from safetensors.torch import save_file
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if path.is_dir() or SKIP & set(rel.parts):
            continue
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix != ".safetensors":
            shutil.copy2(path, out)
            continue
        tensors, changed = {}, False
        with safe_open(str(path), framework="pt") as f:
            meta = f.metadata()
            for key in f.keys():
                t = f.get_tensor(key)
                if t.dtype == torch.float32:
                    t, changed = t.to(torch.bfloat16), True
                tensors[key] = t
        if changed:
            save_file(tensors, str(out), metadata=meta)
        else:
            shutil.copy2(path, out)
        print(f"  {rel}{' (bf16)' if changed else ''}", flush=True)
        del tensors
    for index in dst.rglob("*.safetensors.index.json"):   # shard names are unchanged; only the total size moved
        data = json.loads(index.read_text(encoding="utf-8"))
        data.setdefault("metadata", {})["total_size"] = sum(p.stat().st_size for p in index.parent.glob("*.safetensors"))
        index.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", nargs="?", default=str(DEFAULT_REPO))
    ap.add_argument("--volume", default="skippytel_image-studio-weights")
    ap.add_argument("--prepare", metavar="IMAGE", help="the built studio image, e.g. skippytel-image-studio")
    a = ap.parse_args()
    src = Path(a.repo)
    if not (src / "model_index.json").is_file():
        raise SystemExit(f"{src}: not a diffusers model folder (no model_index.json)")
    with tempfile.TemporaryDirectory(prefix="image-studio-seed-", dir=os.environ.get("SEED_STAGING")) as tmp:
        stage = Path(tmp) / "w"
        print(f"converting {src} -> {stage}", flush=True)
        convert(src, stage)
        source = src / "SOURCE.json"
        info = json.loads(source.read_text(encoding="utf-8")) if source.is_file() else {}
        (stage / "SEEDED_FROM.json").write_text(json.dumps({"from": str(src), **info}, indent=2), encoding="utf-8")
        helper = "image-studio-seed"
        env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
        run = lambda *args: subprocess.run(["docker", *args], check=True, env=env)  # noqa: E731
        subprocess.run(["docker", "rm", "-f", helper], env=env, capture_output=True)
        run("volume", "create", a.volume)
        run("run", "-d", "--name", helper, "-v", f"{a.volume}:/weights", "python:3.12-slim", "sleep", "infinity")
        try:
            run("exec", helper, "sh", "-c", "rm -rf /weights/* /weights/.[!.]*; true")
            print(f"copying into volume {a.volume}", flush=True)
            run("cp", f"{stage}{os.sep}.", f"{helper}:/weights")
            run("exec", helper, "du", "-sh", "/weights")
        finally:
            subprocess.run(["docker", "rm", "-f", helper], env=env, capture_output=True)
    if a.prepare:
        run("run", "--rm", "--gpus", "all", "--memory", "5g", "-v", f"{a.volume}:/weights", a.prepare, "prepare")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
