"""Voice bake-off: render lines.json zero-shot in Skippy's voice with one local TTS model, and time it.

Runs inside that model's container (Dockerfile.<model>), on skippy-pc's GPU, with --network none: the weights come read-only
from D:\\models at /w, the reference and the output from /vb (state/voice-bakeoff). Writes /vb/out/<model>/NN.wav and timing.json.

  python bench.py <qwen3|chatterbox|f5|xtts>

Measured per line: wall time (sync'd CUDA), audio seconds, real-time factor (wall / audio; below 1 is faster than real time),
and time to first audio: the first streamed chunk where the model has a streaming API (xtts, qwen3 does not expose one in qwen-tts),
otherwise the whole render (the clip can't play before it exists). VRAM: torch's peak allocated and reserved after load and after
the run. One warm-up line first (CUDA kernels, caches), not counted.
"""
import json, os, sys, time

import numpy as np
import soundfile as sf
import torch

VB = "/vb"
REF_WAV = os.environ.get("REF_WAV", f"{VB}/ref/ref.wav")   # qwen3 runs with ref_pad.wav: see docs/voice-bakeoff.md
REF_TXT = open(os.environ.get("REF_TXT", f"{VB}/ref/ref.txt"), encoding="utf-8").read().strip()
E = lambda k, d: float(os.environ.get(k, d))   # per-engine settings for the sweeps (docs/voice-bakeoff.md); defaults = the library's
SETTINGS = {k: v for k, v in os.environ.items() if k.split("_")[0] in ("CB", "F5", "XTTS", "QWEN") or k in ("REF_WAV", "REF_TXT")}
LINES = json.load(open("/bench/lines.json", encoding="utf-8"))[: int(os.environ.get("N_LINES", "99"))]
WARMUP = "Fine. I'll warm up, since apparently that is my job now."


def sync():
    torch.cuda.synchronize()


class Qwen3:
    name = "Qwen3-TTS-12Hz-1.7B-Base"

    def __init__(self):
        from qwen_tts import Qwen3TTSModel
        self.m = Qwen3TTSModel.from_pretrained("/w", device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="sdpa")
        self.prompt = self.m.create_voice_clone_prompt(ref_audio=REF_WAV, ref_text=REF_TXT, x_vector_only_mode=os.environ.get("QWEN_XVEC") == "1")

    def say(self, text):
        wavs, sr = self.m.generate_voice_clone(text=text, language="English", voice_clone_prompt=self.prompt)
        return np.asarray(wavs[0], dtype=np.float32), sr, None


class Chatterbox:
    name = "Chatterbox (ResembleAI, English)"

    def __init__(self):
        from chatterbox.tts import ChatterboxTTS
        self.m = ChatterboxTTS.from_local("/w", "cuda")
        self.configure()

    def configure(self):
        self.exag, self.cfg, self.temp = E("CB_EXAG", 0.5), E("CB_CFG", 0.5), E("CB_TEMP", 0.8)
        self.m.prepare_conditionals(REF_WAV, exaggeration=self.exag)

    def say(self, text):
        wav = self.m.generate(text, exaggeration=self.exag, cfg_weight=self.cfg, temperature=self.temp)
        return wav.squeeze(0).cpu().numpy().astype(np.float32), self.m.sr, None


class F5:
    name = "F5-TTS v1 Base"

    def __init__(self):
        from f5_tts.api import F5TTS
        self.m = F5TTS(model="F5TTS_v1_Base", ckpt_file="/w/F5TTS_v1_Base/model_1250000.safetensors",
                       vocab_file="/w/F5TTS_v1_Base/vocab.txt", vocoder_local_path="/vocos", device="cuda")

    def say(self, text):
        wav, sr, _ = self.m.infer(ref_file=REF_WAV, ref_text=REF_TXT, gen_text=text, remove_silence=False, show_info=lambda *a: None,
                                  nfe_step=int(E("F5_NFE", 32)), speed=E("F5_SPEED", 1.0), cfg_strength=E("F5_CFG", 2.0))
        return np.asarray(wav, dtype=np.float32), sr, None


class XTTS:
    name = "XTTS-v2 (Coqui)"

    def __init__(self):
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts
        cfg = XttsConfig()
        cfg.load_json("/w/config.json")
        self.m = Xtts.init_from_config(cfg)
        # coqui-tts loads the archived checkpoint with weights_only=True (allow-listed config classes), then tries to torch.save a
        # converted copy over model.pth. /w is read-only (the archive must stay byte-identical), so that one save is skipped.
        import TTS.tts.models.xtts as xm
        real_save, xm.torch.save = xm.torch.save, lambda *a, **k: None
        try:
            self.m.load_checkpoint(cfg, checkpoint_dir="/w", use_deepspeed=False)
        finally:
            xm.torch.save = real_save
        self.m.cuda()
        self.lat, self.spk = self.m.get_conditioning_latents(audio_path=[REF_WAV])

    def say(self, text):
        t0 = time.perf_counter()
        first, chunks = None, []
        for c in self.m.inference_stream(text, "en", self.lat, self.spk, enable_text_splitting=True, speed=E("XTTS_SPEED", 1.0),
                                          temperature=E("XTTS_TEMP", 0.75), top_p=E("XTTS_TOPP", 0.85)):
            if first is None:
                sync()
                first = time.perf_counter() - t0
            chunks.append(c.cpu().numpy())
        return np.concatenate(chunks).astype(np.float32), 24000, first


MODELS = {"qwen3": Qwen3, "chatterbox": Chatterbox, "f5": F5, "xtts": XTTS}


def gib(b):
    return round(b / 2**30, 2)


def run_lines(model, out):
    os.makedirs(out, exist_ok=True)
    model.say(WARMUP)
    sync()
    runs = []
    for i, text in enumerate(LINES):
        sync()
        t0 = time.perf_counter()
        wav, sr, first = model.say(text)
        sync()
        wall = time.perf_counter() - t0
        secs = len(wav) / sr
        peak = float(np.abs(wav).max()) or 1.0
        sf.write(f"{out}/{i:02d}.wav", wav / max(peak, 1.0), sr)
        runs.append({"i": i, "wall_s": round(wall, 3), "audio_s": round(secs, 3), "rtf": round(wall / secs, 3),
                     "ttfa_s": round(first if first is not None else wall, 3), "streamed": first is not None})
        print(json.dumps(runs[-1]), flush=True)
    return runs


def main(key):
    """One model load, then one pass over LINES per settings set. SWEEP='{"tag": {"F5_NFE": 16}, ...}' writes out/<OUT>/<tag>/;
    without SWEEP it is one pass with the environment's settings, written to out/<OUT> as before."""
    out = f"{VB}/out/{os.environ.get('OUT', key)}"
    sweep = json.loads(os.environ["SWEEP"]) if os.environ.get("SWEEP") else None
    torch.cuda.reset_peak_memory_stats()
    t0 = time.perf_counter()
    model = MODELS[key]()
    sync()
    load_s = time.perf_counter() - t0
    vram_load = (gib(torch.cuda.max_memory_allocated()), gib(torch.cuda.max_memory_reserved()))
    for tag, knobs in (sweep or {"": {}}).items():
        for k in [k for k in os.environ if k.split("_")[0] in ("CB", "F5", "XTTS") and k not in knobs and sweep]:
            del os.environ[k]                                   # each sweep entry starts from the library defaults
        os.environ.update({k: str(v) for k, v in knobs.items()})
        if hasattr(model, "configure"):
            model.configure()
        torch.cuda.reset_peak_memory_stats()
        d = f"{out}/{tag}" if tag else out
        print(f"== {tag or 'single'} {knobs}", flush=True)
        runs = run_lines(model, d)
        res = {"model": model.name, "key": key, "tag": tag, "settings": {**SETTINGS, **{k: str(v) for k, v in knobs.items()}},
               "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__, "load_s": round(load_s, 1),
               "vram_after_load_gib": {"allocated": vram_load[0], "reserved": vram_load[1]},
               "vram_peak_gib": {"allocated": gib(torch.cuda.max_memory_allocated()), "reserved": gib(torch.cuda.max_memory_reserved())},
               "runs": runs}
        json.dump(res, open(f"{d}/timing.json", "w"), indent=1)
        print(json.dumps({k: v for k, v in res.items() if k != "runs"}), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
