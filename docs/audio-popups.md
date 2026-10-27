# Audio pop-ups: production helpers for the Captain's tracks (2026-10-24)

Five models in one container, `services/audio-popups/`, on skippy-pc's GPU. Each model loads on its first job and the service
unloads after five idle minutes (then exits, so Docker's restart brings back a ~50 MB process). They are pop-ups (docs/popups.md):
patient jobs, chat tools for the Captain, and `SKIPPY_POPUP:` lines for the private Skippy. Full songs are NOT here: they stay with
ACE-Step's `make_music`.

| tool | model | what it gives | licence for personal use |
|---|---|---|---|
| `sfx_quick` | Stable Audio Open Small | a 0.5-11 s hit or FX, WAV | Stability AI Community Licence: free (personal, and commercial under $1 M revenue) |
| `sfx_loop` | Stable Audio Open 1.0 | a tempo/key loop or one-shot up to 47 s, cut to whole bars, WAV | same |
| `melody_variations` | MusicGen stereo-melody (1.5 B) | the attached clip's melody in a new style, up to 30 s, WAV | **CC-BY-NC 4.0: sketching only, not in a release** |
| `stems` | Demucs v4 htdemucs_ft (htdemucs for `fast`) | drums/bass/vocals/other as a zip of mp3s, or one stem | MIT |
| `audio_to_midi` | Spotify Basic Pitch | a .mid of the attached clip (polyphonic, bends) | Apache 2.0 |

(T5-base, the Stable Audio text encoder, is Apache 2.0.)

## Verdicts

Run 2026-10-27 on skippy-pc (16 GB Docker VM), every resident model left up. Measured, not listened to: lengths, levels and note
counts are checked below; how good each one *sounds* is the Captain's call (the files are in `state/audio-popups/`).

| tool | verdict | why |
|---|---|---|
| `sfx_loop` | **ship** | 9-16 s a loop once loaded, exactly the bars asked for at the tempo asked; the first call of a session pays a ~5.5 min load |
| `melody_variations` | **ship, sketching only** | 15 s in 36-51 s warm; CC-BY-NC, so never in a release; first call ~4 min (load) |
| `stems` | **ship** | a whole 4-min track in 39-52 s, four real stems; `ft` is the default |
| `audio_to_midi` | **ship** | 8 s warm on a stem (CPU); best on one instrument (the bass stem), as producers use it |
| `sfx_quick` | **not run** | Stable Audio Open Small is gated: the Captain accepts its licence on Hugging Face once, then it seeds like the others |

Cold loads are the cost: Stable Audio is built on the card one tensor at a time (so the Docker VM never holds it), and MusicGen loads
4 GB. Both stay loaded for IDLE_UNLOAD_S (5 min) after the last job, so a session of several asks pays the load once.

## Method

- **Machine:** skippy-pc, RTX 4070 Ti SUPER 16 GB, Docker Desktop's WSL VM (16 GB RAM for the whole stack since 2026-10-25, see "The Docker VM").
- **As a real job runs:** the resident voices
  (tts-studio), Whisper and other families' resident pop-ups stayed up. The card's baseline without Ollama was 6.7-10.4 GB used,
  depending on what the other sessions held.
- **Real material:** Moby, "Porcelain" (from the Captain's library, 4 min): the whole track for stems, its first 15 s (the piano
  intro) for melody and MIDI, and the Demucs bass stem back into MIDI (the producer's chain: split, then transcribe the bassline).
- **Real prompts** from electronic production (`services/audio-popups/bench.py`): 808 kick, closed hat, plate snare, riser,
  impact, glitch; tech-house hats 128 BPM A minor, DnB break 174, deep-house chord stabs 124 F minor, dubstep wobble 140 E minor,
  Juno arp 120 C minor, lo-fi drums 90, ambient pad D minor; melodic techno, deep house, synthwave for the melody.
- **Figures:** *work* = the service's own clock (X-Seconds), *wall* = what a job waits, the first call of each model includes its
  load (cold); *VRAM* = torch's peak reserved in the service (X-Peak-VRAM-MB); *card* = the whole card's peak from nvidia-smi.
- **Samples:** `state/audio-popups/` on skippy-pc (not in git: generated audio and stems of a commercial track).

## Results

*work* = the service's own clock, *wall* = what the job waited (the first row of each model includes its load), *VRAM* = the
service's torch peak, *card* = the whole card's peak (16 GB card, the other residents included).

| tool | ask | work s | wall s | VRAM MB | card MB | check |
|---|---|---:|---:|---:|---:|---|
| sfx_loop | tech-house hats, 128 BPM, 4 bars (cold) | 349.3 | 350.2 | 7796 | 15035 | 7.50 s = 4 bars at 128 |
| sfx_loop | DnB break, 174, 4 bars | 16.2 | 17.7 | 8828 | 15015 | 5.52 s |
| sfx_loop | deep-house chord stabs, 124, F minor | 11.3 | 11.3 | 8834 | 14985 | 7.74 s |
| sfx_loop | dubstep wobble, 140, 2 bars, E minor | 15.2 | 15.3 | 8840 | 14993 | 3.43 s |
| sfx_loop | Juno arp, 120, C minor | 8.9 | 9.7 | 8840 | 14999 | 8.00 s |
| sfx_loop | lo-fi drums, 90, 2 bars | 8.8 | 9.6 | 8840 | 14996 | 5.33 s |
| sfx_loop | ambient pad, 20 s, D minor | 8.9 | 10.0 | 8840 | 14990 | 20.00 s |
| melody_variations | melodic techno, 15 s (cold) | 254.2 | 252.3 | 8840 | 14990 | 15 s, 32 kHz stereo |
| melody_variations | deep house | 50.5 | 52.0 | 4150 | 10285 | 15 s |
| melody_variations | synthwave | 35.5 | 38.3 | 4150 | 10258 | 15 s |
| stems | Porcelain, whole track, `fast` (cold) | 51.5 | 49.8 | 4150 | 11965 | 4 stems |
| stems | Porcelain, whole track, `ft` | 38.6 | 39.1 | 2824 | 10748 | drums -17.1, bass -17.4, other -17.9, vocals -25.3 dB |
| audio_to_midi | the piano intro, 15 s (cold) | 54.0 | 53.7 | 2824 | 10814 | 132 notes |
| audio_to_midi | the `ft` bass stem | 8.3 | 8.5 | 2824 | 10756 | 226 notes |

Every loop is exactly its bars at its tempo; nothing is silent or clipped (peaks are normalised to -1 dBFS). The card peaked at
15.0 GB of 16 with Stable Audio loaded beside the residents: it fits, with little to spare, so the reporter's GPU lock (one pop-up
job at a time) matters.

## The Docker VM

Raised to 16 GB on 2026-10-25 (`.wslconfig`); this benchmark ran on it. Before that, Docker Desktop's VM was capped at 8 GB (`.wslconfig`), and with the home stack plus every family's resident pop-up it sits near
7.5 GB. A Stable Audio Open 1.0 load (container capped at 4 GB, at 1.4 GB when it fell) crashed the whole VM on 2026-10-24; the
stack came back by itself. A container `mem_limit` can't prevent that: the VM itself runs out. Since then the service builds the
Stable Audio models directly on the card (one tensor at a time through the VM), and exits when idle. The container's own cap is now 6 GB.

## Notes

- **Inputs through the chat are ~1 MB** (the framework's cap): about a minute of mp3. Stems of a whole track need the native route
  (`POST /stems` multipart, any length up to 12 min) or a bigger input path (a framework follow-up).
- **Stable Audio Open Small is gated** on Hugging Face: the vault token's account must accept its licence once
  (https://huggingface.co/stabilityai/stable-audio-open-small), then `python tools/models_archive.py hf
  stabilityai/stable-audio-open-small --ignore "*.ckpt"` and `bash services/audio-popups/seed_weights.sh`.
- **Mel-Band / BS-RoFormer** was not adopted: the clearly better RoFormer checkpoints are vocal-only or community 6-stem models of
  unclear licence; for four stems with a clean licence, htdemucs_ft is still the reference (MUSDB SDR ~9.0).
- Demucs' checkpoints are pickles (`weights_only=False`): they come from Meta's CDN and match the sha256 prefixes in demucs'
  `files.txt` (checked when archived).
