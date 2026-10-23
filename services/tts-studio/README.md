# tts-studio

Skippy chat's local voices on skippy-pc's GPU, reached only by `voice-gate` over the internal `tts-net` (no route out, no published port).
`app.py`'s docstring is the authority on routes. Which voice a person may hear is the portal's pass, checked by voice-gate, never this service.

| voice | model | who | license |
|---|---|---|---|
| `stock` | Kokoro-82M, `STOCK_VOICE` (default `bm_george`) | everyone who may chat (Public) | Apache-2.0 |
| `skippy` | F5-TTS v1 Base, zero-shot from `state/tts-studio/ref/{ref.wav,ref.txt}` | the Captain (Private) | CC BY-NC; a clone of a real narrator: personal use |

The reference cut is the voice bake-off's (`docs/voice-bakeoff.md` on `claude/hopeful-elgamal-0df0b1`): 11 s of
`state/skippy-voice-training.m4a`. It is not in git. Weights are mounted read-only from `D:/models`; Vocos (F5's vocoder) and spaCy's
`en_core_web_sm` (Kokoro's G2P) are baked into the image at build time. Each model loads on its first line and unloads after
`IDLE_UNLOAD_S` (600 s).

```bash
cd /d/Aaron/development/SkippyTel
docker compose stop tts-studio; docker compose rm -f tts-studio; docker compose up --build -d tts-studio
docker compose stop voice-gate; docker compose rm -f voice-gate; docker compose up --build -d voice-gate   # it joins tts-net
curl -s http://127.0.0.1:3007/health          # ..."voices":{"stock":true,"skippy":true,"elevenlabs":true}
python tools/tts_bench.py                     # time to first audio and real-time factor, through the real gate
```
