"""tts-studio: Skippy chat's local voices on skippy-pc's GPU, for voice-gate (the browser's way in: Funnel :10000 -> voice-gate -> here).

    GET  /health    {"ready", "voices": {"stock": bool, "skippy": bool}, "loaded": [...], "vram_reserved_gib"}   a voice is true once
                    its files are there; a RESIDENT voice only once it is loaded and warm (so voice-gate falls back while it loads)
    POST /speak     {"text", "voice": "stock"|"skippy"} -> audio/mpeg, streamed a sentence at a time (MP3 frames, so the
                    browser can start on the first sentence while the rest renders)

Two voices, and which one a person gets is voice-gate's call (the portal's pass), never this service's:
  stock   Kokoro-82M (Apache-2.0), a stock voice (STOCK_VOICE, default bm_george). Everyone who may chat.
  skippy  F5-TTS v1 Base at 16 flow steps, zero-shot from /ref/ref.wav + ref.txt: the 9 s print of the Captain's ElevenLabs Skippy
          voice he chose (docs/voice-bakeoff.md: about 0.6 s a line, RTF 0.10, 0.93 like ElevenLabs). CC BY-NC: personal use, the
          Captain only. Numbers are spelled out first (spoken_numbers.py): most of F5's word errors were numbers.

The weights are D:/models' own copies, mounted read-only; the container sits on an internal network (no route out, no published
port), HF_HUB_OFFLINE. RESIDENT voices (default: both) load once at startup, warm up and stay; then the process tops its CUDA memory
up to VOICE_VRAM_GB and keeps it (torch's caching allocator never hands a cached block back), so a picture or song job can't take the
voice's room: the Captain's rule, voice keeps reserved GPU on skippy-pc. A voice not in RESIDENT loads on its first line and unloads
after IDLE_UNLOAD_S idle seconds (0 = never). One line at a time (a lock): it is one GPU.
"""
from __future__ import annotations

import os
import re
import threading
import time

from flask import Flask, Response, jsonify, request

from spoken_numbers import say_numbers

KOKORO = os.environ.get("KOKORO_DIR", "/w/kokoro")
F5_DIR = os.environ.get("F5_DIR", "/w/f5/F5TTS_v1_Base")
VOCOS = os.environ.get("VOCOS_DIR", "/vocos")
REF = os.environ.get("REF_DIR", "/ref")
STOCK_VOICE = os.environ.get("STOCK_VOICE", "bm_george")
IDLE_UNLOAD_S = int(os.environ.get("IDLE_UNLOAD_S", "600"))
F5_NFE = int(os.environ.get("F5_NFE", "16"))
RESIDENT = [v for v in os.environ.get("RESIDENT", "skippy,stock").replace(" ", "").split(",") if v]
VRAM_GB = float(os.environ.get("VOICE_VRAM_GB", "3"))
LOCK_WAIT_S = float(os.environ.get("LOCK_WAIT_S", "10"))
NEED_GB = {"stock": 1.0, "skippy": 2.5}                # what a voice takes on the card at its peak (measured: Kokoro < 1, F5 2.0)
MARGIN_GB = float(os.environ.get("VRAM_MARGIN_GB", "1.5"))   # free card left over after any load or reserve
RETRY_S = float(os.environ.get("RESIDENT_RETRY_S", "120"))
MAX_TEXT = 1000
SR = 24000

app = Flask(__name__)
_lock = threading.Lock()
_models: dict = {}
_last_used = 0.0
_warm: set = set()                                     # resident voices loaded and warm
_status = {"vram_reserved_gib": None, "errors": {}}


def clean(text: str) -> str:
    """What either voice is handed: no ElevenLabs audio tags ([whispers]: a local voice would read them out), no markdown marks."""
    return re.sub(r"\s+", " ", re.sub(r"[`*_#>~|]+", "", re.sub(r"\[[^\]]{1,40}\]", " ", text))).strip()


def for_f5(text: str) -> str:
    """F5 reads characters, not meaning: numbers in words (spoken_numbers.py), no hyphens (it paused at one: "forty-four" came out
    as "forty, four"), and a closing stop (it trails off without one)."""
    t = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", say_numbers(text))
    return t + "." if t and t[-1] not in ".!?…;:" else t


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


def files() -> dict:
    return {"stock": os.path.isfile(f"{KOKORO}/kokoro-v1_0.pth") and os.path.isfile(f"{KOKORO}/voices/{STOCK_VOICE}.pt"),
            "skippy": all(os.path.isfile(p) for p in (f"{F5_DIR}/model_1250000.safetensors", f"{F5_DIR}/vocab.txt",
                                                       f"{REF}/ref.wav", f"{REF}/ref.txt", f"{VOCOS}/config.yaml"))}


def ready() -> dict:
    """A voice that can speak now: its files are there and, if it is resident, it is loaded and warm (a cold F5 load takes over a
    minute, which voice-gate would rather spend on ElevenLabs)."""
    return {v: ok and (v not in RESIDENT or v in _warm) for v, ok in files().items()}


class Stock:
    """Kokoro reads plain numbers itself, but not units ("km/s" came out as "km slash S"), so it gets the numbers pass too."""

    def __init__(self):
        from kokoro import KModel, KPipeline
        model = KModel(repo_id="hexgrad/Kokoro-82M", config=f"{KOKORO}/config.json", model=f"{KOKORO}/kokoro-v1_0.pth").to("cuda").eval()
        self.pipe = KPipeline(lang_code=STOCK_VOICE[0], repo_id="hexgrad/Kokoro-82M", model=model)
        self.voice = f"{KOKORO}/voices/{STOCK_VOICE}.pt"

    def say(self, text: str):
        import numpy as np
        parts = [r.audio.cpu().numpy() for r in self.pipe(say_numbers(text), voice=self.voice, speed=1.0) if r.audio is not None]
        return np.concatenate(parts).astype("float32") if parts else np.zeros(0, "float32")


class Skippy:
    def __init__(self):
        from f5_tts.api import F5TTS
        self.m = F5TTS(model="F5TTS_v1_Base", ckpt_file=f"{F5_DIR}/model_1250000.safetensors", vocab_file=f"{F5_DIR}/vocab.txt",
                       vocoder_local_path=VOCOS, device="cuda")
        self.ref_text = open(f"{REF}/ref.txt", encoding="utf-8").read().strip()

    def say(self, text: str):
        import numpy as np
        wav, sr, _ = self.m.infer(ref_file=f"{REF}/ref.wav", ref_text=self.ref_text, gen_text=for_f5(text), nfe_step=F5_NFE,
                                  remove_silence=False, show_info=lambda *a: None)
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
    """MP3 frames (libsndfile's LAME, in-process: spawning ffmpeg per chunk stalled for seconds whenever the disk was busy), with no
    ID3 tag, so the chunks of one line concatenate into one playable stream. Constant ~96 kb/s."""
    import io

    import numpy as np
    import soundfile as sf
    peak = float(np.abs(wav).max()) if len(wav) else 0.0
    if peak > 1.0:
        wav = wav / peak
    out = io.BytesIO()
    sf.write(out, wav, SR, format="MP3", subtype="MPEG_LAYER_III", bitrate_mode="CONSTANT", compression_level=0.4)
    return out.getvalue()


def _unload_when_idle():
    while IDLE_UNLOAD_S > 0:
        time.sleep(30)
        with _lock:
            idle = [v for v in _models if v not in RESIDENT]
            if idle and time.time() - _last_used > IDLE_UNLOAD_S:
                for v in idle:
                    del _models[v]
                print(f"[tts-studio] unloaded {idle} after idle", flush=True)       # no empty_cache: the reserve stays held


WARMUP = "Fine. I'll warm up, since apparently that is my job now."


def free_gib() -> float:
    """The card's free memory right now, all processes counted: nvidia-smi's figure (the container has it, and it sees the whole card).
    Not torch's mem_get_info: under WSL it told this container 14.7 GB free when the card had 6.0 GB (2026-10-24), which would let a
    load overfill the card. torch only when nvidia-smi can't be asked."""
    import subprocess
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=10).stdout.split()
        return float(out[0]) / 1024
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        import torch
        return torch.cuda.mem_get_info()[0] / 2**30


def room_for(voice: str) -> bool:
    """Loading only when the card has the room, with MARGIN_GB to spare: when the card overflows, WSL spills GPU memory into Docker's
    8 GB VM and the whole VM freezes (three Docker Desktop crashes on 2026-10-23, each while a model loaded onto a full card)."""
    free = free_gib()
    if free >= NEED_GB.get(voice, 2.0) + MARGIN_GB:
        return True
    print(f"[tts-studio] not loading {voice}: {free:.1f} GiB free, it needs {NEED_GB.get(voice, 2.0)} + {MARGIN_GB} to spare", flush=True)
    return False


def reserve(gb: float) -> float:
    """Top this process's CUDA memory up to gb and keep it cached: the voice's headroom, never past MARGIN_GB of free card.
    Returns what is held, in GiB."""
    import torch
    want = min(int(gb * 2**30) - torch.cuda.memory_reserved(), int((free_gib() - MARGIN_GB) * 2**30))
    if want > 0:
        try:
            block = torch.empty(want, dtype=torch.uint8, device="cuda")
            del block                                   # back to torch's cache, not to the driver
        except RuntimeError as exc:                     # the card is already full: run with what there is
            print(f"[tts-studio] could not reserve {want / 2**30:.1f} GB of VRAM: {type(exc).__name__}", flush=True)
    return round(torch.cuda.memory_reserved() / 2**30, 2)


def preload() -> bool:
    """Load and warm each resident voice not yet warm, in RESIDENT order, each only if the card has room; then hold the VRAM reserve.
    True when every resident voice is warm. In the background: /health says not ready meanwhile, and voice-gate falls back."""
    for v in RESIDENT:
        if v in _warm or v not in VOICES or not files().get(v):
            continue
        with _lock:
            try:
                if v not in _models and not room_for(v):
                    _status["errors"][v] = "not enough free VRAM yet; retrying"
                    continue
                started = time.time()
                _model(v).say(WARMUP)
                _warm.add(v)
                _status["errors"].pop(v, None)
                print(f"[tts-studio] {v} resident and warm in {time.time() - started:.1f}s", flush=True)
            except Exception as exc:  # noqa: BLE001  voice-gate falls back; the log and /health say why
                _status["errors"][v] = f"{type(exc).__name__}: {exc}"[:300]
                print(f"[tts-studio] {v} failed to preload: {_status['errors'][v]}", flush=True)
    if _warm and VRAM_GB > 0:
        with _lock:
            _status["vram_reserved_gib"] = reserve(VRAM_GB)
        print(f"[tts-studio] holding {_status['vram_reserved_gib']} GiB of VRAM", flush=True)
    return all(v in _warm for v in RESIDENT if v in VOICES and files().get(v))


def keep_resident():
    """preload() until every resident voice is warm: a voice skipped for want of room is tried again when the card frees up."""
    while not preload():
        time.sleep(RETRY_S)


@app.get("/health")
def health():
    r = ready()
    return jsonify(ready=any(r.values()), voices=r, loaded=sorted(_models), resident=RESIDENT, stock_voice=STOCK_VOICE, f5_nfe=F5_NFE,
                   vram_reserved_gib=_status["vram_reserved_gib"], errors=_status["errors"])


@app.post("/speak")
def speak():
    body = request.get_json(silent=True) or {}
    text, voice = clean(str(body.get("text") or "")), body.get("voice")
    if voice not in VOICES:
        return jsonify(error="voice must be stock or skippy"), 400
    if not text:
        return jsonify(error="no text"), 400
    if len(text) > MAX_TEXT:
        return jsonify(error="too long"), 413
    if not ready()[voice]:
        return jsonify(error=f"the {voice} voice isn't ready (files missing, or still loading)"), 503
    if not _lock.acquire(timeout=LOCK_WAIT_S):           # busy past that: voice-gate would rather fall back than keep him waiting
        return jsonify(error="busy"), 429
    try:
        if voice not in _models and not room_for(voice):
            _lock.release()
            return jsonify(error=f"no room on the card for the {voice} voice right now"), 503
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


if __name__ == "__main__":
    threading.Thread(target=_unload_when_idle, daemon=True).start()
    threading.Thread(target=keep_resident, daemon=True, name="preload").start()
    app.run(host="0.0.0.0", port=8003, threaded=True)
