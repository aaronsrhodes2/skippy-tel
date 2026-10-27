"""image-studio: the portal chat's QUICK pictures (make_image), rendered on skippy-pc's GPU (RTX 4070 Ti SUPER, 16 GB). The
quality tier (make_image_hq) is Qwen-Image on skippy-core (core/image-studio).

    GET  /health     {"ready": true, "loaded": bool, "model": ...}: ready once the weights are there (they load on demand)
    POST /generate   {"prompt", "negative"?, "steps"?, "width"?, "height"?, "seed"?} -> image/png bytes

MODEL picks the pipeline; the weights are in a Docker volume at /weights (seed_weights.py copies them out of the nightly
archive in D:/models, which Docker reads at ~8 MB/s):
    flux2-klein-4b   black-forest-labs/FLUX.2-klein-4B (4B, 4 steps, Apache 2.0): the default
    z-image-turbo    Tongyi-MAI/Z-Image-Turbo (6B, 8 steps, Apache 2.0): ~12 GB at its peak even like this, more than this
                     card has free beside Windows' desktop (~3.7 GB) and Whisper, so it is here for a bigger card only
Both are distilled: no classifier-free guidance, so "negative" is accepted and ignored.

The card is shared and Docker's VM has 8 GB of RAM, so the weights go straight onto the GPU, one part at a time, and
never all at full size at once (overcommitting the card spills into the VM's memory and has hung it): the transformer
loads as bf16 and is then stored as float8 (computed in bf16, diffusers' layerwise casting); the text encoder (Qwen3-4B)
runs as 4-bit NF4 (bitsandbytes: 2.5 GB instead of 8, the same pictures as 8 bits, and faster). `python app.py prepare`
(seed_weights.py runs it) saves that 4-bit copy as /weights/text_encoder-nf4 once, so a cold start reads 2.5 GB instead
of quantising 8 GB (without it the studio quantises on every load). The pipeline loads on the first request and unloads after
IDLE_UNLOAD_S idle seconds, so the voices, the pop-ups and the music studio get the card back. One picture at a time. No port is
published: only the home reporter (same Docker network) calls it.
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time

from flask import Flask, Response, jsonify, request

MODEL = os.environ.get("MODEL", "flux2-klein-4b")
WEIGHTS = os.environ.get("WEIGHTS", "/weights")
PREPARED = os.path.join(WEIGHTS, "text_encoder-nf4")
IDLE_UNLOAD_S = int(os.environ.get("IDLE_UNLOAD_S", "600"))
MAX_SIDE, MIN_SIDE = 1344, 512
MODELS = {   # pipeline class, default steps, guidance (both are guidance-distilled)
    "flux2-klein-4b": ("Flux2KleinPipeline", 4, 1.0),
    "z-image-turbo": ("ZImagePipeline", 9, 0.0),     # 9 scheduler steps = 8 transformer passes (the model card's setting)
}

app = Flask(__name__)
_lock = threading.Lock()
_pipe = None
_last_used = 0.0


def _text_encoder(cls):
    """The 4-bit text encoder: the prepared copy if there is one, else quantised from the bf16 weights as it loads."""
    import torch
    import transformers
    if os.path.isfile(os.path.join(PREPARED, "config.json")):
        return cls.from_pretrained(PREPARED, dtype=torch.bfloat16, device_map="cuda")
    return cls.from_pretrained(WEIGHTS, subfolder="text_encoder", dtype=torch.bfloat16, device_map="cuda",
                               quantization_config=transformers.BitsAndBytesConfig(
                                   load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16))


def _load():
    global _pipe
    if _pipe is None:
        import diffusers
        import torch
        import transformers
        bf16 = torch.bfloat16
        with open(os.path.join(WEIGHTS, "model_index.json"), encoding="utf-8") as f:
            index = json.load(f)

        def part(name):
            lib, kind = index[name]
            return getattr(diffusers if lib == "diffusers" else transformers, kind)

        t0 = time.time()
        transformer = part("transformer").from_pretrained(WEIGHTS, subfolder="transformer", torch_dtype=bf16,
                                                          device_map="cuda")
        transformer.enable_layerwise_casting(storage_dtype=torch.float8_e4m3fn, compute_dtype=bf16)
        torch.cuda.empty_cache()
        t1 = time.time()
        text_encoder = _text_encoder(part("text_encoder"))
        vae = part("vae").from_pretrained(WEIGHTS, subfolder="vae", torch_dtype=bf16, device_map="cuda")
        vae.enable_tiling()   # a 1024 px decode in one piece wants ~3 GB more; in tiles it needs a few hundred MB
        pipe = getattr(diffusers, MODELS[MODEL][0]).from_pretrained(
            WEIGHTS, transformer=transformer, text_encoder=text_encoder, vae=vae, torch_dtype=bf16)
        pipe.set_progress_bar_config(disable=True)
        torch.cuda.empty_cache()   # hand back what the bf16 copies and the quantiser borrowed while loading
        print(f"[image-studio] loaded {MODEL}: transformer {t1 - t0:.0f}s, the rest {time.time() - t1:.0f}s", flush=True)
        _pipe = pipe
    return _pipe


def _unload_when_idle():
    global _pipe
    while True:
        time.sleep(30)
        with _lock:
            if _pipe is not None and time.time() - _last_used > IDLE_UNLOAD_S:
                import gc
                import torch
                _pipe = None
                gc.collect()
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
    vram = {}
    if _pipe is not None:
        import torch
        vram = {"vram_gib": {"allocated": round(torch.cuda.memory_allocated() / 2**30, 2),
                             "reserved": round(torch.cuda.memory_reserved() / 2**30, 2)}}
    return jsonify(ready=os.path.isfile(os.path.join(WEIGHTS, "model_index.json")), loaded=_pipe is not None,
                   model=MODEL, **vram)


@app.post("/generate")
def generate():
    global _last_used
    body = request.get_json(silent=True) or {}
    prompt = str(body.get("prompt") or "").strip()[:900]
    if not prompt:
        return jsonify(error="a prompt is needed"), 400
    import torch
    _, default_steps, guidance = MODELS[MODEL]
    try:
        steps = max(2, min(50, int(body.get("steps") or default_steps)))
    except (TypeError, ValueError):
        steps = default_steps
    width, height = _side(body.get("width"), 1024), _side(body.get("height"), 1024)
    seed = body.get("seed")
    with _lock:
        started = time.time()
        try:
            pipe = _load()
            loaded = time.time()
            torch.cuda.reset_peak_memory_stats()
            gen = torch.Generator("cuda").manual_seed(int(seed)) if isinstance(seed, int) else None
            image = pipe(prompt=prompt, num_inference_steps=steps, guidance_scale=guidance,
                         width=width, height=height, generator=gen).images[0]
        except Exception as exc:  # noqa: BLE001 — the reporter shows the reason in the chat
            return jsonify(error=f"generation failed: {exc}"[:300]), 500
        finally:
            _last_used = time.time()
        done = time.time()
        peak = torch.cuda.max_memory_allocated() / 2**30
        torch.cuda.empty_cache()   # give back the render's scratch space: the card is shared with the voices and Whisper
        out = io.BytesIO()
        image.save(out, format="PNG", optimize=True)
        print(f"[image-studio] {MODEL} {width}x{height} {steps} steps in {done - loaded:.1f}s"
              f" (load {loaded - started:.1f}s, peak {peak:.1f} GiB)", flush=True)
    return Response(out.getvalue(), mimetype="image/png",
                    headers={"X-Render-Seconds": f"{done - loaded:.2f}", "X-Load-Seconds": f"{loaded - started:.2f}",
                             "X-Peak-VRAM-GiB": f"{peak:.2f}"})


threading.Thread(target=_unload_when_idle, daemon=True).start()

if __name__ == "__main__":
    if sys.argv[1:] == ["prepare"]:   # once per seeded volume, mounted read-write
        import transformers
        with open(os.path.join(WEIGHTS, "model_index.json"), encoding="utf-8") as f:
            _, kind = json.load(f)["text_encoder"]
        if not os.path.isfile(os.path.join(PREPARED, "config.json")):
            _text_encoder(getattr(transformers, kind)).save_pretrained(PREPARED)
        print(f"[image-studio] prepared {PREPARED}", flush=True)
    else:
        app.run(host="0.0.0.0", port=8002, threaded=True)
