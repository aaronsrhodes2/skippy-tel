"""The "Skippy" spotter: a small openWakeWord model that hears its name in the raw microphone stream, so nothing said in a room goes anywhere
until it is addressed. It replaces the old way (every utterance transcribed by the home Whisper and thrown away unless it began with "Skippy"),
which stays as the fallback when no model has been trained yet (wakeword/README.md: how to train skippy.onnx on skippy-pc's GPU).

    spot = Spotter.load("state/wakeword/skippy.onnx")      # or Spotter(model=..., ...) in tests
    if spot.feed(samples):                                  # 16 kHz mono float samples in -1..1, any block size
        ...                                                 # "Skippy" was said

openWakeWord scores 80 ms frames (1280 samples) 0..1. A word counts when `patience` frames in a row reach the threshold; after one it is deaf
for `refractory_ms`, so one "Skippy" never wakes it twice. openwakeword and numpy are imported only by load(), so tests need neither.
"""
from __future__ import annotations

from pathlib import Path

FRAME = 1280                                                    # 80 ms at 16 kHz: openWakeWord's step
RATE = 16000


def default_model(env: dict) -> Path:
    """Where the trained model lives (git-ignored): SATELLITE_WAKE_MODEL, else state/wakeword/skippy.onnx beside the repo's other home state."""
    if env.get("SATELLITE_WAKE_MODEL"):
        return Path(env["SATELLITE_WAKE_MODEL"])
    return Path(__file__).resolve().parents[2] / "state" / "wakeword" / "skippy.onnx"


class Spotter:
    def __init__(self, model, name: str = "skippy", threshold: float = 0.5, patience: int = 2, refractory_ms: int = 1500):
        self.model, self.name = model, name
        self.threshold, self.patience, self.refractory = threshold, max(1, patience), refractory_ms * RATE // 1000
        self._pending: list[int] = []
        self._t = 0                                             # samples heard so far (the stream's own clock)
        self._deaf_until = 0
        self._run = 0
        self.last = 0.0                                         # the latest frame's score, for --calibrate style read-outs

    @classmethod
    def load(cls, path, threshold: float = 0.5, patience: int = 2) -> "Spotter":
        """The trained model from disk (onnx; openWakeWord fetches its two small shared feature models once, on first use)."""
        import openwakeword.utils                               # noqa: PLC0415 — only a machine that listens has it
        from openwakeword.model import Model                    # noqa: PLC0415
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"no wake word model at {path} (see services/satellite/wakeword/README.md)")
        openwakeword.utils.download_models(model_names=["__features_only__"])
        return cls(Model(wakeword_models=[str(path)], inference_framework="onnx"), name=path.stem, threshold=threshold, patience=patience)

    def feed(self, samples) -> bool:
        """True once when the word has just been heard in these samples (float -1..1)."""
        self._pending.extend(max(-32768, min(32767, int(float(x) * 32767))) for x in samples)
        heard = False
        while len(self._pending) >= FRAME:
            frame, self._pending = self._pending[:FRAME], self._pending[FRAME:]
            self._t += FRAME
            if self._score(frame) and not heard:
                heard = True
        return heard

    def _score(self, frame: list[int]) -> bool:
        scores = self.model.predict(_int16(frame)) or {}
        self.last = float(max(scores.values(), default=0.0))
        if self._t < self._deaf_until:
            return False
        self._run = self._run + 1 if self.last >= self.threshold else 0
        if self._run < self.patience:
            return False
        self._run, self._deaf_until = 0, self._t + self.refractory
        reset = getattr(self.model, "reset", None)
        if reset:
            reset()                                             # forget the word, or its tail scores again after the deaf spell
        return True

    def reset(self) -> None:
        """Start fresh (after Skippy has spoken: his own voice saying his name must not wake him)."""
        self._pending, self._run = [], 0
        reset = getattr(self.model, "reset", None)
        if reset:
            reset()


def _int16(frame: list[int]):
    try:
        import numpy as np                                      # noqa: PLC0415
    except ImportError:                                         # tests with a fake model: a list is enough
        return frame
    return np.asarray(frame, dtype=np.int16)
