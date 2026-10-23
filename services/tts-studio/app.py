"""tts-studio: Skippy chat's local voices on skippy-pc's GPU, for voice-gate (the browser's way in: Funnel :10000 -> voice-gate -> here).

    GET  /health    {"ready", "voices": {"stock": bool, "skippy": bool}, "loaded": [...]}   ready = the weights (and the reference) are there
    POST /speak     {"text", "voice": "stock"|"skippy"} -> audio/mpeg, streamed a sentence at a time (MP3 frames, so the
                    browser can start on the first sentence while the rest renders)

Two voices, and which one a person gets is voice-gate's call (the portal's pass), never this service's:
  stock   Kokoro-82M (Apache-2.0), a stock voice (STOCK_VOICE, default bm_george). Everyone who may chat.
  skippy  F5-TTS v1 Base, zero-shot from the Captain's reference cut (/ref/ref.wav + ref.txt; the voice bake-off's pick,
          docs/voice-bakeoff.md). CC BY-NC and a clone of a real narrator: personal use, the Captain only.

The weights are D:/models' own copies, mounted read-only; the container sits on an internal network (no route out, no published
port), HF_HUB_OFFLINE. Each model loads on its first line and unloads after IDLE_UNLOAD_S idle seconds, so Ollama gets the card back.
One line at a time (a lock): the GPU is shared with everything else at home.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time

from flask import Flask, Response, jsonify, request

KOKORO = os.environ.get("KOKORO_DIR", "/w/kokoro")
F5_DIR = os.environ.get("F5_DIR", "/w/f5/F5TTS_v1_Base")
VOCOS = os.environ.get("VOCOS_DIR", "/vocos")
REF = os.environ.get("REF_DIR", "/ref")
STOCK_VOICE = os.environ.get("STOCK_VOICE", "bm_george")
IDLE_UNLOAD_S = int(os.environ.get("IDLE_UNLOAD_S", "600"))
F5_NFE = int(os.environ.get("F5_NFE", "32"))
MAX_TEXT = 1000
SR = 24000

app = Flask(__name__)
_lock = threading.Lock()
_models: dict = {}
_last_used = 0.0

ONES = "zero one two three four five six seven eight nine".split()


def say_numbers(text: str) -> str:
    """Numbers as words, for F5 (it reads digits badly: most of its word errors in the bake-off were numbers). Kokoro's G2P does its own."""
    from num2words import num2words

    def decimal(m):
        whole, frac = m.group(1).replace(",", ""), m.group(2)
        return f"{num2words(int(whole))} point {' '.join(ONES[int(d)] for d in frac)}"

    t = re.sub(r"(\d+)\s*%", r"\1 percent", text)
    t = re.sub(r"(\d[\d,]*)\.(\d+)", decimal, t)
    t = re.sub(r"\d{1,3}(?:,\d{3})+|\d+", lambda m: num2words(int(m.group(0).replace(",", ""))), t)
    return t


def chunks(text: str, rest_max: int = 260) -> list[str]:
    """Sentences: the first one alone (time to first audio; merged with the next while it is a few words), the rest packed up to rest_max."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?;])\s+", re.sub(r"\s+", " ", text).strip()) if s.strip()]
    out: list[str] = []
    for s in sentences:
        if out and (len(out) == 1 and len(out[0]) < 30 or len(out) > 1 and len(out[-1]) + 1 + len(s) <= rest_max):
            out[-1] += " " + s
        else:
            out.append(s)
    return out


def ready() -> dict:
    return {"stock": os.path.isfile(f"{KOKORO}/kokoro-v1_0.pth") and os.path.isfile(f"{KOKORO}/voices/{STOCK_VOICE}.pt"),
            "skippy": all(os.path.isfile(p) for p in (f"{F5_DIR}/model_1250000.safetensors", f"{F5_DIR}/vocab.txt",
                                                       f"{REF}/ref.wav", f"{REF}/ref.txt", f"{VOCOS}/config.yaml"))}


class Stock:
    def __init__(self):
        from kokoro import KModel, KPipeline
        model = KModel(repo_id="hexgrad/Kokoro-82M", config=f"{KOKORO}/config.json", model=f"{KOKORO}/kokoro-v1_0.pth").to("cuda").eval()
        self.pipe = KPipeline(lang_code=STOCK_VOICE[0], repo_id="hexgrad/Kokoro-82M", model=model)
        self.voice = f"{KOKORO}/voices/{STOCK_VOICE}.pt"

    def say(self, text: str):
        import numpy as np
        parts = [r.audio.cpu().numpy() for r in self.pipe(text, voice=self.voice, speed=1.0) if r.audio is not None]
        return np.concatenate(parts).astype("float32") if parts else np.zeros(0, "float32")


class Skippy:
    def __init__(self):
        from f5_tts.api import F5TTS
        self.m = F5TTS(model="F5TTS_v1_Base", ckpt_file=f"{F5_DIR}/model_1250000.safetensors", vocab_file=f"{F5_DIR}/vocab.txt",
                       vocoder_local_path=VOCOS, device="cuda")
        self.ref_text = open(f"{REF}/ref.txt", encoding="utf-8").read().strip()

    def say(self, text: str):
        import numpy as np
        wav, sr, _ = self.m.infer(ref_file=f"{REF}/ref.wav", ref_text=self.ref_text, gen_text=say_numbers(text), nfe_step=F5_NFE,
                                  remove_silence=False, show_info=lambda *a: None, progress=None)
        assert sr == SR, sr
        return np.asarray(wav, dtype="float32")


VOICES = {"stock": Stock, "skippy": Skippy}


def _model(voice: str):
    if voice not in _models:
        started = time.time()
        _models[voice] = VOICES[voice]()
        print(f"[tts-studio] {voice} loaded in {time.time() - started:.1f}s", flush=True)
    return _models[voice]


def mp3(wav) -> bytes:
    """Raw MP3 frames (no ID3 tag, no Xing header), so the chunks of one line concatenate into one playable stream."""
    import numpy as np
    peak = float(np.abs(wav).max()) if len(wav) else 0.0
    if peak > 1.0:
        wav = wav / peak
    p = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0", "-f", "mp3", "-b:a", "96k",
                        "-id3v2_version", "0", "-write_xing", "0", "pipe:1"], input=wav.astype("<f4").tobytes(), capture_output=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace")[-300:])
    return p.stdout


def _unload_when_idle():
    while True:
        time.sleep(30)
        with _lock:
            if _models and time.time() - _last_used > IDLE_UNLOAD_S:
                import torch
                _models.clear()
                torch.cuda.empty_cache()
                print("[tts-studio] unloaded after idle", flush=True)


@app.get("/health")
def health():
    r = ready()
    return jsonify(ready=any(r.values()), voices=r, loaded=sorted(_models), stock_voice=STOCK_VOICE)


@app.post("/speak")
def speak():
    body = request.get_json(silent=True) or {}
    text, voice = str(body.get("text") or "").strip(), body.get("voice")
    if voice not in VOICES:
        return jsonify(error="voice must be stock or skippy"), 400
    if not text:
        return jsonify(error="no text"), 400
    if len(text) > MAX_TEXT:
        return jsonify(error="too long"), 413
    if not ready()[voice]:
        return jsonify(error=f"the {voice} voice's files are missing"), 503
    if not _lock.acquire(timeout=60):
        return jsonify(error="busy"), 429
    try:
        model = _model(voice)                       # load before the response starts, so a failed load is a 503, not a cut stream
    except Exception as exc:  # noqa: BLE001
        _lock.release()
        return jsonify(error=f"{voice} failed to load: {exc}"[:300]), 503

    def stream():
        global _last_used
        started, audio_s = time.time(), 0.0
        for i, part in enumerate(chunks(text)):
            wav = model.say(part)
            audio_s += len(wav) / SR
            if i == 0:
                print(f"[tts-studio] {voice}: first audio after {time.time() - started:.2f}s", flush=True)
            yield mp3(wav)
            _last_used = time.time()
        took = time.time() - started
        print(f"[tts-studio] {voice}: {len(text)} chars, {audio_s:.1f}s of audio in {took:.2f}s (RTF {took / max(audio_s, 0.01):.2f})", flush=True)

    def done():
        global _last_used
        _last_used = time.time()
        _lock.release()

    resp = Response(stream(), mimetype="audio/mpeg", headers={"Cache-Control": "no-store"})
    resp.call_on_close(done)                        # runs when the stream ends, fails or the listener hangs up: the lock always comes back
    return resp


threading.Thread(target=_unload_when_idle, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8003, threaded=True)
