"""diarize: an attached recording back as a speaker-labelled transcript ("who spoke when", then what each one said).

Who spoke when is sherpa-onnx's offline speaker diarization, on the CPU: pyannote's segmentation-3.0 (MIT; the ONNX export k2-fsa
publishes, so no Hugging Face gate and no token) finds the speech and the speaker changes, a WeSpeaker ResNet34 (VoxCeleb) turns each
stretch into a voice print, and the prints are clustered into speakers. What each one said is the whisper container that already
sits behind voice-gate (skippytel-whisper, faster-whisper on the GPU): each turn is cut out and sent to its /transcribe, so no second
copy of whisper is ever loaded. Whisper there always answers in English (task "translate"), so a turn in another language comes back
translated.

  GET  /health   {"ready": bool, "whisper": bool, "models": bool}
  POST /run      {"args": {"format"?: "text" | "json"}, "input": {"type", "name", "b64"}}
                 -> text/plain ("Speaker 1 [0:03-0:11]: ...", one turn a paragraph), or application/json with every turn.

The speaker count is the clustering's to find (cosine THRESHOLD): forcing a count merged two of three speakers in the benchmark
(docs/search-speech-popups.md), so it isn't offered.

Nothing is kept: the audio lives in memory for the length of the request (the turn files whisper reads are its own tempfiles).
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

RATE = 16000
MODELS = os.environ.get("DIARIZE_MODELS", "/models")
SEGMENTATION = os.environ.get("SEGMENTATION_MODEL", f"{MODELS}/pyannote-segmentation-3-0/model.onnx")
EMBEDDING = os.environ.get("EMBEDDING_MODEL", f"{MODELS}/wespeaker_en_voxceleb_resnet34_LM.onnx")
WHISPER = os.environ.get("WHISPER_URL", "http://whisper:8000").rstrip("/")
THREADS = int(os.environ.get("DIARIZE_THREADS", "4"))
THRESHOLD = float(os.environ.get("CLUSTER_THRESHOLD", "0.5"))   # cosine distance at which two prints are one speaker (unknown count)
MAX_BODY = 40_000_000             # base64 of the largest recording the portal passes (it holds attachments far smaller)
MAX_SECONDS = 3 * 3600
MERGE_GAP = 1.0                   # one speaker's stretches this close together are one turn
MIN_TURN = 0.4                    # shorter turns are a cough or a "mm": whisper gets nothing worth sending
NO_PROMPT = " "                   # whisper's initial_prompt: present (so not its voice-command primer) and empty of words
MAX_TURN = 30.0                   # whisper's window: a longer turn goes in pieces (one transcript line still)

_sd = None
_sd_key = None
_lock = threading.Lock()


def decode(data: bytes) -> np.ndarray:
    """Any audio ffmpeg reads -> 16 kHz mono float32."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "pipe:1"],
                       input=data, capture_output=True, timeout=300)
    if p.returncode != 0 or not p.stdout:
        raise ValueError("that recording couldn't be read: " + p.stderr.decode(errors="replace")[-200:])
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def diarizer(speakers: int | None):
    """One sherpa-onnx diarizer per speaker count (building it is ~0.1 s; the models are small and mapped)."""
    global _sd, _sd_key
    import sherpa_onnx
    key = speakers or 0
    if _sd is None or _sd_key != key:
        cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=SEGMENTATION), num_threads=THREADS),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=EMBEDDING, num_threads=THREADS),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=speakers or -1, threshold=THRESHOLD),
            min_duration_on=0.3, min_duration_off=0.5)
        if not cfg.validate():
            raise RuntimeError("the diarization models aren't where they should be")
        _sd, _sd_key = sherpa_onnx.OfflineSpeakerDiarization(cfg), key
    return _sd


def who_spoke_when(samples: np.ndarray, speakers: int | None = None) -> list[dict]:
    with _lock:
        result = diarizer(speakers).process(samples).sort_by_start_time()
    return [{"start": float(s.start), "end": float(s.end), "speaker": int(s.speaker)} for s in result]


def turns(segments: list[dict], gap: float = MERGE_GAP) -> list[dict]:
    """Consecutive stretches of one speaker become one turn; speakers are renumbered 1, 2, ... in the order they first speak."""
    out: list[dict] = []
    for s in sorted(segments, key=lambda s: s["start"]):
        if out and out[-1]["speaker"] == s["speaker"] and s["start"] - out[-1]["end"] <= gap:
            out[-1]["end"] = max(out[-1]["end"], s["end"])
        else:
            out.append(dict(s))
    names: dict[int, int] = {}
    for t in out:
        t["speaker"] = names.setdefault(t["speaker"], len(names) + 1)
    return [t for t in out if t["end"] - t["start"] >= MIN_TURN]


def wav_bytes(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def whisper(clip: bytes, prompt: str = "") -> str:
    """POST one turn to the resident whisper's /transcribe (multipart, as flask-api and voice-gate send it)."""
    bound = uuid.uuid4().hex
    parts = [f'--{bound}\r\nContent-Disposition: form-data; name="audio"; filename="turn.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
             clip, b"\r\n"]
    if prompt:
        parts += [f'--{bound}\r\nContent-Disposition: form-data; name="initial_prompt"\r\n\r\n'.encode(), prompt.encode(), b"\r\n"]
    parts.append(f"--{bound}--\r\n".encode())
    req = urllib.request.Request(f"{WHISPER}/transcribe", data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={bound}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return str(json.loads(r.read()).get("english_text") or "").strip()


def transcribe(samples: np.ndarray, plan: list[dict], ask=None) -> list[dict]:
    """Each turn's words; a turn longer than whisper's window goes in pieces. Whisper gets NO prompt: the previous speaker's words
    as a prompt made it invent a line on the next speaker's turn (the benchmark clip, docs/search-speech-popups.md), and an empty
    prompt makes skippytel-whisper fall back to its voice-command primer, so it is sent a single space."""
    ask = ask or whisper
    for t in plan:
        a, b = int(t["start"] * RATE), int(t["end"] * RATE)
        bits = []
        for s in range(a, b, int(MAX_TURN * RATE)):
            piece = samples[s:min(b, s + int(MAX_TURN * RATE))]
            if len(piece) >= MIN_TURN * RATE:
                bits.append(ask(wav_bytes(piece), NO_PROMPT))
        t["text"] = " ".join(x for x in bits if x).strip()
    return plan


def clock(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def as_text(plan: list[dict], seconds: float) -> str:
    n = len({t["speaker"] for t in plan})
    head = f"{n} speaker{'s' if n != 1 else ''}, {len(plan)} turn{'s' if len(plan) != 1 else ''}, {clock(seconds)}"
    lines = [f"Speaker {t['speaker']} [{clock(t['start'])}-{clock(t['end'])}]: {t.get('text') or '(nothing whisper could make out)'}"
             for t in plan]
    return head + "\n\n" + "\n\n".join(lines) + "\n"


def run(data: bytes, args: dict) -> tuple[bytes, str]:
    samples = decode(data)
    seconds = len(samples) / RATE
    if seconds > MAX_SECONDS:
        raise ValueError(f"that recording is {clock(seconds)}; the most it takes is {clock(MAX_SECONDS)}")
    t0 = time.time()
    plan = turns(who_spoke_when(samples))
    t1 = time.time()
    plan = transcribe(samples, plan)
    t2 = time.time()
    print(json.dumps({"event": "diarized", "seconds": round(seconds, 1), "turns": len(plan),
                      "diarize_s": round(t1 - t0, 2), "whisper_s": round(t2 - t1, 2)}), flush=True)
    if args.get("format") == "json":
        body = {"seconds": round(seconds, 2), "speakers": len({t["speaker"] for t in plan}),
                "turns": [{**t, "start": round(t["start"], 2), "end": round(t["end"], 2)} for t in plan]}
        return json.dumps(body).encode(), "application/json"
    return as_text(plan, seconds).encode(), "text/plain"


def health() -> dict:
    models = os.path.isfile(SEGMENTATION) and os.path.isfile(EMBEDDING)
    try:
        with urllib.request.urlopen(f"{WHISPER}/health", timeout=3) as r:
            heard = json.loads(r.read()).get("status") == "ok"
    except (OSError, ValueError):
        heard = False
    return {"ready": models and heard, "whisper": heard, "models": models}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype == "text/plain" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, status: int, why: str) -> None:
        self._send(status, json.dumps({"error": why}).encode())

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send(200, json.dumps(health()).encode())
        self._err(404, "no such page")

    def do_POST(self):  # noqa: N802
        if self.path != "/run":
            return self._err(404, "no such page")
        size = int(self.headers.get("Content-Length") or 0)
        if size > MAX_BODY:
            return self._err(413, "that recording is too big")
        try:
            body = json.loads(self.rfile.read(size) or b"{}")
            args = body.get("args") if isinstance(body.get("args"), dict) else {}
            given = body.get("input") if isinstance(body.get("input"), dict) else None
            if not given:
                return self._err(400, "attach a recording: diarize needs audio")
            data = base64.b64decode(given["b64"], validate=True)
        except (ValueError, KeyError, TypeError):
            return self._err(400, "that wasn't a pop-up request")
        try:
            out, ctype = run(data, args)
        except ValueError as exc:
            return self._err(400, str(exc)[:300])
        except (urllib.error.URLError, OSError) as exc:
            return self._err(503, f"whisper didn't answer: {exc}"[:300])
        except Exception as exc:  # noqa: BLE001 - one bad recording must not take the service down
            return self._err(500, f"{type(exc).__name__}: {exc}"[:300])
        self._send(200, out, ctype)

    def log_message(self, *a):
        pass


def serve(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


if __name__ == "__main__":
    serve(int(os.environ.get("PORT", "47431"))).serve_forever()
