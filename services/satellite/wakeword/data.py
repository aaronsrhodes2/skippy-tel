"""Fetch what openWakeWord's trainer learns from, once, into /work/data (state/wakeword/data on skippy-pc; about 20 GB). Each part is skipped when
it is already there, so a broken download is fixed by running it again. Runs inside the training container (train.sh)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DATA = Path(os.environ.get("WAKE_DATA", "/work/data"))
FEATURES = "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/"
AUDIOSET_SHARDS = ["data/bal_train/09.parquet"]             # ~0.7 GB of everyday sounds (a TV, a dog, dishes); add shards for a sturdier model
FMA_HOURS = 1                                               # music, so a song with "skippy" in no line does not wake him


def _wav_dir(rows, out: Path, limit: int | None = None) -> None:
    import numpy as np                                      # noqa: PLC0415
    import scipy.io.wavfile                                 # noqa: PLC0415
    from tqdm import tqdm                                   # noqa: PLC0415
    out.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(tqdm(rows, total=limit)):
        if limit is not None and i >= limit:
            break
        audio = row["audio"]
        name = Path(str(audio.get("path") or f"{i}.wav")).stem + ".wav"
        scipy.io.wavfile.write(out / name, 16000, (np.asarray(audio["array"]) * 32767).astype(np.int16))
    (out / ".done").touch()


def _done(path: Path) -> bool:
    return (path / ".done").exists() if path.suffix == "" else path.is_file()


def main() -> int:
    import datasets                                         # noqa: PLC0415
    DATA.mkdir(parents=True, exist_ok=True)
    for name in ("validation_set_features.npy", "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"):   # 0.2 GB, then 17 GB
        if not _done(DATA / name):
            print(f"downloading {name} ...", flush=True)
            subprocess.run(["wget", "-c", "-q", "--show-progress", "-O", str(DATA / (name + ".part")), FEATURES + name], check=True)
            (DATA / (name + ".part")).rename(DATA / name)
    if not _done(DATA / "mit_rirs"):                       # how rooms echo: every clip is played "in" a real room
        rirs = datasets.load_dataset("davidscripka/MIT_environmental_impulse_responses", split="train", streaming=True)
        _wav_dir(rirs, DATA / "mit_rirs")
    if not _done(DATA / "audioset_16k"):
        from huggingface_hub import hf_hub_download         # noqa: PLC0415 — datasets' own dependency
        shards = [hf_hub_download("agkphysics/AudioSet", s, repo_type="dataset", cache_dir=str(DATA / "hf")) for s in AUDIOSET_SHARDS]
        noise = datasets.load_dataset("parquet", data_files=shards, split="train").cast_column("audio", datasets.Audio(sampling_rate=16000))
        _wav_dir(noise, DATA / "audioset_16k")
    if not _done(DATA / "fma"):
        fma = datasets.load_dataset("rudraml/fma", name="small", split="train", streaming=True).cast_column("audio", datasets.Audio(sampling_rate=16000))
        _wav_dir(fma, DATA / "fma", limit=FMA_HOURS * 3600 // 30)   # 30 s clips
    print("data: ready", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
