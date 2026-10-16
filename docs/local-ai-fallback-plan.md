# Home AI box: offload, fallback, archive, own voice (the plan in full)

Status: **plan; nothing bought or built yet** (consolidated 2026-10-16). Facts marked "reported" come from web searches that day and need re-checking;
numbers marked "estimate" are my arithmetic. Related: `docs/v2/CHARTER.md` §10 question 1 (this answers it: option (b), a dedicated always-on Core box).

## 0. Where things stand

| Item | State |
|---|---|
| The box | **Recommended: Bosgame M5, 128 GB / 2 TB, $3,599** (Amazon, free returns, 3-yr parts warranty). The Captain has not ordered. Budget was $3,500, "a little higher" allowed. |
| OS | Ubuntu 26.04 LTS, headless (decided in principle). |
| Voice capture kit | Written and tested (`tools/voice_capture.py`), **not yet run** (needs skippy-pc and the vault key). |
| ElevenLabs | Purchased credits, ~8,500 left (not a subscription); the API and the Skippy clone were answering on Oct 14. |
| Everything else | Plan only. |

## 1. Goals and non-goals

1. **Contention.** The gaming PC (RTX 4070, 12 GB) is the dev machine, game/stream host and LLM host. Ollama keeps two models resident
   (`OLLAMA_KEEP_ALIVE=-1`), so VRAM fights Sunshine, games, ACE-Step and Whisper. `reporter.py` even unloads Ollama before a song to cope.
2. **Fallback.** Hosted frontier models can become dearer, slower, deprecated or policy-restricted. A *good-enough* local model keeps Skippy and the coding loop alive.
3. **Archive.** Pinned weights, pinned software and a recorded baseline: "this moment in software history".
4. **Own voice.** Skippy's voice that no longer depends on a paid service.

Non-goals: matching Sonnet 5.5 on long-context comprehension or speed (it won't); replacing the 4070 by assumption (placement is decided by measurement, section 5).

## 2. Target architecture

**The Halo becomes the Core box**: always-on Linux running SkippyTel's brains and the models. **The gaming PC** keeps development, games, Sunshine, and whatever is
CUDA- or Windows-bound. Development stays on the PC; only inference is remote (over Tailscale).

### 2.1 What moves, what stays (from the compose files and a grep for hard-coded paths; not a full audit of every route)

| Moves cleanly (CPU, Docker, mostly env-configured) | Stays on the PC (CUDA, Windows, attached hardware) | Decide by measurement |
|---|---|---|
| `flask-api`, `home-reporter`, `mentat-home`, `wa-trickle`, `voice-gate`, `video-gate`, `music-gate`, Dozzle, Uptime Kuma; the LLM (`ollama` -> llama.cpp/Ollama on the iGPU) | `claude-bridge` (Workshop: Windows paths, `claude` login, dev clones), the starter (Windows service manager), the UPS monitor (WMI), Jellyfin (`D:\Movies`), Sunshine and games | Whisper (CUDA today), Stable Diffusion, ACE-Step music, a local voice engine |

### 2.2 The real obstacles

1. **The vault.** Compose uses `env_file: G:/My Drive/vault/.env`, Google Drive's virtual drive: not on Linux. The Halo needs a synced or copied `.env`, tight permissions, never committed.
2. **Data on `D:`.** The music library (`D:/Aaron/Music`, mounted into `music-gate` and `flask-api`) and `D:\models` must be copied to the Halo or mounted over the network.
3. **Cross-host addressing.** Containers now reach each other by compose names and `host.docker.internal`; across two machines they need LAN/tailnet addresses. Many are env vars
   already (`OLLAMA_URL`, `MUSIC_URL`, `CLAUDE_BRIDGE_URL`, `JELLYFIN_URL`, `WHISPER_URL`); the rest must be found (the UPS URL is one).
4. **Funnel doors and the home link.** Gate public URLs sit under the PC's tailnet name and change with the move (`*_GATE_PUBLIC` in the portal). The portal expects one home reporter:
   it moves to the Halo and probes the PC's services over the network.
5. **GPU reservations.** The compose files hard-code `driver: nvidia`; the Halo needs its own compose file passing through `/dev/dri` (and `/dev/kfd` for ROCm).
6. **Recovery.** The starter restarts the stack from the portal, on Windows only. On Linux, `restart: unless-stopped` plus systemd covers reboots; the portal's Recovery card still targets the PC.

Plus: the Halo becomes Skippy's single point of failure (a small UPS for it), and a PC that is off or gaming no longer takes Skippy down (a gain). The reporter already shows an absent service as "not ready".

## 3. Hardware and purchase

Prices (reported 2026-10-16; the sandbox could not open vendor pages, so verify): all use the same Ryzen AI Max+ 395, so inference speed should match; differences are price,
warranty, cooling, ports. Do **not** buy a 64 GB tier (the 120B-class models need ~66 GB plus context).

| Machine (128 GB) | Price | Notes |
|---|---|---|
| **Bosgame M5, 2 TB** (Amazon, pasted listing) | **$3,599.00** | 4.3 stars (635), free returns, 1 yr full + 3 yr parts warranty. Dual USB4, one 2.5GbE, Wi-Fi 7. **Cooling not yet checked.** |
| GMKtec EVO-X2, 2 TB (Amazon) | $3,649.99 | 1 yr limited. (Vendor store: 1 TB at $3,499.99, a price rise announced.) |
| GMKtec EVO-X3, 2 TB (Amazon) | $3,799.99 | OCuLink, better-reported cooling; the "$5,499.99 list / 31% off" is theater; reviews say software still needs work. |
| AMD Ryzen AI Halo, 2 TB | $3,999.99 | AMD-supported dev platform, Linux SKU, Micro Center in-store only. |
| Others | $3,449-$4,349 | Framework Desktop (stock?), Minisforum MS-S1 Max, Beelink GTR9 Pro. |

**Pick: the M5** unless a thermal/noise review or the burn-in says otherwise; the X3 only if the Captain wants the cooling/OCuLink at ~$200 more; the Halo for vendor support at ~$400 more.
Sales tax will push any of these past the sticker. Keep ~$150-250 aside for an archive drive and think about a UPS (neither is in the $3,500).

**Burn-in inside the return window (before wiping Windows or relying on it):**
1. Confirm the OS sees 128 GB and 2 TB; note the BIOS version and the iGPU memory (UMA) setting. Set a reminder two days before the return deadline.
2. Run everything from a Linux USB stick or external SSD, so the internal drive and its Windows stay untouched until you decide to keep it. Check Wi-Fi/2.5GbE, iGPU, SSD.
3. llama.cpp (Vulkan) `llama-bench` on gpt-oss-120b and Qwen3-30B-A3B in a loop for hours: tokens/s over time, temperatures, fan noise, throttling.
4. Placement tests (section 5). Pass: speeds hold near baseline, no throttle cliff, no crashes. Fail: return it.

## 4. Software stack

- **OS:** Ubuntu 26.04 LTS headless (AMD's ROCm packages target Ubuntu LTS first; a published faster-whisper-on-Strix-Halo recipe uses 26.04 + ROCm 7.2.2); Fedora 43 is the alternative the LLM
  benchmarks used. Use the in-kernel amdgpu driver, not DKMS (an earlier test saw DKMS builds fail on a newer kernel); check gfx1151 on AMD's ROCm compatibility page at install time. Record kernel/Mesa/ROCm versions.
- **LLM inference:** `llama.cpp` / `llama-server`, **Vulkan (RADV) first** (needs no ROCm); ROCm HIP as the alternative (HIP often wins prompt processing, Vulkan often wins generation; measure both). Pin the build.
  `llama-swap` or a systemd unit per model to switch models. Skippy's default tier may stay on the Ollama API if Ollama runs well on this chip; otherwise add a small OpenAI-compatible client next to `clients/ollama.py`.
- **ROCm is only needed for** ComfyUI, Whisper and ACE-Step: the immature side is contained to the "decide by measurement" items.
- **Network:** Tailscale only; `llama-server --api-key` so only tailnet holders of the key call it. k8s/Docker Swarm: skip (one node; compose is the repo's idiom).
- **Ops:** compose/systemd on the Halo; the reporter reports its health like any capability.

## 5. Models and workloads

| Role | Model | Evidence |
|---|---|---|
| Coding agent / fallback | **Qwen3-Coder-Next** (80B, 3B active, Apache 2.0, ~46 GB) | ~42.7 tok/s measured on Strix Halo (reported) |
| General, tools, Skippy "big" tier | **gpt-oss-120b** (~66 GB, US-origin) | ~46-53 tok/s (reported) |
| Fast chat / summaries / routing | **Qwen3-30B-A3B** | ~86 tok/s (reported) |
| Avoid | Dense 70B-123B models | ~215 GB/s bandwidth caps them near 5 tok/s (measured ~4.6-5.3 for a 70B); a 70B Q8 would cap near 3 (estimate) |

| Workload | Halo plan | Confidence | Notes |
|---|---|---|---|
| Chat / Skippy tiers | llama.cpp or Ollama on the iGPU | high | |
| Coding fallback | Qwen3-Coder-Next via Claude Code | medium | Useful, **not equivalent**: vendor figures 70.6% SWE-Bench Verified (SWE-Agent), 44.3% SWE-Bench Pro vs Haiku 5.5's published 64.8 on Pro (no Verified published). Not like-for-like, but "below Haiku on hard tasks". The bake-off decides. |
| Speech to text | faster-whisper on ROCm | medium-high | ~9x real time reported; needs ROCm >= 6.4.3 images (6.1 lacks gfx1151 and silently falls back to CPU) |
| Text to speech | unchanged at first; own voice (section 7) | low for a local clone | not GPU-bound today |
| Image generation | ComfyUI on ROCm | medium | SDXL-class works; FP8 Flux-class ~6 min/image reported; some models crash; `--force-fp32` fixes NaN output on gfx1151. Diffusion is compute-bound: the 4070 may be faster |
| Music (ACE-Step 1.5) | unknown | low | no ROCm report found; the service's notes say plain PyTorch, so it may run. Test, else it stays on the 4070 |

**Memory/bandwidth (128 GB shared).** The two big models together (112 GB) cannot be resident with a KV cache, the OS and a diffusion model: keep a small resident set (fast chat model + Whisper), load big models on demand,
and unload them before image/music jobs (what `reporter.py` already does before a song). Concurrent jobs share bandwidth, so token speed drops. A coding agent's 40K-token first turn at ~850-1,000 tok/s prefill is ~40-50 s (estimate).

**Placement by measurement.** Run the same jobs on both machines: SDXL 1024x1024 at 30 steps, a 60 s ACE-Step song, Whisper large-v3 on 10 min of audio, one coding task, TTS first-audio latency. A workload moves to the Halo
only if it lands within about 2x of the 4070's time and frees the PC; otherwise it stays.

## 6. Coding fallback and editors

- Claude Code can be pointed at a local server: `llama-server` speaks the Anthropic Messages API (`/v1/messages`, streaming, tools) natively, so `ANTHROPIC_BASE_URL=http://halo:8080 claude --model <alias>` needs no proxy.
- The documented redirect is the `env` block in `~/.claude/settings.json` (or a project's `.claude/settings.json`), described for the **CLI and the VS Code extension**. **Claude Desktop: no external-model setting found; treat as unsupported until a day-one test shows otherwise.**
  Put the fallback in a separate `claude-local` wrapper or settings file, not the global one (a token can bypass `/login`); variables are read at start-up. Built-in web search is reported unavailable with third-party models.
- **Cursor:** custom endpoints go through Cursor's servers (localhost never works; needs a public tunnel) and only the chat panel honours them. Not recommended. Alternatives: Cline, OpenCode, Continue, Aider.
- **The Workshop's model is hard-coded on purpose** (`workshop_engine.py`: "no environment variable and no request field", asserted by tests). So the local fallback is a **separate mode the Captain switches on by hand**, never a silent failover; the default stays Sonnet 5.5.
- Context >= 32K (ideally 64K+); Claude Code's per-request attribution header can defeat the KV cache on local servers: check and neutralise it.
- Day-one test: the same task in (a) `claude-local` in a terminal, (b) the VS Code extension with a project `.claude/settings.json`, (c) Claude Desktop; the `llama-server` request log shows which reaches the Halo.

## 7. Own voice for Skippy (ElevenLabs as teacher, a local single-voice model as student)

**Plumbing is one seam:** `voice-gate` and the Android app call flask-api's `/tts` -> `clients/elevenlabs_client.py` (streams mp3). A local engine plugs in behind that route; `voice-gate`'s ffmpeg chain takes mp3, so WAV only needs converting.
`intent.py`/`health.py` also mention ElevenLabs (not yet read). **The hard part is the voice:**
- ElevenLabs v3 emotion tags (`[sighs]`, `[chuckles]`, `[laughs]`, `[sarcastic]`, `[whispers]`, `[excited]`, ALL CAPS, "...") need a per-engine mapping (translate or strip).
- The cloned reference is rough/noisy (the repo measures HNR ~3 dB); fine-tuning on clean teacher data fixes more than zero-shot cloning can.
- **Latency bar to beat** (Captain's request log, 66 calls Oct 10-14, all 200): median 0.33 s, p90 0.46 s, max 0.87 s (assuming the log's latency is time to response start).
- Candidates (licences from roundups; verify): Chatterbox (MIT), Orpheus 3B (Apache 2.0 per some sources, emotion tags), Qwen3-TTS (Apache 2.0 per one list); XTTS-v2 and F5-TTS are non-commercial (fine for strictly personal use). Kokoro (`services/tts`) cannot clone.

**Pipeline:**
1. *Source and clean locally (free):* isolate speech and denoise with local tools (options: Demucs, DeepFilterNet, Resemble Enhance, `voice-gate`'s RNNoise); cut into 3-15 s segments; transcribe with local Whisper; spot-check.
2. *Teacher generation (spends credits once):* `tools/voice_capture.py` with `tools/voice_capture_lines.jsonl` (70 fresh persona lines). Runs on skippy-pc (the sandbox has neither the key nor a route to `api.elevenlabs.io`).
   Dry run by default; spending needs `--go --max-credits N`; resumable; stops on an empty balance; the key never prints. At 1 credit/char (assumption): tier 1 = 2,625 credits (37 lines, ~2.9 min), +tier 2 = 4,772 (61 lines, ~5.3 min), +tier 3 = 5,854 (70 lines, ~6.5 min).
   Run order: ~300-credit trial (listen), tier 1 (listen), tier 2, tier 3. Check the dry-run's `voice=` against `OhzNXjLB3k0MK59KIJXI` and that `model=` is the v3 model production uses. Hold out 10% for the blind test. Ask the private Skippy (Workshop) to run it, or run it by hand; the kit must be on `main` for the Workshop.
3. *Baseline:* zero-shot candidates on the held-out lines.
4. *Fine-tune on CUDA* (the gaming PC; a LoRA on a small TTS should fit 12 GB, to verify), then serve from wherever it measures best. The student learns the tags only from the teacher's examples: tag coverage matters more than minutes.
5. *Evaluate blind* against ElevenLabs and the zero-shot baseline; deploy behind the pluggable engine. Optionally pre-render signature phrases with ElevenLabs and seed `voice-gate`'s cache.

**Credits (~8,500):** observed burn ~1,700 in two weeks of integration testing (~120/day, ~10 weeks of runway); `VOICE_DAILY_CHARS` is 4,000 (a ~2-day burn at the cap): lower it to ~1,000 as a guard; tests should use a stub or local engine.
Budget: Skippy full set ~5,850, Nagatha ~1,000, Bilby ~1,000, reserve ~600-1,000. For Nagatha and Bilby, a short generated sample (ElevenLabs voice design would make original voices) then zero-shot locally, no fine-tune.
**Back up before anything else:** `state/skippy-voice-training.m4a`, voice-gate's `tts-cache/*.mp3`, and the ElevenLabs History lines (check ElevenLabs' terms on using output to train another model; I have not read them; credit expiry is unknown).

**Reference sample (measured, not listened to):** the Captain's 171 s MP3 (48 kHz, 128 kbps, two identical channels = mono). The 0:08-1:28 segment: -17.7 LUFS, LRA 3.7 LU, peak -1.9 dBFS, no clipping, bandwidth ~16.5 kHz (MP3 lowpass); gaps between lines are digital silence and
the sound falls 30 dB within 10-90 ms of each pause (no long reverb tail: the "hollow room" is a short effect in the voice); silence suggests generated or gated audio; nine 0.5-0.8 s gaps split it into ~10 utterances; a ~7 s silence starts at ~1:28.
A faint constant dip near 0.75 kHz runs through the file (cause unknown). Prefer a higher-bitrate or lossless original for training. Open: whether the clip is ElevenLabs output or an audiobook rip, and whether 1:35-2:51 is also Skippy.
The cloned voice stays personal-use only (built from an audiobook narrator's voice; never shared; the `speak` scope stays admin-only). Not legal advice.

**Limits:** the student cannot beat its teacher; 9-19 minutes of speech is thin for a fine-tune (the audiobook source holds far more); expect a narrower emotional range than v3 and occasional glitches that chunking, retries and the cache soften.

## 8. Availability, legal risk and the archive

- **No US ban on open-weight models exists** (searched, as of Oct 2026); the June 2026 executive order says it does not authorize mandatory licensing or preclearance. Pending bills: the China FIREWALL Act (federal procurement of "foreign entities of concern" models) and the FRONTIER Act (would treat
  making a model available for copying as deployment; registration/audits for covered developers): neither enacted. Some states ban DeepSeek on government devices. The EU AI Act gives open-source models partial relief; enforcement began Aug 2, 2026.
- **My read (not legal advice):** criminalising an individual running weights they hold is very unlikely (code is speech in US precedent; unenforceable). More plausible: limits on publishing very large models, registration for developers, platform takedowns, and bans on specific foreign-origin models for
  government/contractors, which would make *getting new weights* harder.
- **So the archive:** weights for every model above plus tokenizer/config, in `D:\models` with SHA-256 manifests (`tools/models_archive.py verify`) and a second copy elsewhere; each model's licence text and card; **both US-origin (gpt-oss) and Chinese-origin (Qwen) weights**;
  the exact software (llama.cpp commit or image digest, Mesa/ROCm/kernel versions, configs, unit files); the voice reference audio and `tts-cache`; and a dated **snapshot note** (hardware, versions, baseline numbers, bake-off results).

## 9. Phases

| # | Phase | Needs hardware? | Done when |
|---|---|---|---|
| 0 | **Decide and buy.** Re-check price/stock, pick the unit, order Linux-capable 128 GB. | no | order placed, delivery date known |
| 1 | **Voice capture (time-sensitive, credits).** Back up reference audio, `tts-cache`, History; check ElevenLabs terms; trial -> tier 1 -> 2 -> 3; Nagatha/Bilby samples. | no (skippy-pc) | audio + manifest on skippy-pc, outside git |
| 2 | **Prep in the repo (tests first).** Per-service LLM endpoints (flask-api, Mentat, reporter; the reporter must **not** unload the Halo); tiers as config; remote-aware `/health` and `core_check`; a Halo compose file (GPU passthrough, parameterised vault path and cross-host URLs); a pluggable TTS engine with a stub for tests; lower `VOICE_DAILY_CHARS`. | no | tests green; a day-one config change, not a rewrite |
| 3 | **Burn-in** in the return window (section 3). | yes | keep or return decided |
| 4 | **Bring-up.** Ubuntu, Mesa/ROCm, llama.cpp (Vulkan and HIP), Tailscale, models with checksums, smoke tests, **baseline numbers** (tokens/s per model/backend, load times, watts, temperatures). | yes | baseline recorded |
| 5 | **Skippy cutover, in stages:** (a) LLM + default tier; (b) `flask-api`, `home-reporter`, `mentat-home`; (c) gates and Funnel doors last (they change public URLs); (d) leave the Windows-bound pieces. Retire the "unload Ollama before a song" workaround when nothing LLM lives on the PC. | yes | Skippy answers from the Halo; PC can be off |
| 6 | **Fallback coding agent** (section 6): `claude-local`, the three-surface test, Workshop fallback as an explicit switch. | yes | one task finished through each surface that works |
| 7 | **Placement by measurement** for STT, images, music, TTS; move only what passes. | yes | per-workload decision recorded |
| 8 | **Voice:** baseline zero-shot, fine-tune on the 4070, blind A/B, local fallback when the daily budget is spent, switch the default only if the Captain prefers it. | partly | Captain's verdict |
| 9 | **Archive and bake-off.** 30-50 real Skippy and coding prompts, local vs hosted, shown blind, the Captain votes (could be an Experiments page); write the snapshot note. | yes | archive verified; snapshot note written |

## 10. Risks

- Price and stock move weekly; verify before ordering. Strix Halo software (ROCm, Mesa, kernel) is young: expect tinkering; Vulkan llama.cpp is the safe first path.
- Agentic coding with a 3B-active model will be visibly weaker and slower than Sonnet over long runs; set expectations and let the bake-off show where.
- Credits cannot be re-spent and may expire: capture once, deliberately, and back up first. A distilled voice cannot beat its teacher.
- The Halo is a single point of failure for Skippy; an always-on box needs power/UPS/cooling thought.
- Open-weight availability is not guaranteed forever: download and verify now.

## 11. Decisions and questions for the Captain

1. Which box, and the real ceiling (M5 at $3,599, X3 at $3,799.99, Halo at $3,999.99)?
2. Merge the capture kit (and these docs) to `main`, so the private Skippy can run it? (Work so far sits on the feature branch only.)
3. Is the 171 s clip ElevenLabs output or an audiobook rip? Is 1:35-2:51 also Skippy? Is there a higher-quality original?
4. What is ElevenLabs voice `AnqNg6CSpNIFXSC6vMLI` (opened in the dashboard on Oct 15): Nagatha or Bilby?
5. Music library size, and is the gaming PC ever off or asleep for long stretches?
6. Lower `VOICE_DAILY_CHARS` to ~1,000 now?
7. Start Phase 2 (repo prep) before the box arrives?

## Sources

- Prices/hardware: [Wccftech Bosgame M5](https://wccftech.com/review/bosgame-m5-review-the-cheapest-128-gb-strix-halo-mini-pc/), [Tom's Hardware EVO-X2](https://www.tomshardware.com/desktops/mini-pcs/gmktec-evo-x2-ai-mini-pc-review), [TechRadar EVO-X3](https://www.techradar.com/pro/gmktec-evo-x3-mini-pc-review), [Halo launch](https://videocardz.com/newz/amd-launches-3999-ryzen-ai-halo-pc-limited-to-one-us-retailer), [Tom's Hardware Halo](https://www.tomshardware.com/desktops/mini-pcs/amd-challenges-nvidias-dgx-spark-with-usd3-999-ryzen-ai-halo-with-windows-11-support-strix-halo-desktop-undercuts-nvidia-by-usd700-packs-128gb-of-unified-memory)
- Speeds: [Level1Techs](https://forum.level1techs.com/t/strix-halo-llm-inference-notes/249466), [Framework forum](https://community.frame.work/t/amd-strix-halo-ryzen-ai-max-395-gpu-llm-performance-tests/72521), [Phoronix ROCm 7.0](https://www.phoronix.com/review/amd-rocm-7-strix-halo)
- Models: [Pinggy 128 GB](https://pinggy.io/blog/top_5_local_llm_tools_and_models/), [Pinggy coding LLMs](https://pinggy.io/blog/best_open_source_self_hosted_llms_for_coding/), [Qwen3-Coder-Next](https://qwen.ai/blog?id=qwen3-coder-next), [Haiku 5.5 benchmarks](https://apidog.com/blog/claude-haiku-5-5-benchmarks/)
- Image/speech: [AMD ComfyUI](https://rocm.blogs.amd.com/artificial-intelligence/comfyui-windows/README.html), [Gigazine](https://gigazine.net/gsc_news/en/20260107-amd-comfyui-rocm), [Framework image gen](https://community.frame.work/t/anybody-tried-running-image-generation-e-g-stable-diffusion-xl-3-5-or-similar-on-linux/76932), [whisper-rocm](https://github.com/davidguttman/whisper-rocm)
- Claude Code with local models: [Hugging Face](https://huggingface.co/blog/ggml-org/anthropic-messages-api-in-llamacpp), [llama.cpp PR](https://github.com/ggml-org/llama.cpp/pull/17570), [Cursor override](https://dev.to/orchidfiles/why-localhost-doesnt-work-as-openai-base-url-in-cursor-and-how-to-fix-it-589e), [gateway docs](https://code.claude.com/docs/en/llm-gateway-connect.md)
- Voices: [Pinggy TTS](https://pinggy.io/blog/best_open_source_self_hosted_text_to_speech_models/), [Yaps TTS](https://www.yaps.ai/blog/best-local-tts-models)
- Policy: [TechCrunch](https://techcrunch.com/2026/07/24/as-us-weighs-response-to-chinese-ai-industry-urges-against-broad-open-weight-restrictions/), [Commercient](https://www.commercient.com/us-ban-open-weight-ai-models)
