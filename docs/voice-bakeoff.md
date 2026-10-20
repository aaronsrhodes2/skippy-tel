# Voice bake-off: can skippy-pc speak Skippy locally? (2026-10-19)

The question: if skippy-pc (RTX 4070 Ti SUPER, 16 GB) did nothing but speak Skippy chat's spoken summaries, how close can a
local voice-cloning model get to what ElevenLabs gives the Captain today, and how fast is it?

**Verdict, short:** yes. **F5-TTS v1, zero-shot, on skippy-pc** speaks a summary line in about 2 s (RTF 0.39, 2 GB of VRAM)
in a voice a neutral speaker encoder puts at 0.94 against the source recording. Neither ElevenLabs voice comes close to that
(0.52 to 0.59): ElevenLabs isn't speaking as the narrator at all (next section). Qwen3-TTS sounds closest (0.96, fewest word errors)
but is 4x slower than real time with the reference `qwen-tts` code. The Captain's ears decide the rest: the blind page.

## What ElevenLabs actually speaks today (a finding)

The account has **no clone of the source recording**. The voices on it (names only; ids stay in the vault):

| vault variable | ElevenLabs voice | kind |
|---|---|---|
| `ELEVENLABS_SKIPPY_VOICE_ID` | "Arrogant Asshole" ("Male British lord baritone…") | **generated** (Voice Design, from a text prompt) |
| *(none: flask-api's fallback id `2EiwWnXFnvU5JabPnv8n`)* | "Kaelen - Amateur Warrior" | professional (library voice) |
| `ELEVENLABS_USER_VOICE_ID` | "Aaron Rhodes (me)" | cloned |

flask-api reads `ELEVENLABS_VOICE_ID` (`services/flask-api/app/config.py`), which the vault doesn't set, so `/tts/stream` (and
therefore voice-gate and the chat's speaker key) speaks with the fallback id, which ElevenLabs now resolves to "Kaelen", not the
designed Skippy voice. `tools/clone_skippy_voice.py` once made an instant clone from `state/skippy-voice-training.m4a`, but that
voice is no longer on the account. Not fixed here (out of scope); it is one line in config or the vault, and it's a follow-up.

So the blind page carries **both** ElevenLabs voices: the designed Skippy voice and the one the chat really plays.

## Method

- **Reference (what every local model heard):** 11.0 s cut from `state/skippy-voice-training.m4a`, the Captain's own source
  recording (the SkipCoin promo, Skippy in character, one voice, clean, no music), 1.05 s to 12.05 s, cut at a pause:
  *"Greetings, inferior beings. As you know, SkipCoin had a bit of a tumble. To those poor saps who lost their hard-earned jump
  change, well, I'd say I'm sorry, but let's face it, I'm not."* (transcript checked with the home Whisper). `state/skippy2.m4a`
  was rejected: book narration with other characters' voices in it. The cut lives in `state/voice-bakeoff/ref/` (not in git:
  personal-use voice).
- **No ElevenLabs audio was used as a reference or for training.** ElevenLabs' Prohibited Use Policy, §12 "Commercial and
  Technical Restrictions", bars "Using our Services or Output to develop, train, fine-tune, or improve any AI model or service"
  (checked 2026-10-19 at elevenlabs.io/use-policy). So a fine-tune must use the source recordings, never ElevenLabs renders.
- **Lines:** 10 typical Skippy summary lines with numbers and units (`tools/voice_bakeoff/lines.json`; the physics ones checked
  with Mentat: γ at 0.9 c = 2.294, so a clock at 44 %; Earth escape speed 11.19 km/s).
- **ElevenLabs:** rendered through flask-api's own client inside its container (model `eleven_v4`, the stored settings), timed from
  request to first byte and to the last byte (`tools/voice_bakeoff/el_render.py`). Raw: no voice-gate style tag, no FX polish.
- **Local models:** zero-shot, each in its own Docker image (`tools/voice_bakeoff/Dockerfile`, PyTorch 2.6 / CUDA 12.4), run with
  `--gpus all --network none` and the weights mounted read-only from `D:\models` (`tools/voice_bakeoff/run.sh`, harness
  `bench.py`). One warm-up line, then the 10. Ollama's qwen3:8b (6 GB) stayed resident on the same card throughout, which is the
  realistic shared case. F5-TTS's vocoder (Vocos, MIT, 54 MB) isn't in `D:\models`, so it is baked into that image at build time.
  Orpheus was skipped: its fine-tuned checkpoint speaks fixed voices and doesn't clone zero-shot.
- **Blind page:** `tools/voice_bakeoff/build_page.py` loudness-normalises every clip (EBU R128 −18 LUFS) and re-encodes them all
  the same way (MP3 96 kb/s mono 24 kHz), shuffles the letters per line, and embeds everything in one HTML file.

## Results (RTX 4070 Ti SUPER, beside Ollama)

Means over the 10 lines after a warm-up. **RTF** = render time / audio length (below 1 is faster than real time). **First audio**
= first streamed chunk where the model streams (XTTS, ElevenLabs), else the whole render. **VRAM** = torch's peak reserved.
**Same speaker** = cosine between mean Resemblyzer voice prints (a neutral GE2E encoder none of the contestants use;
`similarity.py`): about 0.8 and up is usually the same person, 0.5 to 0.7 a different one. **Word errors** = home Whisper's
transcript against the script, numbers normalised (`wer.py`; a few points of it are Whisper/normaliser noise, e.g. "1,284").

| voice | first audio | per line | RTF | VRAM | load | same speaker as the recording | same speaker as EL Skippy | word errors |
|---|---|---|---|---|---|---|---|---|
| ElevenLabs, Skippy voice (vault) | 0.74 s | 1.88 s | cloud | – | – | 0.59 | 1.00 | 2.5 % |
| ElevenLabs, what the chat plays | 0.69 s | 1.86 s | cloud | – | – | 0.52 | 0.67 | 2.5 % |
| **F5-TTS v1 Base** | 1.96 s\* | 1.96 s | **0.39** | **2.0 GB** | 67 s | 0.94 | 0.64 | 8 % |
| XTTS-v2 | **0.79 s** | 11.1 s | 0.89 | 4.9 GB | 57 s | 0.80 | 0.72 | 18 % |
| Chatterbox | 10.8 s\* | 10.8 s | 1.71 | 3.8 GB | 207 s | 0.93 | 0.67 | 8 % |
| Qwen3-TTS 1.7B Base (x-vector) | 30.4 s\* | 30.4 s | 4.35 | 4.3 GB | 96 s | **0.96** | 0.66 | **4 %** |

\* not streamed: the whole line is the first audio. Load times are dominated by reading the weights through Docker Desktop's
Windows bind mount (4 GB for Qwen3); a resident service pays them once.

What the numbers hide:

- **Qwen3, in-context mode (the default clone, with the reference transcript) is broken here:** every line came out starting with
  the reference's last words ("…let's face it, I'm not"), 30 % word errors, RTF 5.5. Padding the reference with 1 s of silence didn't
  help. The page uses `x_vector_only_mode` (speaker embedding only), which is clean. The in-context run is kept in
  `state/voice-bakeoff/out/qwen3-icl/` for the record.
- **Everything autoregressive is launch-bound on this box, not GPU-bound.** During the Qwen3 and Chatterbox runs the container held
  one CPU core at 100 % while the GPU sat at 20 % in power state P3. A micro-benchmark (`matmul+silu+add`, 3 small kernels) costs
  90 µs per step in Docker and 157 µs natively on Windows (the Windows display driver model adds roughly 30 µs per kernel launch,
  and Docker's WSL2 path is actually the faster of the two); captured as a CUDA graph it is 25 µs in both. So Qwen3 and Chatterbox
  are slow because of how their reference code launches kernels, and an engine that captures CUDA graphs (vLLM's Qwen3-TTS
  support, Chatterbox's compiled/streaming forks) is what would make them fast. That wasn't tried here.
- **XTTS** streams its first chunk in 0.8 s (the only local one faster than ElevenLabs' first byte) but rambles: three lines ran
  18 to 27 s for 6 s of text, repeating words ("congratulations, congratulations, second"). It is also the least like the recording.
- **F5** reads the speaking rate from the reference, and Skippy talks fast, so its lines are short (5.0 s mean vs ElevenLabs' 7.5 s).
  Its word errors are mostly numbers ("1,284", "0.9 c"); spelling numbers out before speaking would fix most of them.

## Recommendation

1. **Model: F5-TTS v1 Base, zero-shot.** It is the only local model that is both clearly faster than real time *without*
   engine work (2 s a line, so a summary of two or three sentences spoken one at a time starts in about 2 s) and close to the
   recording (0.94). 2 GB of VRAM leaves the card to Ollama. Its license is CC BY-NC 4.0: fine for personal use, which is all
   the cloned voice may be used for anyway. Watch **Qwen3-TTS**: it is the closest and the cleanest, Apache-2.0, and becomes the
   pick if it is served through an engine with CUDA graphs. That is a worthwhile second experiment, and a cheap one, since the
   weights are already in `D:\models`. Drop XTTS (rambles) and keep Chatterbox only as a fallback (MIT, close, but 1.7x slower
   than real time as packaged).
2. **Zero-shot, not fine-tune, for now.** Zero-shot already lands at 0.93 to 0.96 against the recording. A fine-tune only makes sense
   if the Captain's ears say the attitude or the prosody is missing, and then it must be trained on **source recordings only**:
   ElevenLabs' policy §12 rules out its outputs. The Captain has 85 s + 28 s of source audio; a fine-tune wants something like
   30 to 60 minutes of clean single-speaker Skippy, which means audiobook audio, which keeps it personal use.
3. **skippy-pc, not skippy-core.** These models run on CUDA, need 2 to 5 GB, and ran fine on skippy-pc beside Ollama's 6 GB model.
   skippy-core's Strix Halo is ROCm (most of these TTS stacks are CUDA-first) and is reserved for the LLM. If skippy-pc becomes the
   speech box, it is already where voice-gate, Whisper and flask-api live: the swap point is voice-gate's `tts_url`
   (`services/voice-gate/gate.py`), which today points at flask-api's ElevenLabs `/tts/stream`. A local engine there, a numbers-to-words
   pass, and one sentence at a time would give the chat local Skippy with no per-character budget.
4. **First, decide which Skippy.** The local clones sound like the **narrator**; the ElevenLabs voices are a designed character
   ("Arrogant Asshole") and a library voice. If the Captain prefers the designed ElevenLabs character, no local clone of the
   recording will match it, and copying it by cloning ElevenLabs output is what §12 forbids. That choice is his, on the page.

**Follow-ups (not done here):** flask-api speaks the wrong ElevenLabs voice (`ELEVENLABS_VOICE_ID` unset, see above); serving
Qwen3-TTS with CUDA graphs; a local-TTS engine behind voice-gate.

## Reproduce

```bash
# ElevenLabs (inside flask-api, so its key never leaves the container): skippy = the vault's Skippy voice, chat = what /tts/stream plays
docker cp tools/voice_bakeoff/lines.json skippytel-flask-api:/tmp/vb/lines.json
docker cp tools/voice_bakeoff/el_render.py skippytel-flask-api:/tmp/vb/el_render.py
docker exec -e PYTHONPATH=/app skippytel-flask-api python /tmp/vb/el_render.py skippy   # then: chat; docker cp /tmp/vb/. out/elevenlabs{,-chat}
bash tools/voice_bakeoff/run.sh build          # the four images
bash tools/voice_bakeoff/run.sh bench chatterbox f5 xtts
MSYS_NO_PATHCONV=1 docker run --rm --gpus all --network none -e QWEN_XVEC=1 -v D:/models/huggingface/Qwen__Qwen3-TTS-12Hz-1.7B-Base:/w:ro -v D:/Aaron/development/SkippyTel/state/voice-bakeoff:/vb voice-bakeoff-qwen3 qwen3
docker build --build-arg PIP=resemblyzer==0.1.4 -t voice-bakeoff-judge tools/voice_bakeoff
MSYS_NO_PATHCONV=1 docker run --rm --network none -v D:/Aaron/development/SkippyTel/state/voice-bakeoff:/vb --entrypoint python voice-bakeoff-judge /bench/similarity.py
python tools/voice_bakeoff/wer.py              # needs skippytel-whisper on 127.0.0.1:8000
python tools/voice_bakeoff/build_page.py       # -> state/voice-bakeoff/skippy-voice-bakeoff.html (personal-use audio: not in git)
```
