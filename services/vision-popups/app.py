"""vision-popups: the Captain's vision and AR-development pop-ups on skippy-pc's GPU (tools.py says what each does).

    GET  /health        {"ready", "loaded", "models_loaded", "tools": {name: {"ready", "models", "default"}}}: ready once a
                        tool's weights are in the archive; nothing is loaded until asked
    GET  /tools         every tool and model, with licences and what each needs on the card
    POST /run/<tool>    {"image": base64, ...options} -> {"tool", "model", "text", "outputs": [{"name", "mime", "b64"}],
                        "data", "seconds", "peak_mb"}. 400 = the request is wrong; 503 {"busy": true} = the card is full
                        right now (a patient job tries again later)
    POST /popup/<tool>  the pop-up plumbing's call (docs/popups.md): {"args", "input": {"type", "name", "b64"}} -> one result's
                        bytes under its type (popups.json registers one entry per tool)
    POST /unload        give the card back now

Models load on demand and unload after IDLE_UNLOAD_S idle seconds or when another tool needs the card, like
services/image-studio. One tool at a time. No port is published: only the home reporter (same Docker network) calls it."""
from __future__ import annotations

import os
import threading
import time
import traceback

from flask import Flask, Response, jsonify, request

import imagekit as ik
import models as m
from tools import TOOLS

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = ik.MAX_INPUT_BYTES * 4 // 3 + 64_000


def tool_ready(name: str) -> bool:
    return any(m.seeded(x) for x in TOOLS[name]["models"])


@app.get("/health")
def health():
    tools = {n: {"ready": tool_ready(n), "models": list(t["models"]), "default": t["default"]} for n, t in TOOLS.items()}
    return jsonify(ready=any(t["ready"] for t in tools.values()), loaded=bool(m.loaded()), models_loaded=m.loaded(), tools=tools)


@app.get("/tools")
def tools():
    return jsonify(tools={n: {"summary": t["summary"], "default": t["default"], "models": list(t["models"])} for n, t in TOOLS.items()},
                   models={x.name: {"licence": x.licence, "need_mb": x.need_mb, "folder": x.folder, "ready": m.seeded(x.name)}
                           for x in m.MODELS.values()})


@app.post("/unload")
def unload():
    for n in m.loaded():
        m.unload(n)
    return jsonify(ok=True)


@app.post("/run/<name>")
def run(name: str):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="send JSON: {\"image\": base64, ...}"), 400
    result, status = _run(name, body.get("image") or "", body)
    if status != 200:
        return jsonify(result), status
    return jsonify({**result, "outputs": [ik.as_json(o) for o in result["outputs"]]})


@app.post("/popup/<name>")
def popup(name: str):
    """The pop-up plumbing's call (docs/popups.md): {"args": {...}, "input": {"type", "name", "b64"}} -> ONE result's bytes under
    its own type: the picture args.output names (else the tool's first), text/plain for ocr, or application/json (the text and
    the data) when args.output is "json"."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("args", {}), dict):
        return jsonify(error="that wasn't a pop-up request"), 400
    args = dict(body.get("args") or {})
    given = body.get("input") if isinstance(body.get("input"), dict) else {}
    result, status = _run(name, given.get("b64") or "", args)
    if status != 200:
        return jsonify(result), status
    want = str(args.get("output") or "")
    if want == "json" or (not result["outputs"] and name != "ocr"):
        return jsonify(text=result["text"], data=result["data"], model=result["model"], seconds=result["seconds"])
    if not result["outputs"]:
        return Response(result["text"].encode("utf-8"), mimetype="text/plain", content_type="text/plain; charset=utf-8")
    chosen = next((o for o in result["outputs"] if o["name"] == want), None) if want else result["outputs"][0]
    if chosen is None:
        return jsonify(error=f"{name} gives {', '.join(o['name'] for o in result['outputs'])} or json, not {want}"), 400
    return Response(chosen["bytes"], mimetype=chosen["mime"])


def _run(name: str, image, params: dict) -> tuple[dict, int]:
    if name not in TOOLS:
        return {"error": f"no tool called {name}; there are {', '.join(TOOLS)}"}, 404
    import torch
    started = time.time()
    try:
        body = params
        rgb, alpha = ik.decode(image)
        with m.lock:
            torch.cuda.reset_peak_memory_stats()
            result = TOOLS[name]["run"](rgb, alpha, body)
            peak = int(torch.cuda.max_memory_reserved() // 2**20)
            m.touch()
    except ik.BadInput as exc:
        return {"error": str(exc)}, 400
    except m.CardBusy as exc:
        return {"error": str(exc), "busy": True}, 503
    except FileNotFoundError as exc:
        return {"error": str(exc)}, 503
    except torch.cuda.OutOfMemoryError:
        for n in m.loaded():
            m.unload(n)
        return {"error": "the card ran out of memory; try again when it's quieter", "busy": True}, 503
    except Exception as exc:  # noqa: BLE001 — the reporter shows the reason in the chat
        traceback.print_exc()
        return {"error": f"{name} failed: {exc}"[:300]}, 500
    seconds = round(time.time() - started, 2)
    print(f"[vision-popups] {name} {rgb.width}x{rgb.height} in {seconds}s, peak {peak} MB", flush=True)
    model = str(body.get("model") or TOOLS[name]["default"])
    return {"tool": name, "model": model, "licence": m.MODELS[model].licence, "text": result["text"], "outputs": result["outputs"],
            "data": result.get("data") or {}, "seconds": seconds, "peak_mb": peak}, 200


threading.Thread(target=m.unload_when_idle, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8004")), threaded=True)
