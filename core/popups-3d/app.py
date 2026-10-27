"""popups-3d on skippy-core: the QUALITY pop-ups that need the box's big memory, loaded only while they work.

Pop-ups (docs/popups.md): each endpoint takes the plumbing's {"args": {...}, "input"?: {"type", "name", "b64"}} and answers with
the result's bytes under its type, or a non-2xx with {"error": "plain words"}. Registered in services/3d-video-popups/popups.json.

    GET  /health        {"ready", "busy", "popups": {name: {"ready", "peak_gib", "why"?}}, "gpu_free_gib", "gpu_free_pct",
                         "gpu_util_pct"}; "ready" is the models on disk (text_to_3d also needs the core image-studio up)
    POST /image_to_3d   args {"texture"?: true, "shape"?: "full"|"mini", "seed"?}, input: the picture  -> model/gltf-binary
    POST /text_to_3d    args {"prompt", "texture"?, "shape"?, "seed"?}  -> model/gltf-binary (a picture from the core
                         image-studio first, then image_to_3d: a chain, not another model)
    POST /make_video    args {"prompt", "seconds"?, "width"?, "height"?, "seed"?}, input?: a first frame  -> video/mp4

image_to_3d is Hunyuan3D-2 (shape DiT + Paint, Tencent Hunyuan Community Licence) on ROCm PyTorch (AMD's TheRock wheels for
gfx1151); video is Wan 2.2 TI2V-5B (Apache 2.0) through stable-diffusion.cpp's Vulkan build, the picture studio's route.
Every job is its own process (worker.py, or sd-cli), so nothing is held between jobs: the box's language models keep their
~75 GB. One job at a time. The measured seconds and peaks are in docs/3d-video-popups.md.

Reached by skippy-pc's home reporter over the tailnet: host networking on PORT, ufw keeps the LAN out, Tailscale's own rules
let the tailnet in, and ALLOW_FROM narrows that to skippy-pc (and the box itself), like core/image-studio.
"""
from __future__ import annotations

import base64
import binascii
import glob
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

from flask import Flask, Response, jsonify, request

MODELS = os.environ.get("MODELS", "/models")
HF = os.path.join(MODELS, "huggingface")
HY3D = os.path.join(HF, "tencent__Hunyuan3D-2")
HY3D_MINI = os.path.join(HF, "tencent__Hunyuan3D-2mini")
WAN = os.path.join(HF, "Comfy-Org__Wan_2.2_ComfyUI_Repackaged/split_files")
WAN_DIT = os.path.join(WAN, "diffusion_models/wan2.2_ti2v_5B_fp16.safetensors")
WAN_VAE = os.path.join(WAN, "vae/wan2.2_vae.safetensors")
UMT5 = os.path.join(HF, "city96__umt5-xxl-encoder-gguf/umt5-xxl-encoder-Q8_0.gguf")
SD_CLI = os.environ.get("SD_CLI", "/opt/sd/sd-cli")
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
SD_ENV = {**os.environ, "LD_LIBRARY_PATH": os.environ.get("SD_LIB", os.path.dirname(SD_CLI))}   # sd-cli's libraries, for it alone
WORKER = [os.environ.get("WORKER_PYTHON", sys.executable), os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.py")]
IMAGE_STUDIO = os.environ.get("IMAGE_STUDIO_URL", "http://127.0.0.1:7347").rstrip("/")
DRM = os.environ.get("DRM_DEVICE", "/sys/class/drm/card*/device")
PORT = int(os.environ.get("PORT", "7348"))
TIMEOUT_S = int(os.environ.get("TIMEOUT_S", "5400"))     # a 4 s clip is ~45 min; the reporter gives up at 2 h
ALLOW_FROM = {a.strip() for a in os.environ.get("ALLOW_FROM", "").split(",") if a.strip()}   # empty: anyone who can reach the port
MAX_IMAGE = 20 * 2**20

# What each pop-up needs on disk, and its measured peak (GiB of GTT above the resident LLMs; docs/3d-video-popups.md).
PEAK_GIB = {"image_to_3d": 18, "text_to_3d": 30, "make_video": 22}
VIDEO_FPS = 24                       # TI2V-5B's own rate
# Measured on the box (docs/3d-video-popups.md): sampling at 1280x704, 20 steps, is ~320 s for 1 s of clip, ~680 s for 2 s,
# ~1150 s for 3 s (attention grows faster than the frames); the VAE decode ~10 s a frame. 3 s is ~31 min, 4 s ~45 min, 5 s would
# pass an hour, so 4 s is the cap.
VIDEO_MAX_S = 4
VIDEO_STEPS = int(os.environ.get("VIDEO_STEPS", "20"))
# Wan's own negative prompt (its docs and sd.cpp's), in Chinese as it was trained: in English the "subtitles" smudge came back.
VIDEO_NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，"
                  "残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，"
                  "三条腿，背景人很多，倒着走")
PROP_STYLE = ("a single {p}, the whole object in view, centered, three-quarter view, plain white background, soft even studio "
              "light, no shadow on the ground, sharp detail, product render")

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


def _missing(*paths: str) -> str | None:
    for p in paths:
        if not os.path.exists(p):
            return "missing " + (os.path.relpath(p, MODELS).replace(os.sep, "/") if p.startswith(MODELS) else p)
    return None


def _studio_up() -> bool:
    try:
        with urllib.request.urlopen(f"{IMAGE_STUDIO}/health", timeout=10) as r:
            return bool(json.loads(r.read()).get("ready"))
    except (OSError, ValueError):
        return False


def popups() -> dict:
    hy = _missing(os.path.join(HY3D, "hunyuan3d-dit-v2-0-turbo"), os.path.join(HY3D, "hunyuan3d-paint-v2-0-turbo"),
                  os.path.join(HY3D, "hunyuan3d-delight-v2-0"), WORKER[-1])
    wan = _missing(WAN_DIT, WAN_VAE, UMT5, SD_CLI)
    chain = hy or (None if _studio_up() else "the core image-studio is not answering")
    out = {}
    for name, why in (("image_to_3d", hy), ("text_to_3d", chain), ("make_video", wan)):
        out[name] = {"ready": why is None, "peak_gib": PEAK_GIB[name], **({"why": why} if why else {})}
    return out


@app.get("/health")
def health():
    p = popups()
    # One /health for the three (the plumbing reads one per service): ready when the models are on disk. text_to_3d without the
    # picture studio fails in plain words instead.
    ready = p["image_to_3d"]["ready"] and p["make_video"]["ready"]
    return jsonify(ready=ready, busy=_lock.locked(), popups=p, **gpu())


def _job() -> tuple[dict, dict]:
    """(args, input) from the plumbing's body."""
    body = request.get_json(silent=True) or {}
    args, given = body.get("args"), body.get("input")
    return (args if isinstance(args, dict) else {}), (given if isinstance(given, dict) else {})


def _image(given: dict) -> bytes | None:
    raw = given.get("b64")
    if not isinstance(raw, str) or not raw:
        return None
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(data) > MAX_IMAGE or not (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8") or data[8:12] == b"WEBP"):
        return None
    return data


def _seed(body: dict) -> int:
    s = body.get("seed")
    return s if isinstance(s, int) and 0 <= s < 2**31 else int(time.time()) % 2**31


def _tail(run: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in (run.stderr or run.stdout or "").strip().splitlines() if ln.strip()]
    return " | ".join(lines[-3:])[:300]


def _to_3d(png: bytes, body: dict) -> Response:
    """Run worker.py on one picture; the lock is held by the caller."""
    texture = body.get("texture", True) is not False
    shape = "mini" if body.get("shape") == "mini" else "full"
    with tempfile.TemporaryDirectory() as tmp:
        src, out = os.path.join(tmp, "in.img"), os.path.join(tmp, "out.glb")
        with open(src, "wb") as f:
            f.write(png)
        cmd = [*WORKER, "image_to_3d", "--image", src, "--out", out, "--shape", shape, "--seed", str(_seed(body)),
               *([] if texture else ["--no-texture"])]
        started = time.time()
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return jsonify(error=f"the model took longer than {TIMEOUT_S // 60} minutes"), 500
        took = time.time() - started
        if run.returncode != 0 or not os.path.isfile(out):
            print(f"[popups-3d] image_to_3d failed ({run.returncode}): {_tail(run)}", flush=True)
            return jsonify(error=f"image_to_3d failed: {_tail(run)}"[:300]), 500
        stats = _stats(run.stdout)
        with open(out, "rb") as f:
            glb = f.read()
    print(f"[popups-3d] image_to_3d {shape} texture={texture} in {took:.0f}s {stats}", flush=True)
    return Response(glb, mimetype="model/gltf-binary", headers={"X-Render-Seconds": f"{took:.1f}",
                                                                 "X-Faces": str(stats.get("faces", "")),
                                                                 "X-Peak-GiB": str(stats.get("peak_gib", ""))})


def _stats(stdout: str) -> dict:
    for line in reversed((stdout or "").splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {}


@app.post("/image_to_3d")
def image_to_3d():
    body, given = _job()
    png = _image(given)
    if png is None:
        return jsonify(error="a picture is needed (base64 PNG, JPEG or WebP, up to 20 MB)"), 400
    with _lock:
        return _to_3d(png, body)


@app.post("/text_to_3d")
def text_to_3d():
    body, _ = _job()
    prompt = str(body.get("prompt") or "").strip()[:600]
    if not prompt:
        return jsonify(error="a prompt is needed"), 400
    with _lock:
        # The core studio draws one picture (its own process; it lets go of its memory before ours loads), then image_to_3d.
        req = urllib.request.Request(f"{IMAGE_STUDIO}/generate", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"prompt": PROP_STYLE.format(p=prompt), "steps": int(os.environ.get(
                                         "PROP_STEPS", "20")), "width": 1024, "height": 1024, "seed": _seed(body)}).encode())
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                png = r.read()
        except urllib.error.HTTPError as e:
            return jsonify(error=f"the picture for it failed: {e.read()[:200].decode('utf-8', 'replace')}"), 502
        except OSError as e:
            return jsonify(error=f"the image-studio did not answer: {e}"), 502
        return _to_3d(png, body)


def _side(value, default: int, lo: int = 320, hi: int = 1280) -> int:
    try:
        v = int(value or default)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v)) // 32 * 32


@app.post("/make_video")
def make_video():
    body, given = _job()
    prompt = str(body.get("prompt") or "").strip()[:900]
    if not prompt:
        return jsonify(error="a prompt is needed"), 400
    init = _image(given) if given.get("b64") else None
    if given.get("b64") and init is None:
        return jsonify(error="the picture must be base64 PNG, JPEG or WebP, up to 20 MB"), 400
    try:
        seconds = max(1.0, min(float(VIDEO_MAX_S), float(body.get("seconds") or 3)))
    except (TypeError, ValueError):
        seconds = 3.0
    frames = int(seconds * VIDEO_FPS) // 4 * 4 + 1          # Wan wants 4n+1 frames
    width, height = _side(body.get("width"), 1280), _side(body.get("height"), 704)
    with _lock, tempfile.TemporaryDirectory() as tmp:
        raw, out = os.path.join(tmp, "out.webm"), os.path.join(tmp, "out.mp4")
        cmd = [SD_CLI, "-M", "vid_gen", "--diffusion-model", WAN_DIT, "--vae", WAN_VAE, "--t5xxl", UMT5, "-p", prompt,
               "-n", VIDEO_NEGATIVE, "--cfg-scale", "5.0", "--sampling-method", "euler", "--flow-shift", "5.0",
               "--steps", str(VIDEO_STEPS), "-W", str(width), "-H", str(height), "--video-frames", str(frames),
               "--fps", str(VIDEO_FPS), "-s", str(_seed(body)), "--diffusion-fa", "--mmap", "-o", raw,
               # The decode stays on the GPU in sd.cpp's default 256 px tiles, one frame at a time: 512 px tiles with two frames
               # at once reset the whole GPU (the language models' containers with it), and on the CPU it was 60 s a frame.
               "--vae-tiling"]
        if init is not None:
            src = os.path.join(tmp, "init.img")
            with open(src, "wb") as f:
                f.write(init)
            cmd += ["-i", src]
        started = time.time()
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, env=SD_ENV)
        except subprocess.TimeoutExpired:
            return jsonify(error=f"the video took longer than {TIMEOUT_S // 60} minutes"), 500
        if run.returncode != 0 or not os.path.isfile(raw):
            print(f"[popups-3d] sd-cli video failed ({run.returncode}): {_tail(run)}", flush=True)
            return jsonify(error=f"video failed: {_tail(run)}"[:300]), 500
        # H.264 in MP4 plays everywhere (Safari included); faststart so it streams before it has all arrived.
        enc = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", raw, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                              "-crf", "18", "-movflags", "+faststart", out], capture_output=True, text=True, timeout=600)
        if enc.returncode != 0 or not os.path.isfile(out):
            return jsonify(error=f"the mp4 encode failed: {_tail(enc)}"[:300]), 500
        took = time.time() - started
        with open(out, "rb") as f:
            mp4 = f.read()
    print(f"[popups-3d] video {width}x{height} {frames} frames {VIDEO_STEPS} steps in {took:.0f}s", flush=True)
    return Response(mp4, mimetype="video/mp4", headers={"X-Render-Seconds": f"{took:.1f}", "X-Frames": str(frames)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
