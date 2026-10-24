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

## Re-run, 3 lines (2026-10-23, after the voice id fix)

flask-api now reads `ELEVENLABS_SKIPPY_VOICE_ID` first, so the chat plays `w7zF…`. Checked against ElevenLabs before comparing:
it is **"Arrogant Asshole", category `generated`** (Voice Design), **not a clone** of any recording. It is the same voice the
first run already had as "ElevenLabs · Skippy voice (vault)", so that run's ElevenLabs Skippy column stood; only the "what the chat
plays" column was the wrong voice. Lines 1 to 3 of `lines.json`, same reference, same images (rebuilt so `N_LINES`/`OUT` work).
ElevenLabs through flask-api's own client; the local engines with `--memory=4g` per container (see the crash below).
Page: `state/voice-bakeoff/rerun3/skippy-voice-rerun.html` (`tools/voice_bakeoff/rerun_page.py`), sent to the Captain.

| engine | first audio | total | audio | RTF | RTF, first run | VRAM (model) | whole card in use (peak) | GPU busy (max) |
|---|---|---|---|---|---|---|---|---|
| ElevenLabs (the chat's voice now) | 0.81 s | 2.04 s | 7.1 s | 0.29 (cloud) | – | – | – | – |
| F5-TTS v1 | 2.30 s | 2.30 s | 4.7 s | 0.49 | 0.41 | 2.0 GB | 15.6 GB | 87 % |
| XTTS-v2 | **0.72 s** | 9.36 s | 12.1 s | 0.77 | 0.91 | 3.1 GB | 10.9 GB | 88 % |
| Chatterbox | 13.15 s | 13.15 s | 6.7 s | 1.97 | 1.70 | 3.8 GB | 12.1 GB | 100 % |
| Qwen3-TTS 1.7B (x-vector) | 74.84 s | 74.84 s | 7.1 s | 10.47 | 4.57 | 4.3 GB | 15.4 GB | 100 % |

"RTF, first run" = the same three lines in the 2026-10-20 run, with only Ollama's model on the card. This time the card was shared:
`skippytel-image-studio` / `image-studio-next` (the faster-pictures session) held SDXL and were generating during the F5,
Chatterbox and Qwen3 runs, so those three are slower than on a quiet card; Qwen3 is hurt most (its line 3 took 126 s). Whisper heard
every line, except that F5 dropped "The tests" and garbled "1,284", and Chatterbox dropped "Done.". The conclusion does not change:
F5 is the local pick at about 2 s a line; nothing local matches ElevenLabs' 0.8 s to first audio except XTTS, which rambles.

**What a shared card does to skippy-pc as a speech box:** the TTS model must not be one of several big GPU jobs. Ollama, SDXL
and a TTS model don't fit together in 16 GB without spilling, and a picture job at 100 % GPU doubles F5's time per line and
more than doubles Qwen3's. A speech box needs its few GB reserved (or the picture studio parked while the chat speaks).

**Docker Desktop crashed during this re-run** (2026-10-23 ~13:19): while the XTTS container was loading, with image-studio holding
SDXL, the engine went away (`error waiting for container: unexpected EOF`; the npipe vanished) and came back about a minute later
with the whole stack. The likely cause is the Docker VM's 7.75 GB of RAM running out (the XTTS checkpoint is 1.9 GB and loads on the
CPU first), not proven. Afterwards flask-api answered nothing for several minutes: both gunicorn workers sat in `p9_client_rpc` on
`/music/…` (a startup scan of the music library through the Windows bind mount, slow on a cold cache), and it recovered by itself.
The remaining runs used `--memory=4g`, so an out-of-memory kill would take the bench container, not the engine.

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

## Cloned from the ElevenLabs print, with settings (2026-10-23)

The Captain listened to the re-run ("ElevenLabs perfect; F5 not bad at all, normal gait and accent; XTTS slow-motion; Chatterbox
fast but lost the accent; Qwen3 good but unacceptable time") and decided the local engines clone **ElevenLabs' Skippy voice**, not
the narrator recording. His reasoning, for the record: "we are not training a model, we are just getting a voice print that we
created from our own prompting", "and it is not a service we are providing to anyone for profit". (The §12 note above is why it is
written down.) The print: `state/voice-bakeoff/ref-el/ref.wav`, 9.25 s, take 2 of three renders of a fixed line through flask-api
(`w7zF…`, `eleven_v4`), cut at the second sentence; transcript in `ref.txt`, checked word for word with Whisper.

Three settings per engine, 3 lines (`SWEEP` in `bench.py`: one model load, one pass per setting), every take run through the chat's
own polish chain (`polish_noise.py` = voice-gate's `fx_chain_for`). Qwen3 left out (too slow). `elref_report.py` builds the table
and `state/voice-bakeoff/elref/skippy-voice-elref.html`.

| engine | setting | first audio | total | RTF | word errors | like ElevenLabs | pause noise raw → polished |
|---|---|---|---|---|---|---|---|
| ElevenLabs | the chat's voice | 0.81 s | 2.04 s | cloud | 0 % | 1.00 | −86 → −94 dB |
| **F5-TTS** | **nfe16** (16 steps) | **0.59 s** | **0.59 s** | **0.10** | 4 % | 0.93 | −79 → −84 dB |
| F5-TTS | cfg3 | 1.18 s | 1.18 s | 0.20 | 6 % | 0.92 | −83 → −83 dB |
| F5-TTS | default (32 steps) | 3.57 s\* | 3.57 s | 0.60 | 8 % | 0.92 | −83 → −83 dB |
| XTTS-v2 | default | **0.45 s** | 3.39 s | 0.51 | 0 % | 0.90 | −54 → −58 dB |
| XTTS-v2 | speed 1.2 | 0.43 s | 4.15 s | 0.60 | 0 % | 0.91 | −62 → −63 dB |
| XTTS-v2 | speed 1.2, temp 0.5 | 0.43 s | 2.90 s | 0.54 | 2 % | 0.88 | −55 → −57 dB |
| Chatterbox | default | 6.08 s | 6.08 s | 0.84 | 0 % | 0.93 | −92 → −93 dB |
| Chatterbox | exag 0.7, cfg 0.3 | 5.87 s | 5.87 s | 0.85 | 7 % | 0.93 | −82 → −84 dB |
| Chatterbox | exag 0.4, cfg 0.8 | 5.88 s | 5.88 s | 0.84 | 8 % | 0.93 | −77 → −81 dB |

\* one slow first line (7.8 s) while the picture studio had the GPU; its other two took 1.2 and 1.7 s.
"Like ElevenLabs" = Resemblyzer cosine against the 10 ElevenLabs lines plus the print (`sim_dirs.py`); "pause noise" = 10th-percentile
20 ms frame level, dBFS, lower is cleaner.

What changed with the print:
- **The background noise came from the reference.** Cloned from the clean ElevenLabs print, F5 and Chatterbox are as quiet in the
  pauses as ElevenLabs itself (−80 to −93 dBFS, against −51 to −66 from the narrator recording). XTTS stays noisy (−54 to −63).
- **XTTS's slow motion was the reference too:** its lines are now normal length, and its first streamed audio (0.4 s) beats ElevenLabs.
- **F5 at 16 flow steps** speaks a whole line in about 0.6 s, sooner than ElevenLabs' first byte, at 4 % word errors and 0.93.
  That makes it the pick: it needs no streaming to feel instant for one-sentence-at-a-time summaries.

**Two Docker Desktop crashes, both while XTTS loaded on a nearly full card** (13:19 and 14:32; image-studio held SDXL, card at 15+ GB).
The `--memory=4g` cap didn't prevent the second, so it isn't RAM; the likely trigger is GPU memory oversubscription under WSL. XTTS
loaded cleanly both times the card had room. Each crash took the home stack down for a minute, plus a few minutes of flask-api's
cold `/music` scan. **The Captain's decision (2026-10-23):** image generation moves to skippy-core ("quality over speed"; it can
finish late and show up in the chat later); voice keeps reserved GPU on skippy-pc ("we do care about speed and quality").
Forwarded to the faster-pictures session, which owns image-studio.
