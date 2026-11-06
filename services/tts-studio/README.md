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

## Where a line's time goes (2026-11-05, "snappy like Amazon Echo"; RTX 4070 Ti SUPER, WSL2, the card shared with the desktop)

One F5 run is 16 flow steps over the 9 s reference (850 frames) plus the new words, and each chunk of a reply is one full run. In a throwaway container
beside the live one (the same image, the same weights):

| piece of one run | time |
|---|---|
| the 16 flow steps (the DiT) | the floor is 0.45 to 0.65 s even for one word: ~800 small ops a step, ~28 ms of CPU launching per step, so a short line is launch-bound and a long one GPU-bound (graphs gain 5% on a 9 s line, 31 to 35% on a 1 to 2 s one) |
| the same run when the card is busy | one line, same seed and shape, 32 calls: 0.59 to 1.66 s, median 0.9 s. Not the idle gap (30 s idle = the same), not the GPU clock (2760 MHz, P2, always), not a length seen before (no first-sight cost) |
| reference preprocessing + decode + seed + upload | 3 + 5 + 1 + 1 ms per line, and a disk read of `/ref/ref.wav`: now once at load (`Skippy._prepare`, bit-identical audio for the same seed) |
| vocoder / mel / pinyin / copies | 3 / 1 / 0 / 0 ms |
| MP3 (in-process LAME) | 6 ms per second of audio (65 ms for a 9 s line) |
| `ready()` (7 stat() calls on bind mounts) | 4 ms per line and per health check. A warm resident voice costs ONE `stat()` of the file its next line may still read (`line_file()`: the Skippy voice's `ref.wav`, the stock voice's pack), so a vanished reference is still "not ready" |
| voice-gate cache bookkeeping | was 319 ms per new line, now 2 ms (see `services/voice-gate`) |

`"whole": true` on `/speak` (the gate forwards it; `TTS_WHOLE=1` in the gate asks for it on every line) renders a reply that fits one F5 batch (192 characters for this
reference) as ONE run instead of one per sentence: a 2 to 3 sentence reply 0.8 to 1.1 s faster in a busy stretch and 0.35 s in a quiet one, the same audio length, and
the hub skips its ffmpeg re-encode of joined MP3 pieces (about 0.1 s). Nobody reads this service's stream early (the gate buffers it, the hub and the pages read it
whole), so the lone first sentence buys nothing today. The service's own default is off because it moves where the pauses fall; the house hub asks for it on every reply (`services/satellite/voicegate.py`, `Gate.tts` sends `"whole": true`; one line there turns it off), and the Captain's ear decides whether it stays.
Whatever the length, nothing is dropped or cut (`tests/test_sat_fix_tts.py`): a reply that fits one batch is one run; a longer one is several runs in order, each at most one
batch; a single sentence longer than a batch goes to F5 whole, as it always did, and F5 splits it inside the run (this service never cuts it).

Measured and NOT applied (speed only, quality untested): `F5_NFE` 12 / 10 / 8 = -21 to -25% / -38 to -44% / -50 to -55% per run; a 5.4 s reference (507 frames) = -16 to -20%;
both (5.4 s and 12 steps) = -30 to -40%. CUDA graphs for the steps (same kernels, bit-identical audio) = -31 to -35% on a 1 to 2 s line once its length has been captured,
but the first line of each length pays +0.9 s to capture and ~70 MB of card each, so it only pays for lengths that come back (the hub's short confirmations).

```bash
cd /d/Aaron/development/SkippyTel
docker compose stop tts-studio; docker compose rm -f tts-studio; docker compose up --build -d tts-studio
docker compose stop voice-gate; docker compose rm -f voice-gate; docker compose up --build -d voice-gate   # it joins tts-net
curl -s http://127.0.0.1:3007/health          # ..."voices":{"stock":true,"skippy":true,"elevenlabs":true}
python tools/tts_bench.py                     # time to first audio and real-time factor, through the real gate
```
