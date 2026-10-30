"""audio-popups: the Captain's production helpers on skippy-pc's GPU, one endpoint per model, each loaded only when asked.

    GET  /health               {"ready", "loaded", "models": {name: {"ready", "loaded", "gpu"}}}
    POST /sfx_quick            {"prompt", "seconds"?, "seed"?}                         -> audio/wav   Stable Audio Open Small
    POST /sfx_loop             {"prompt", "seconds"? | "bpm"+"bars"?, "key"?, "seed"?, "steps"?, "loop"?}
                                                                                       -> audio/wav   Stable Audio Open 1.0
    POST /melody_variations    multipart: audio + prompt [+ seconds, seed]             -> audio/wav   MusicGen stereo-melody-large
    POST /stems                multipart: audio [+ stem, format (flac|wav|mp3), quality (ft|fast)]
                                                                                       -> application/zip, or one stem   Demucs v4
    POST /audio_to_midi        multipart: audio [+ bpm, onset, frame, min_note_ms]     -> audio/midi  Basic Pitch (CPU)

The weights are D:/models' own copies, seeded once into a Docker volume (seed_weights.sh: Docker reads D: at ~8 MB/s, so a
mount straight from D: would take minutes per load) and mounted read-only. Everything runs offline. Nothing is kept warm (the
Captain, 2026-10-29: music takes time, quality over speed): a model loads for its job, and once the reply has gone the process ends,
so Docker's restart policy brings back a bare Flask and the card and the VM get everything back. The settings are the quality ones
(the large MusicGen, two Demucs passes, 150 diffusion steps), not the fast ones. One job at a time. Every reply carries X-Seconds,
X-Peak-VRAM-MB and X-Seed (when it used one).
Full songs are NOT here: they stay with ACE-Step's music-studio (make_music).
"""
from __future__ import annotations

import io
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import wave
import zipfile

from flask import Flask, Response, jsonify, request

WEIGHTS = os.environ.get("AUDIO_POPUPS_WEIGHTS", "/weights")
IDLE_UNLOAD_S = int(os.environ.get("IDLE_UNLOAD_S", "300"))      # the fallback only: normally the process ends after each job
EXIT_AFTER_JOB = os.environ.get("EXIT_AFTER_JOB", "1") == "1"
DEMUCS_SHIFTS = int(os.environ.get("DEMUCS_SHIFTS", "2"))         # passes at shifted offsets, averaged: a little cleaner, twice the time
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "96")) * 1024 * 1024   # a 64 MB track base64'd in the contract's JSON is ~86 MB
DEVICE = os.environ.get("AUDIO_POPUPS_DEVICE", "cuda")

# Where each model's files live inside WEIGHTS (the D:/models folder names, so a look at the archive says what is used).
PATHS = {
    "sfx_quick": "stabilityai__stable-audio-open-small",
    "sfx_loop": "stabilityai__stable-audio-open-1.0",
    "t5": "google-t5__t5-base",
    "melody_variations": os.environ.get("MELODY_MODEL", "facebook__musicgen-stereo-melody-large"),   # 3.3 B; the 1.5 B is the fallback
    "stems": "facebookresearch__demucs",
    "audio_to_midi": "spotify__basic-pitch",
}
NEEDS = {                                   # the files a model can't load without: /health says "ready" only when all are there
    "sfx_quick": [f"{PATHS['sfx_quick']}/model.safetensors", f"{PATHS['sfx_quick']}/model_config.json",
                  f"{PATHS['t5']}/model.safetensors"],
    "sfx_loop": [f"{PATHS['sfx_loop']}/model.safetensors", f"{PATHS['sfx_loop']}/model_config.json",
                 f"{PATHS['t5']}/model.safetensors"],
    "melody_variations": [f"{PATHS['melody_variations']}/config.json"],
    "stems": [f"{PATHS['stems']}/htdemucs_ft.yaml", f"{PATHS['stems']}/htdemucs.yaml"],
    "audio_to_midi": [f"{PATHS['audio_to_midi']}/nmp.onnx"],
}
GPU_MODELS = ("sfx_quick", "sfx_loop", "melody_variations", "stems")

LIMITS = {
    "sfx_quick": (0.5, 11.0, 3.0),          # (min, max, default) seconds: SAO Small makes up to 11 s
    "sfx_loop": (1.0, 47.0, 8.0),           # SAO 1.0 makes up to 47 s
    "melody_variations": (4.0, 30.0, 12.0), # MusicGen's window is 30 s
}
MELODY_REF_MAX_S = 30.0                     # how much of the attached clip MusicGen hears (its chroma window)
STEMS_MAX_S = 12 * 60                       # a whole track, generously
MIDI_MAX_S = 10 * 60

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD
_lock = threading.Lock()
_loaded: dict[str, object] = {}
_last_used = 0.0


# ── plain helpers (no torch: the tests run these without a GPU) ─────────────────────────────────────────────────────────

def clamp_seconds(model: str, value) -> float:
    lo, hi, default = LIMITS[model]
    try:
        v = float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        v = default
    if v != v:                                                   # NaN
        v = default
    return round(max(lo, min(hi, v)), 3)


def bar_seconds(bpm, bars, beats_per_bar: int = 4) -> float | None:
    """The exact length of `bars` bars at `bpm`, so a loop drops onto a DAW's grid; None when either is missing or silly."""
    try:
        bpm, bars = float(bpm), float(bars)
    except (TypeError, ValueError):
        return None
    if not (40 <= bpm <= 250) or not (0 < bars <= 64):
        return None
    return round(bars * beats_per_bar * 60.0 / bpm, 6)


KEY_RE = re.compile(r"^\s*([A-Ga-g])\s*([#b♯♭]?)\s*(m|min|minor|maj|major)?\s*$")


def tidy_key(key) -> str | None:
    """'a min', 'F#m', 'Bb major' -> 'A minor', 'F# minor', 'Bb major' (the way SAO's training tags spell keys); None if unreadable."""
    m = KEY_RE.match(str(key or ""))
    if not m:
        return None
    note = m.group(1).upper() + {"♯": "#", "♭": "b"}.get(m.group(2), m.group(2))
    return f"{note} {'minor' if (m.group(3) or '').startswith('m') and m.group(3) not in ('maj', 'major') else 'major'}"


def loop_prompt(prompt: str, bpm=None, key=None) -> str:
    """The prompt as SAO 1.0 was trained on Freesound tags: tempo and key said once, in its words, unless already there."""
    text = " ".join(str(prompt or "").split())[:400]
    try:
        tempo = int(round(float(bpm))) if bpm not in (None, "") else None
    except (TypeError, ValueError):
        tempo = None
    if tempo and not re.search(r"\b\d{2,3}\s*bpm\b", text, re.I):
        text += f", {tempo} BPM"
    k = tidy_key(key)
    if k and k.lower() not in text.lower():
        text += f", {k}"
    return text


def pick_seed(value) -> int:
    try:
        seed = int(value)
        if 0 <= seed < 2**31:
            return seed
    except (TypeError, ValueError):
        pass
    return random.randrange(2**31)


def pcm16_wav(samples, rate: int) -> bytes:
    """A float array (channels, frames) in -1..1 as 16-bit PCM WAV bytes."""
    import numpy as np
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim == 1:
        data = data[None, :]
    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).round().astype("<i2").T.copy()   # frames x channels, interleaved
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())
    return out.getvalue()


def finish(samples, rate: int, frames: int | None = None, loop: bool = False, peak_db: float = -1.0):
    """Cut to exactly `frames` (a loop must be sample-exact), peak-normalise to `peak_db`, and give a one-shot a 5 ms tail fade so it
    never ends on a click. A loop gets no fade: its seam is the model's, and a fade would make it pump."""
    import numpy as np
    data = np.asarray(samples, dtype=np.float32)
    if data.ndim == 1:
        data = data[None, :]
    if frames:
        data = data[:, :frames]
        if data.shape[1] < frames:
            data = np.pad(data, ((0, 0), (0, frames - data.shape[1])))
    peak = float(np.abs(data).max()) if data.size else 0.0
    if peak > 1e-6:
        data = data * (10 ** (peak_db / 20.0) / peak)
    if not loop:
        n = min(data.shape[1], max(1, int(rate * 0.005)))
        data[:, -n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
    return data


def stem_zip(stems: dict[str, bytes], ext: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:             # audio doesn't deflate; storing is instant
        for name, data in stems.items():
            z.writestr(f"{name}.{ext}", data)
    return out.getvalue()


class BadClip(ValueError):
    """The attached file wasn't audio ffmpeg could read: the person's to fix, so a 400."""


def decode(raw: bytes, rate: int, channels: int, max_s: float):
    """Any audio file (wav, flac, mp3, m4a, ogg, aiff...) -> float32 (channels, frames) at `rate`, through ffmpeg; at most `max_s`."""
    import numpy as np
    with tempfile.NamedTemporaryFile(suffix=".in") as f:
        f.write(raw)
        f.flush()
        run = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", f.name, "-t", str(max_s), "-f", "f32le",
                              "-ac", str(channels), "-ar", str(rate), "-"], capture_output=True, timeout=300)
    if run.returncode != 0 or not run.stdout:
        raise BadClip("couldn't read that audio: " + run.stderr.decode(errors="replace").strip()[:160])
    return np.frombuffer(run.stdout, dtype=np.float32).reshape(-1, channels).T.copy()


def encode(samples, rate: int, fmt: str) -> bytes:
    """float (channels, frames) -> wav (16-bit), flac (24-bit) or mp3 (320k) bytes."""
    if fmt == "wav":
        return pcm16_wav(samples, rate)
    import numpy as np
    data = np.asarray(samples, dtype=np.float32)
    codec = ["-c:a", "flac", "-sample_fmt", "s32"] if fmt == "flac" else ["-c:a", "libmp3lame", "-b:a", "320k"]
    run = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "f32le", "-ac", str(data.shape[0]), "-ar", str(rate),
                          "-i", "-", *codec, "-f", fmt, "-"], input=np.clip(data, -1, 1).T.astype("<f4").tobytes(),
                         capture_output=True, timeout=600)
    if run.returncode != 0:
        raise RuntimeError("couldn't encode: " + run.stderr.decode(errors="replace").strip()[:160])
    return run.stdout


def upload() -> bytes | None:
    """The attached clip: a multipart `audio` (or `file`) field, or a raw audio/* body."""
    for field in ("audio", "file"):
        if field in request.files:
            return request.files[field].read() or None
    if (request.mimetype or "").startswith("audio/") or request.mimetype == "application/octet-stream":
        return request.get_data() or None
    return None


def params() -> dict:
    """JSON body or form fields, the same either way."""
    if request.is_json:
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}
    return request.form.to_dict()


def path(*parts: str) -> str:
    return os.path.join(WEIGHTS, *parts)


def model_ready(name: str) -> bool:
    if name == "melody_variations":                              # its shards are named by the index, not by us
        folder = path(PATHS[name])
        return os.path.isfile(os.path.join(folder, "config.json")) and any(
            n.endswith(".safetensors") for n in (os.listdir(folder) if os.path.isdir(folder) else []))
    if name == "stems":
        folder = path(PATHS[name])
        return all(os.path.isfile(path(p)) for p in NEEDS[name]) and any(
            n.endswith(".th") for n in os.listdir(folder))
    return all(os.path.isfile(path(p)) for p in NEEDS[name])


# ── loading (torch only from here on) ───────────────────────────────────────────────────────────────────────────────────

def _free(keep: str | None = None) -> None:
    for name in [n for n in _loaded if n != keep and n in GPU_MODELS]:
        _loaded.pop(name, None)
    import gc
    gc.collect()
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 — no torch, nothing to free
        pass


def _load_sao(name: str):
    """Built in its final precision directly on the card and filled one tensor at a time from the safetensors file, so the Docker VM
    (8 GB, shared by the whole stack; an overshoot crashed it, 2026-10-24) holds one tensor at a time, never the model: SAO 1.0's file
    is 4.8 GB of fp32; built in fp16 it is 2.4 GB of VRAM."""
    import torch
    from safetensors import safe_open
    from stable_audio_tools.models.factory import create_model_from_config
    with open(path(PATHS[name], "model_config.json"), encoding="utf-8") as f:
        config = json.load(f)
    for cond in config["model"]["conditioning"]["configs"]:      # T5 from the archive, never the hub
        if cond.get("type") == "t5":
            cond["config"]["model_path"] = path(PATHS["t5"])
    dtype = torch.float16 if name == "sfx_loop" else torch.float32   # 1.0 is 1.2 B parameters; Small (0.5 B) stays full precision
    previous = torch.get_default_dtype()
    torch.set_default_dtype(dtype)
    try:
        with torch.device(DEVICE):                               # built ON the card: the VM's RAM never holds the model at all
            model = create_model_from_config(config)
    finally:
        torch.set_default_dtype(previous)
    own = model.state_dict()
    with safe_open(path(PATHS[name], "model.safetensors"), framework="pt") as weights:
        missing = set(own) - set(weights.keys())
        if any(not k.startswith("conditioner.") for k in missing):  # the T5 encoder comes from its own folder, not this file
            raise RuntimeError(f"{name}: weights file lacks {sorted(missing)[:3]}")
        with torch.no_grad():
            for key in weights.keys():
                if key in own:
                    own[key].copy_(weights.get_tensor(key))
    model = model.to(dtype).to(DEVICE).eval().requires_grad_(False)
    return {"model": model, "config": config}


def _load_musicgen():
    import torch
    from transformers import AutoProcessor, MusicgenMelodyForConditionalGeneration
    folder = path(PATHS["melody_variations"])
    processor = AutoProcessor.from_pretrained(folder)
    model = MusicgenMelodyForConditionalGeneration.from_pretrained(folder, torch_dtype=torch.float16, device_map=DEVICE,
                                                                   low_cpu_mem_usage=True).eval()  # straight to the card, shard by shard
    return {"model": model, "processor": processor}


def _load_demucs(quality: str):
    from pathlib import Path
    from demucs.pretrained import get_model
    model = get_model("htdemucs_ft" if quality == "ft" else "htdemucs", repo=Path(path(PATHS["stems"])))
    return {"model": model.to(DEVICE).eval(), "quality": quality}


def _load_basic_pitch():
    from basic_pitch.inference import Model
    return {"model": Model(path(PATHS["audio_to_midi"], "nmp.onnx"))}


def get(name: str, quality: str = "ft"):
    """The loaded model, loading it (and unloading any other GPU model) first when needed. Call with _lock held."""
    have = _loaded.get(name)
    if name == "stems" and have is not None and have["quality"] != quality:
        have = None
    if have is not None:
        return have
    if name in GPU_MODELS:
        _free()
    started = time.time()
    loader = {"sfx_quick": lambda: _load_sao("sfx_quick"), "sfx_loop": lambda: _load_sao("sfx_loop"),
              "melody_variations": _load_musicgen, "stems": lambda: _load_demucs(quality),
              "audio_to_midi": _load_basic_pitch}[name]
    _loaded[name] = loader()
    print(f"[audio-popups] loaded {name} in {time.time() - started:.1f}s", flush=True)
    return _loaded[name]


def _unload_when_idle():
    """After IDLE_UNLOAD_S quiet seconds: drop the models and, once torch has been imported, end the process. Docker's restart policy
    (unless-stopped) brings back a bare Flask of ~50 MB; staying up would keep ~1.2 GB of imported libraries in the Docker VM, which has
    8 GB for the whole stack."""
    while True:
        time.sleep(30)
        with _lock:
            if time.time() - _last_used <= IDLE_UNLOAD_S:
                continue
            if _loaded:
                names = list(_loaded)
                _loaded.clear()
                _free()
                print(f"[audio-popups] unloaded {', '.join(names)} after idle", flush=True)
            if "torch" in sys.modules and os.environ.get("EXIT_WHEN_IDLE", "1") == "1":
                print("[audio-popups] idle: exiting so the restart policy brings back a small process", flush=True)
                os._exit(0)


class Job:
    """One job: the lock, the clock, the peak-VRAM counter and the reply headers."""

    def __init__(self, name: str):
        self.name, self.headers = name, {}

    def __enter__(self):
        _lock.acquire()
        self.started = time.time()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:  # noqa: BLE001
            pass
        return self

    def __exit__(self, *exc):
        global _last_used
        _last_used = time.time()
        seconds = time.time() - self.started
        self.headers["X-Seconds"] = f"{seconds:.2f}"
        try:
            import torch
            if torch.cuda.is_available():
                self.headers["X-Peak-VRAM-MB"] = str(int(torch.cuda.max_memory_reserved() / 2**20))
        except Exception:  # noqa: BLE001
            pass
        print(f"[audio-popups] {self.name} in {seconds:.1f}s {self.headers}", flush=True)
        _lock.release()
        return False


class Refused(Exception):
    """A job this service won't do, with the HTTP status and the plain words the chat shows."""

    def __init__(self, status: int, words: str):
        super().__init__(words)
        self.status = status


class Result:
    def __init__(self, data: bytes, mimetype: str, filename: str, headers: dict):
        self.data, self.mimetype, self.filename, self.headers = data, mimetype, filename, headers


def need_ready(name: str) -> None:
    if not model_ready(name):
        raise Refused(503, f"{name}'s weights aren't on this machine")


# ── the work: each takes the arguments (a dict) and the attached clip (bytes or None) ───────────────────────────────────

def run_sao(name: str, body: dict, _clip=None) -> Result:
    if name == "sfx_loop":
        exact = bar_seconds(body.get("bpm"), body.get("bars"))
        seconds = min(exact, LIMITS["sfx_loop"][1]) if exact else clamp_seconds("sfx_loop", body.get("seconds"))
        loop = str(body.get("loop", "1" if exact else "0")).lower() in ("1", "true", "yes", "on")
        prompt = loop_prompt(body.get("prompt"), body.get("bpm"), body.get("key"))
    else:
        seconds, loop = clamp_seconds("sfx_quick", body.get("seconds")), False
        prompt = " ".join(str(body.get("prompt") or "").split())[:400]
    if not prompt:
        raise Refused(400, "a prompt is needed")
    need_ready(name)
    seed = pick_seed(body.get("seed"))
    with Job(name) as job:
        import torch
        from stable_audio_tools.inference.generation import generate_diffusion_cond
        m = get(name)
        model, config = m["model"], m["config"]
        rate, sample_size = int(config["sample_rate"]), int(config["sample_size"])
        if name == "sfx_quick":
            kw = {"steps": 8, "cfg_scale": 1.0, "sampler_type": "pingpong"}
        else:
            try:
                steps = max(20, min(250, int(body.get("steps") or 150)))
            except (TypeError, ValueError):
                steps = 150
            kw = {"steps": steps, "cfg_scale": 7.0, "sigma_min": 0.3, "sigma_max": 500, "sampler_type": "dpmpp-3m-sde"}
        with torch.inference_mode():
            audio = generate_diffusion_cond(model, conditioning=[{"prompt": prompt, "seconds_start": 0, "seconds_total": seconds}],
                                            sample_size=sample_size, seed=seed, device=DEVICE, **kw)
        data = finish(audio[0].float().cpu().numpy(), rate, int(round(seconds * rate)), loop=loop)
        job.headers.update({"X-Seed": str(seed), "X-Prompt": prompt[:200].encode("ascii", "replace").decode()})
    return Result(pcm16_wav(data, rate), "audio/wav", f"{name}-{seed}.wav", job.headers)


def run_melody(body: dict, clip) -> Result:
    prompt = " ".join(str(body.get("prompt") or "").split())[:400]
    if not clip:
        raise Refused(400, "attach the clip to vary (audio)")
    if not prompt:
        raise Refused(400, "a prompt is needed (the style to take the melody into)")
    need_ready("melody_variations")
    seconds = clamp_seconds("melody_variations", body.get("seconds"))
    seed = pick_seed(body.get("seed"))
    with Job("melody_variations") as job:
        import torch
        m = get("melody_variations")
        model, processor = m["model"], m["processor"]
        rate = int(model.config.audio_encoder.sampling_rate)
        ref = decode(clip, rate, 1, MELODY_REF_MAX_S)[0]
        inputs = processor(audio=ref, sampling_rate=rate, text=[prompt], padding=True, return_tensors="pt").to(DEVICE)
        inputs = {k: (v.half() if v.is_floating_point() else v) for k, v in inputs.items()}
        torch.manual_seed(seed)
        with torch.inference_mode():
            out = model.generate(**inputs, do_sample=True, guidance_scale=3.0,
                                 max_new_tokens=int(seconds * model.config.audio_encoder.frame_rate) + 4)
        data = finish(out[0].float().cpu().numpy(), rate, int(seconds * rate))
        job.headers["X-Seed"] = str(seed)
    return Result(pcm16_wav(data, rate), "audio/wav", f"melody-{seed}.wav", job.headers)


STEM_TYPES = {"flac": "audio/flac", "wav": "audio/wav", "mp3": "audio/mpeg"}
STEM_NAMES = ("drums", "bass", "other", "vocals")
def separate(clip: bytes, quality: str):
    """(stems {name: float array}, rate, seconds) for this clip."""
    import torch
    from demucs.apply import apply_model
    model = get("stems", quality)["model"]
    rate = int(model.samplerate)
    mix = torch.from_numpy(decode(clip, rate, 2, STEMS_MAX_S))
    ref = mix.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.inference_mode():
        out = apply_model(model, ((mix - mean) / std)[None].to(DEVICE), device=DEVICE,
                          shifts=DEMUCS_SHIFTS if quality == "ft" else 1, split=True, overlap=0.25, progress=False)[0]
    out = (out * std.to(out.device) + mean.to(out.device)).float().cpu().numpy()
    return {n: out[i] for i, n in enumerate(model.sources)}, rate, mix.shape[1] / rate


def run_stems(body: dict, clip) -> Result:
    if not clip:
        raise Refused(400, "attach the track to split (audio)")
    fmt = str(body.get("format") or "flac").lower()
    if fmt not in STEM_TYPES:
        raise Refused(400, "format is flac, wav or mp3")
    quality = "fast" if str(body.get("quality") or "").lower() == "fast" else "ft"
    only = str(body.get("stem") or "").lower() or None
    if only and only not in (*STEM_NAMES, "instrumental"):
        raise Refused(400, "stem is drums, bass, other, vocals or instrumental")
    need_ready("stems")
    with Job("stems") as job:
        stems_, rate, seconds = separate(clip, quality)
        if only == "instrumental":                                # everything but the vocals: the classic ask
            picked = {"instrumental": stems_["drums"] + stems_["bass"] + stems_["other"]}
        else:
            picked = {n: a for n, a in stems_.items() if only in (None, n)}
        files = {n: encode(a, rate, fmt) for n, a in picked.items()}
        job.headers.update({"X-Stems": ",".join(files), "X-Duration-S": f"{seconds:.1f}"})
    if only:
        return Result(files[only], STEM_TYPES[fmt], f"{only}.{fmt}", job.headers)
    return Result(stem_zip(files, fmt), "application/zip", "stems.zip", job.headers)


NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(pitch: int) -> str:
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


def note_list(notes, bpm: float) -> list[dict]:
    """Basic Pitch's (start s, end s, pitch, amplitude, bends) as a producer reads them: note, beat, length in beats, velocity."""
    beat = 60.0 / bpm
    rows = []
    for start, end, pitch, amp, *_ in sorted(notes, key=lambda n: (n[0], n[2])):
        rows.append({"note": note_name(int(pitch)), "beat": round(start / beat, 3), "beats": round((end - start) / beat, 3),
                     "velocity": int(max(1, min(127, round(127 * float(amp)))))})
    return rows


def run_midi(body: dict, clip) -> Result:
    if not clip:
        raise Refused(400, "attach the clip to transcribe (audio)")
    need_ready("audio_to_midi")

    def num(key, default, lo, hi):
        try:
            return max(lo, min(hi, float(body.get(key) if body.get(key) not in (None, "") else default)))
        except (TypeError, ValueError):
            return default

    bpm = num("bpm", 120, 40, 250)
    with Job("audio_to_midi") as job:
        from basic_pitch.inference import predict
        model = get("audio_to_midi")["model"]
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "in.wav")
            with open(wav, "wb") as f:
                f.write(pcm16_wav(decode(clip, 22050, 1, MIDI_MAX_S), 22050))
            _, midi, notes = predict(wav, model, onset_threshold=num("onset", 0.5, 0.05, 0.95),
                                     frame_threshold=num("frame", 0.3, 0.05, 0.95),
                                     minimum_note_length=num("min_note_ms", 127.7, 20, 2000), midi_tempo=bpm)
            out = io.BytesIO()
            midi.write(out)
        job.headers["X-Notes"] = str(len(notes))
    if str(body.get("as") or "").lower() == "json":
        import base64
        return Result(json.dumps({"bpm": bpm, "notes": note_list(notes, bpm),
                                  "midi_b64": base64.b64encode(out.getvalue()).decode()}).encode(),
                      "application/json", "transcribed.json", job.headers)
    return Result(out.getvalue(), "audio/midi", "transcribed.mid", job.headers)


WORK = {"sfx_quick": lambda b, c: run_sao("sfx_quick", b, c), "sfx_loop": lambda b, c: run_sao("sfx_loop", b, c),
        "melody_variations": run_melody, "stems": run_stems, "audio_to_midi": run_midi}


def answer(name: str, body: dict, clip) -> Response:
    try:
        r = WORK[name](body, clip)
        headers = dict(r.headers, **{"Content-Disposition": f'attachment; filename="{r.filename}"'})
        response = Response(r.data, mimetype=r.mimetype, headers=headers)
    except Refused as exc:
        response = jsonify(error=str(exc))
        response.status_code = exc.status
    except BadClip as exc:
        response = jsonify(error=str(exc)[:300])
        response.status_code = 400
    except Exception as exc:  # noqa: BLE001 — the reporter shows the reason in the chat
        response = jsonify(error=f"{type(exc).__name__}: {exc}"[:300])
        response.status_code = 500
    if EXIT_AFTER_JOB and app.config.get("SERVING") and (_loaded or "torch" in sys.modules):
        response.call_on_close(_end_after_job)            # a refusal before any load leaves the small process be
    return response


def _end_after_job() -> None:
    """Once the reply has been sent: end the process, unless another job already holds the lock (it ends it instead)."""
    if _lock.acquire(blocking=False):
        print("[audio-popups] job done: exiting so the card and the VM get everything back", flush=True)
        os._exit(0)


# ── routes ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def gpu_figures() -> dict:
    """The whole card's free memory and load (the portal's patient queue starts a GPU job only when there is room), from nvidia-smi,
    never torch: asking torch would make a CUDA context and cost VRAM just to answer /health."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout.split(",")
        total, used, util = (float(x) for x in out[:3])
        return {"gpu_free_pct": round(100 * (total - used) / total, 1), "gpu_util_pct": util,
                "gpu_free_gib": round((total - used) / 1024, 2)}
    except Exception:  # noqa: BLE001 — no card (tests, CI): say nothing rather than guess
        return {}


@app.get("/health")
def health():
    models = {n: {"ready": model_ready(n), "loaded": n in _loaded, "gpu": n in GPU_MODELS} for n in NEEDS}
    return jsonify(ready=any(m["ready"] for m in models.values()), loaded=bool(_loaded), loaded_models=sorted(_loaded),
                   models=models, idle_unload_s=IDLE_UNLOAD_S, **gpu_figures())


@app.post("/run/<name>")
def run(name: str):
    """The pop-up contract (docs/popups.md): {"args": {...}, "input"?: {"type", "name", "b64"}} -> the result's bytes. The chat can
    keep 25 MB at most, so the stems' zip holds mp3s; a `stem` argument hands back that one stem instead."""
    if name not in WORK:
        return jsonify(error=f"there is no {name} here"), 404
    body = request.get_json(silent=True) or {}
    args = body.get("args") if isinstance(body.get("args"), dict) else {}
    clip = None
    given = body.get("input")
    if isinstance(given, dict) and given.get("b64"):
        import base64
        import binascii
        try:
            clip = base64.b64decode(given["b64"], validate=True)
        except (binascii.Error, ValueError):
            return jsonify(error="the attached file didn't arrive whole"), 400
    if name == "stems":                                           # mp3 inside the zip: four lossless stems would pass 25 MB
        args = {**args, "format": "mp3"}
    if name == "audio_to_midi":
        args = {k: v for k, v in args.items() if k != "as"}
    return answer(name, args, clip)


@app.post("/<name>")
def native(name: str):
    """The full routes, for a person or a script with a whole track: JSON or a form, the clip as multipart `audio` or a raw body;
    stems can come as one zip and MIDI as a real .mid."""
    if name not in WORK:
        return jsonify(error=f"there is no {name} here"), 404
    return answer(name, params(), upload())


if __name__ == "__main__":                                       # only the service unloads and exits; an import (the tests) never does
    threading.Thread(target=_unload_when_idle, daemon=True).start()
    app.config["SERVING"] = True
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "47440")), threaded=True)
