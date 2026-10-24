"""Speaker similarity: how much each voice sounds like the same speaker as the reference, by a neutral judge.

Resemblyzer (GE2E speaker encoder; its weights ship inside the pip package, none of the contestants use it) embeds every clip;
the score is the cosine between a voice's mean embedding over its 10 lines and (a) the source recording (the 11 s reference plus the
rest of skippy-voice-training.wav) and (b) ElevenLabs' Skippy voice. Same speaker usually scores above ~0.80, different speakers
~0.5-0.7. A hint, not a verdict: the Captain's ears decide.

  docker build --build-arg PIP=resemblyzer==0.1.4 -t voice-bakeoff-judge tools/voice_bakeoff
  docker run --rm --network none -v D:/Aaron/development/SkippyTel/state/voice-bakeoff:/vb --entrypoint python \
      voice-bakeoff-judge /bench/similarity.py
"""
import json, os, sys

import numpy as np
from resemblyzer import VoiceEncoder, preprocess_wav

sys.path.insert(0, "/bench")
from build_page import SOURCES  # noqa: E402

VB = "/vb"
enc = VoiceEncoder("cpu")


def emb(path):
    return enc.embed_utterance(preprocess_wav(path))


def mean(vs):
    m = np.mean(vs, axis=0)
    return m / np.linalg.norm(m)


source = mean([emb(f"{VB}/ref/ref.wav"), emb(f"{VB}/ref/skippy-voice-training.wav")])
voices = {}
for key, folder, pat in SOURCES:
    files = [f"{VB}/out/{folder}/{pat.format(i=i)}" for i in range(10)]
    if all(os.path.exists(f) for f in files):
        voices[key] = mean([emb(f) for f in files])
res = {k: {"vs_source_recording": round(float(v @ source), 3),
           "vs_elevenlabs_skippy": round(float(v @ voices["elevenlabs"]), 3) if "elevenlabs" in voices else None}
       for k, v in voices.items()}
json.dump(res, open(f"{VB}/out/similarity.json", "w"), indent=1)
for k, r in res.items():
    print(f"{k:12s} vs source {r['vs_source_recording']:.3f}   vs ElevenLabs Skippy {r['vs_elevenlabs_skippy']:.3f}")
