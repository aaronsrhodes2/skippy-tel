# tts-studio

Skippy chat's local voices on skippy-pc's GPU, reached only by `voice-gate` over the internal `tts-net` (no route out, no published port).
`app.py`'s docstring is the authority on routes. Which voice a person may hear is the portal's pass, checked by voice-gate, never this service.

| voice | model | who | license |
|---|---|---|---|
| `stock` | Kokoro-82M, `STOCK_VOICE` (default `bm_george`) | everyone who may chat (Public) | Apache-2.0 |
| `skippy` | F5-TTS v1 Base at 16 flow steps, zero-shot from `state/tts-studio/ref/{ref.wav,ref.txt}` | the Captain (Private) | CC BY-NC: personal use |

The reference is the 9 s print of the Captain's voice (`state/voice-bakeoff/ref-el`, his pick on 2026-10-23: the
local voice should sound like the Skippy he designed; the earlier narrator cut is kept in `state/tts-studio/ref-narrator`). Not in git.
Weights are mounted read-only from `D:/models`; Vocos (F5's vocoder) and spaCy's `en_core_web_sm` (Kokoro's G2P) are baked into the
image at build time. Both voices are **resident** (`RESIDENT`): loaded and warmed at start, never unloaded, and the process then holds
`VOICE_VRAM_GB` (3) of the card so a picture or song job can't squeeze the voice out. `/health` says a resident voice is ready only
once it is warm, and voice-gate returns 503 while it is loading. F5 gets numbers in words first (`spoken_numbers.py`: "1,284", "0.9 c", "11.2 km/s", "44%", dates, times).

## Measured (2026-10-23, RTX 4070 Ti SUPER beside Ollama's qwen3:8b; `tools/tts_bench.py`, through the real gate)

Means over 6 summary-length lines (71 to 103 characters) after one warm-up line. First audio = the first streamed bytes at the
gate; RTF = whole reply / audio length.

| voice | first audio | whole reply | audio | RTF | cold load (busy D:) |
|---|---|---|---|---|---|
| stock (Kokoro, bm_george) | **0.21 s** | 0.32 s | 7.0 s | **0.048** | 118 s + 35 s first-line setup (now at start, before /health says ready) |
| skippy (F5 zero-shot; then 32 steps and the narrator reference, now 16 and the Captain's print) | **1.23 s** | 1.60 s | 4.9 s | **0.34** | 67 s + 17 s first-line setup (now at start, before /health says ready) |
MP3 is encoded in-process by libsndfile: an earlier version spawned ffmpeg per chunk, and on a busy disk that spawn alone took 3 to
60 s. The cold loads were measured while other containers were reading D: hard; on a quiet disk expect them much shorter.

```bash
cd /d/Aaron/development/SkippyTel
docker compose stop tts-studio; docker compose rm -f tts-studio; docker compose up --build -d tts-studio
docker compose stop voice-gate; docker compose rm -f voice-gate; docker compose up --build -d voice-gate   # it joins tts-net
curl -s http://127.0.0.1:3007/health          # ..."voices":{"stock":true,"skippy":true,"elevenlabs":true}
python tools/tts_bench.py                     # time to first audio and real-time factor, through the real gate
```
