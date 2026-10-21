"""image-studio: Stable Diffusion XL on skippy-pc's GPU, for the portal chat's make_image (the Captain only, for now).

    GET  /health     {"ready": true, "loaded": bool, "model": ...}: ready once the weights file is there (it loads on demand)
    POST /generate   {"prompt", "negative"?, "steps"?, "width"?, "height"?, "seed"?} -> image/png bytes

The weights are the nightly archive's own copy (D:/models, mounted read-only): sd_xl_base_1.0.safetensors. The pipeline
loads on the first request and unloads after IDLE_UNLOAD_S idle seconds, so Ollama and the music studio get the card back.
One picture at a time. No port is published: only the home reporter (same Docker network) calls it.
"""
from __future__ import annotations

import io
import os
import threading
import time

from flask import Flask, Response, jsonify, request

WEIGHTS = os.environ.get("SDXL_WEIGHTS", "/weights/sd_xl_base_1.0.safetensors")
IDLE_UNLOAD_S = int(os.environ.get("IDLE_UNLOAD_S", "600"))
MAX_SIDE, MIN_SIDE = 1344, 512
DEFAULT_NEGATIVE = "blurry, low quality, distorted, deformed, extra limbs, watermark, text, signature"

app = Flask(__name__)
_lock = threading.Lock()
_pipe = None
_last_used = 0.0


def _load():
    global _pipe
    if _pipe is None:
        import torch
        from diffusers import StableDiffusionXLPipeline
        _pipe = StableDiffusionXLPipeline.from_single_file(WEIGHTS, torch_dtype=torch.float16, use_safetensors=True)
        _pipe.to("cuda")
        _pipe.set_progress_bar_config(disable=True)
    return _pipe


def _unload_when_idle():
    global _pipe
    while True:
        time.sleep(30)
        with _lock:
            if _pipe is not None and time.time() - _last_used > IDLE_UNLOAD_S:
                import torch
                _pipe = None
                torch.cuda.empty_cache()
                print("[image-studio] unloaded after idle", flush=True)


def _side(value, default):
    try:
        v = int(value or default)
    except (TypeError, ValueError):
        v = default
    return max(MIN_SIDE, min(MAX_SIDE, v)) // 64 * 64


@app.get("/health")
def health():
    return jsonify(ready=os.path.isfile(WEIGHTS), loaded=_pipe is not None, model="sdxl-base-1.0")


@app.post("/generate")
def generate():
    global _last_used
    body = request.get_json(silent=True) or {}
    prompt = str(body.get("prompt") or "").strip()[:900]
    if not prompt:
        return jsonify(error="a prompt is needed"), 400
    import torch
    try:
        steps = max(8, min(50, int(body.get("steps") or 28)))
    except (TypeError, ValueError):
        steps = 28
    width, height = _side(body.get("width"), 1024), _side(body.get("height"), 1024)
    seed = body.get("seed")
    with _lock:
        started = time.time()
        try:
            pipe = _load()
            gen = torch.Generator("cuda").manual_seed(int(seed)) if isinstance(seed, int) else None
            image = pipe(prompt=prompt, negative_prompt=str(body.get("negative") or DEFAULT_NEGATIVE)[:500],
                         num_inference_steps=steps, guidance_scale=6.5, width=width, height=height, generator=gen).images[0]
        except Exception as exc:  # noqa: BLE001 — the reporter shows the reason in the chat
            return jsonify(error=f"generation failed: {exc}"[:300]), 500
        finally:
            _last_used = time.time()
        out = io.BytesIO()
        image.save(out, format="PNG", optimize=True)
        print(f"[image-studio] {width}x{height} {steps} steps in {time.time() - started:.1f}s", flush=True)
    return Response(out.getvalue(), mimetype="image/png")


threading.Thread(target=_unload_when_idle, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002, threaded=True)
