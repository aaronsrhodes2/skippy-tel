"""Speaker similarity for the sweeps: every folder of clips under ROOT (e.g. elref/f5/nfe16) against the ElevenLabs Skippy voice
(the 10 lines in out/elevenlabs plus the ElevenLabs reference clip). Same judge and scale as similarity.py (Resemblyzer GE2E; about
0.8 and up = same speaker). Writes ROOT/similarity.json. Polished (_fx) clips are scored separately.

  docker run --rm --network none -v D:/Aaron/development/SkippyTel/state/voice-bakeoff:/vb --entrypoint python \
      voice-bakeoff-judge /bench/sim_dirs.py /vb/elref
"""
import glob, json, os, sys

import numpy as np
from resemblyzer import VoiceEncoder, preprocess_wav

enc = VoiceEncoder("cpu")
emb = lambda p: enc.embed_utterance(preprocess_wav(p))
unit = lambda vs: (lambda m: m / np.linalg.norm(m))(np.mean(vs, axis=0))

root = sys.argv[1]
target = unit([emb(f) for f in sorted(glob.glob("/vb/out/elevenlabs/el_*.mp3"))] + [emb("/vb/ref-el/ref.wav")])
res = {}
for d in sorted({os.path.dirname(f) for f in glob.glob(f"{root}/**/*.wav", recursive=True)}):
    for kind, pat in (("raw", "[0-9][0-9].wav"), ("fx", "[0-9][0-9]_fx.wav")):
        files = sorted(glob.glob(f"{d}/{pat}"))
        if files:
            res.setdefault(os.path.relpath(d, root), {})[kind] = round(float(unit([emb(f) for f in files]) @ target), 3)
json.dump(res, open(f"{root}/similarity.json", "w"), indent=1)
for k, v in res.items():
    print(f"{k:28s} vs ElevenLabs Skippy  raw {v.get('raw', 0):.3f}  polished {v.get('fx', 0):.3f}")
