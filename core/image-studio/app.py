"""image-studio on skippy-core: the portal chat's QUALITY pictures (make_image_hq), drawn for quality, not speed. The quick
tier (make_image) stays on skippy-pc (services/image-studio).

    GET  /health     {"ready", "busy", "model", "gpu_free_gib", "gpu_free_pct", "gpu_util_pct"}
    POST /generate   {"prompt", "negative"?, "steps"?, "width"?, "height"?, "seed"?} -> image/png bytes

Qwen-Image-2512 (Apache 2.0) at Q8_0, with its Qwen2.5-VL-7B text encoder at Q8_0 and its VAE: about 30 GB, run by
stable-diffusion.cpp's sd-cli on the iGPU through Vulkan. Each picture is its own sd-cli run, so the studio holds no memory
between pictures; the box's language models keep theirs. One picture at a time.

The gpu_* figures are what the portal's patient queue (portal/patient.py) reads before it hands out a picture: free GPU
memory (GTT, which is how this iGPU gets its share of the 128 GB) and how busy the GPU is (llama-swap mid-reply shows here).

Reached by skippy-pc's home reporter over the tailnet: the container uses host networking on PORT. ufw keeps the LAN out
(default deny); Tailscale's own rules let the tailnet in, so ALLOW_FROM narrows that to skippy-pc (and the box itself).
"""
from __future__ import annotations

import glob
import os
import subprocess
import tempfile
import threading
import time

from flask import Flask, Response, jsonify, request

MODELS = os.environ.get("MODELS", "/models")
DIFFUSION = os.path.join(MODELS, "huggingface/unsloth__Qwen-Image-2512-GGUF/qwen-image-2512-Q8_0.gguf")
TEXT = os.path.join(MODELS, "huggingface/unsloth__Qwen2.5-VL-7B-Instruct-GGUF/Qwen2.5-VL-7B-Instruct-Q8_0.gguf")
VAE = os.path.join(MODELS, "huggingface/Comfy-Org__Qwen-Image/split_files/vae/qwen_image_vae.safetensors")
SD_CLI = os.environ.get("SD_CLI", "/opt/sd/sd-cli")
DRM = os.environ.get("DRM_DEVICE", "/sys/class/drm/card*/device")
PORT = int(os.environ.get("PORT", "7347"))
STEPS = int(os.environ.get("STEPS", "40"))          # ~20 s a step at 1024 px on this iGPU (with CFG), so ~13 min a picture
CFG = float(os.environ.get("CFG", "4.0"))
TIMEOUT_S = int(os.environ.get("TIMEOUT_S", "7200"))
ALLOW_FROM = {a.strip() for a in os.environ.get("ALLOW_FROM", "").split(",") if a.strip()}   # empty: anyone who can reach the port
MAX_SIDE, MIN_SIDE = 1664, 512
DEFAULT_NEGATIVE = "blurry, low quality, distorted, deformed, extra limbs, watermark, signature"

app = Flask(__name__)
_lock = threading.Lock()


@app.before_request
def _only_known_callers():
    if ALLOW_FROM and request.remote_addr not in ALLOW_FROM:
        return jsonify(error="not for you"), 403


def _sysfs(name: str) -> int | None:
    for path in sorted(glob.glob(os.path.join(DRM, name))):
        try:
            with open(path, encoding="ascii") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            continue
    return None


def gpu() -> dict:
    """Free GPU memory and how busy the GPU is, from the amdgpu driver's sysfs files (busy: the mean of 8 samples, 2 s)."""
    total, used = _sysfs("mem_info_gtt_total"), _sysfs("mem_info_gtt_used")
    samples = []
    for _ in range(8):
        v = _sysfs("gpu_busy_percent")
        if v is not None:
            samples.append(v)
        time.sleep(0.25)
    out = {}
    if total and used is not None:
        free = max(0, total - used)
        out["gpu_free_gib"] = round(free / 2**30, 1)
        out["gpu_free_pct"] = round(100 * free / total, 1)
    if samples:
        out["gpu_util_pct"] = round(sum(samples) / len(samples), 1)
    return out


def _side(value, default):
    try:
        v = int(value or default)
    except (TypeError, ValueError):
        v = default
    return max(MIN_SIDE, min(MAX_SIDE, v)) // 64 * 64


@app.get("/health")
def health():
    ready = all(os.path.isfile(p) for p in (DIFFUSION, TEXT, VAE, SD_CLI))
    return jsonify(ready=ready, busy=_lock.locked(), model="qwen-image-2512-q8", **gpu())


@app.post("/generate")
def generate():
    body = request.get_json(silent=True) or {}
    prompt = str(body.get("prompt") or "").strip()[:900]
    if not prompt:
        return jsonify(error="a prompt is needed"), 400
    try:
        steps = max(4, min(60, int(body.get("steps") or STEPS)))
    except (TypeError, ValueError):
        steps = STEPS
    width, height = _side(body.get("width"), 1024), _side(body.get("height"), 1024)
    seed = body.get("seed") if isinstance(body.get("seed"), int) else -1
    negative = str(body.get("negative") or DEFAULT_NEGATIVE)[:500]
    with _lock, tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.png")
        cmd = [SD_CLI, "--diffusion-model", DIFFUSION, "--llm", TEXT, "--vae", VAE, "-p", prompt, "-n", negative,
               "--cfg-scale", str(CFG), "--sampling-method", "euler", "--flow-shift", "3", "--steps", str(steps),
               "-W", str(width), "-H", str(height), "-s", str(seed), "--diffusion-fa", "--vae-tiling",
               "--backend", "vae=cpu",   # the decode on the iGPU page-faulted once (a compute ring reset); on the CPU it's ~80 s
               "--mmap",   # without it the weights sit in RAM and again in GPU memory (unified: the same RAM) and the OOM killer fires
               "-o", out]
        started = time.time()
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return jsonify(error=f"the picture took longer than {TIMEOUT_S // 60} minutes"), 500
        took = time.time() - started
        if run.returncode != 0 or not os.path.isfile(out):
            tail = (run.stderr or run.stdout or "").strip().splitlines()[-3:]
            print(f"[image-studio] sd-cli failed ({run.returncode}): {' | '.join(tail)}", flush=True)
            return jsonify(error=f"generation failed: {' '.join(tail)}"[:300]), 500
        with open(out, "rb") as f:
            png = f.read()
    print(f"[image-studio] {width}x{height} {steps} steps cfg {CFG} in {took:.0f}s", flush=True)
    return Response(png, mimetype="image/png", headers={"X-Render-Seconds": f"{took:.1f}"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
