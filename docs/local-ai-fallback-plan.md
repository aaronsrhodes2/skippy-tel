# Home AI box: offload, fallback and archive (plan)

Status: **plan, nothing bought or built yet** (written 2026-10-16). Budget ceiling: **$3,500**.
Related: `docs/v2/CHARTER.md` §10 question 1 (this answers it: option (b), a dedicated always-on box).

## Why

1. **Contention.** The gaming PC (RTX 4070, 12 GB) is the dev machine, the game/stream host and the LLM host. Ollama keeps two
   models resident (`OLLAMA_KEEP_ALIVE=-1`), so VRAM fights Sunshine, games, ACE-Step and Whisper. The reporter even has an
   "unload Ollama before a song" step (`services/home-reporter/reporter.py`) to work around it.
2. **Fallback.** Hosted frontier models can get dearer, slower, deprecated or policy-restricted. A local model that is
   *good enough* keeps Skippy and the coding loop alive on the day that happens.
3. **Archive.** Pinned weights, pinned software and a recorded baseline of how well they work today.

Non-goals: matching Sonnet 5.5 on long-context comprehension or speed (it won't). Whether each GPU job leaves the 4070 is decided by
measurement (see "Scope v2"), not assumed.

## What the "fake Anthropic key" idea turns into

The idea is right; the editor is the wrong half of it. Checked 2026-10-16:

- **Cursor** sends custom-endpoint requests through *its own servers*, so `localhost` never works. It needs a public HTTPS tunnel, and
  only the chat panel honours the override: Agent, Composer, inline edit and Tab stay on Cursor's backend. The override also
  interferes with Cursor's built-in models. Not recommended as the fallback surface.
  ([forum](https://forum.cursor.com/t/problem-reaching-openai-error-on-a-local-model-with-overriden-base-url/3975),
  [explainer](https://dev.to/orchidfiles/why-localhost-doesnt-work-as-openai-base-url-in-cursor-and-how-to-fix-it-589e))
- **Claude Code can be pointed at a local server.** `llama-server` has spoken the Anthropic Messages API (`/v1/messages`, streaming,
  tool use, token counting) natively since January 2026, so `ANTHROPIC_BASE_URL=http://halo:8080 claude --model <alias>` works with no
  proxy. ([Hugging Face write-up](https://huggingface.co/blog/ggml-org/anthropic-messages-api-in-llamacpp),
  [PR](https://github.com/ggml-org/llama.cpp/pull/17570)) The Workshop already runs Claude Code, so this is the natural fallback.
- **Editor-side alternatives** that honour a local endpoint for agent work: Cline, OpenCode, Continue, Aider (point them at the box over the tailnet).
- **k8s / Docker Swarm: skip.** They coordinate *many* nodes. One box needs `docker compose` or systemd, which is already the idiom in this repo.
  Revisit only if a second inference node is ever added. A gateway (LiteLLM) is optional: add it only for key mapping or routing between boxes.
- The "fake key" is just any string; set `--api-key` on the server so only holders of it on the tailnet can call it.

### Which editor surface can use it (the Captain found no external-model setting in Claude Desktop)

Searched 2026-10-16: the documented way to redirect Claude Code is the **`env` block in `~/.claude/settings.json`** (or a project's
`.claude/settings.json`): `ANTHROPIC_BASE_URL`, a token in `ANTHROPIC_AUTH_TOKEN`, and a model id/alias. Third-party gateway guides describe this
for the **CLI and the VS Code extension**; the one Claude Desktop mention found says Desktop needs its in-app developer configuration for a
gateway, and I could not retrieve those steps. **Treat Claude Desktop as unsupported for a local model until a test proves otherwise.**
Official reference to check first: [code.claude.com/docs/en/llm-gateway-connect](https://code.claude.com/docs/en/llm-gateway-connect.md).

Practical consequences:
- **Terminal / VS Code with the Claude Code extension:** best surface for the fallback; use `/status` to confirm the base URL in effect.
- Setting `ANTHROPIC_AUTH_TOKEN`/`ANTHROPIC_API_KEY` globally can bypass the normal `/login`, so put the fallback in a **separate settings
  file or shell profile** (e.g. a `claude-local` wrapper that exports the variables for one run), not in the global file the Workshop and
  the Captain's normal sessions read. Environment variables are read at start-up, so switching needs a new session.
- A local model loses Anthropic-hosted extras (for example the built-in web search is reported unavailable with third-party models).
- **Cline / OpenCode / Continue / Aider** in an editor of choice are the fallbacks if Claude Code's tool loop proves too heavy for a 3B-active model.
- Cursor: chat panel only, via a public tunnel (above). Not worth the exposure.

Test to run on day one: same task in (a) `claude-local` in a terminal, (b) VS Code extension with a project `.claude/settings.json`, (c) Claude Desktop.
Record which of the three actually reaches the Halo (`llama-server` request log) before relying on it.

## Hardware (budget $3,500)

All prices below are from search summaries on 2026-10-16 and **must be re-checked before buying** (the page fetches failed in the
session; prices have been rising: the 2025 guides quoting $1,500-2,000 are obsolete, apparently because of DRAM costs).

| Machine | 128 GB config | Price seen | Notes |
|---|---|---|---|
| GMKtec EVO-X2 | 128 GB / 1 TB | **$3,499.99** | Vendor store; a notice says another price rise is coming. 2 TB is $3,649.99. |
| Framework Desktop (DIY) | 128 GB | $3,449 | Out of stock late September; confirm what the price includes (SSD, PSU, case). |
| Bosgame M5 | 128 GB / 2 TB | $3,599 | Cheapest 128 GB on Amazon US per one Oct 10 listing. $99 over. |
| Minisforum MS-S1 Max | 128 GB | $3,799-$3,879 | Over budget. |
| **AMD Ryzen AI Halo** | 128 GB / 2 TB | **$3,999.99** | Over budget by $500. AMD-supported dev platform, Micro Center in-store. |
| Beelink GTR9 Pro | 128 GB | $4,349 | Over budget. |

**Recommendation:** under a hard $3,500 the only clean fit is the **GMKtec EVO-X2 128 GB/1 TB at $3,499.99**, and tax or shipping may push it
over; the Bosgame M5 at $3,599 is the nearest fallback. Do **not** buy a 64 GB tier: the 120B-class models need ~66 GB plus context. If the
ceiling can flex by $500, the Halo buys vendor support, which matters most for ROCm/BIOS issues. All of these use the same Ryzen AI
Max+ 395, so inference speed should be near-identical; what differs is cooling, noise, ports and support (read a review of the exact unit).

### Amazon listings pasted by the Captain (2026-10-16)

| Listing | Config | Price | Returns | Warranty | Notes |
|---|---|---|---|---|---|
| **Bosgame M5** | 128 GB / 2 TB | **$3,599.00** | free | 1 yr full + 3 yr parts | 4.3 stars (635). Dual USB4, single 2.5GbE, Wi-Fi 7. |
| GMKtec EVO-X2 | 128 GB / 2 TB | $3,649.99 | free | 1 yr limited | Same listing as the X3 (its 64 GB / 2 TB option is $2,399.99: too small). |
| GMKtec EVO-X3 | 128 GB / 2 TB | $3,799.99 | free | 1 yr limited | 4.2 stars (115). OCuLink. The struck-through "$5,499.99 list / 31% off" is not a real reference price. |

All three use the same Ryzen AI Max+ 395, so inference speed should match; the differences are price, warranty, cooling and OCuLink.
Both listings' marketing ("70B+ LLMs with ease", "DeepSeek 70B Q8 runs comfortably") is optimistic: a 70B Q8 reads ~70 GB per token, and at
~215 GB/s that caps near 3 tokens/s (derived, not measured). This plan does not rely on dense 70B models. The "Radeon 8090S" typo in the X3 copy
is sloppy listing text, not evidence about the hardware.

**Pick at these prices: the Bosgame M5 ($3,599, 2 TB, longest warranty, cheapest of the three)**, unless a thermal/noise review of the M5 shows
it throttling under sustained load. Sales tax will push the total above $3,599. Cooling for the M5 has NOT been checked yet.

**Burn-in inside the return window (before wiping Windows or relying on it):**
1. Confirm the OS reports 128 GB and a 2 TB drive; note the BIOS version and the iGPU memory (UMA) setting.
2. Check the return deadline on the order page and set a reminder two days before it.
3. Install llama.cpp (Vulkan) and run `llama-bench` on gpt-oss-120b and Qwen3-30B-A3B in a loop for several hours.
   Log tokens/s over time, CPU/GPU temperatures, fan noise at the desk, and whether the clocks throttle.
4. Run the burn-in from a Linux USB stick or external SSD, so the internal drive and its Windows stay untouched until you decide to keep the box.
   Check Wi-Fi/2.5GbE, the iGPU and the SSD are all seen.
5. Pass: speeds stay near the baseline, no throttle cliff, no crashes. Fail: return it.

Budget is effectively all spent on the machine. Archive storage (below) should reuse the existing `D:\models` drive plus one more existing disk;
a second copy on a new external drive is extra money, not in the $3,500.

## Software

- **OS:** Linux, headless. **First choice: Ubuntu 26.04 LTS** (AMD's ROCm packages target Ubuntu LTS first, and a published faster-whisper-on-Strix-Halo
  recipe uses Ubuntu 26.04 + ROCm 7.2.2); Fedora 43 is the alternative the LLM benchmarks used. Prefer the distro's in-kernel amdgpu driver over DKMS
  (an earlier Phoronix test saw DKMS builds fail on a newer kernel), and check gfx1151 against AMD's ROCm compatibility page at install time.
  The LLM path (llama.cpp on Vulkan / Mesa RADV) needs **no ROCm at all**; ROCm only matters for ComfyUI, Whisper and ACE-Step. Community benchmarks used Fedora 43 (ROCm 7.0
  nightlies and Vulkan RADV) and CachyOS (kernel 7.0, Mesa RADV). Results depend on kernel/Mesa/ROCm versions, so **record them**.
- **Inference:** `llama.cpp` / `llama-server`, **Vulkan (RADV) first**, ROCm HIP as the alternative (HIP often wins prompt processing,
  Vulkan often wins token generation; measure both on the real box). Pin the build (commit or container image digest).
- **Model switching:** one big model resident at a time plus the small fast one; `llama-swap` (or a systemd unit per model) to change.
  Two big models do not fit together: 66 GB + 46 GB leaves too little for the KV cache and the OS.
- **Skippy's default tier** can stay on the Ollama API if Ollama runs well on this chip (its AMD support has been uneven); otherwise add a
  small OpenAI-compatible client next to `clients/ollama.py`.
- **Network:** Tailscale only. Never published to the internet. The Funnel doors stay on the gaming PC.
- **Ops:** compose/systemd on the Halo; the home reporter reports its health to the portal like every other capability.

## Models (to evaluate; benchmark figures in the blogs conflict, trust only our own bake-off)

| Role | Model | Why | Evidence |
|---|---|---|---|
| Coding agent / Workshop fallback | **Qwen3-Coder-Next** (80B, 3B active, Apache 2.0, ~46 GB) | Most-recommended local agentic coder for 128 GB | ~42.7 tok/s measured on Strix Halo |
| General, tools, Skippy "big" tier | **gpt-oss-120b** (~66 GB) | Strong tool calling; US-origin | ~46-53 tok/s measured |
| Fast chat / summaries / routing | **Qwen3-30B-A3B** | Cheap and quick | ~86 tok/s measured |
| Not recommended on this box | Dense 70B-123B (e.g. Mistral Medium 3.5, Devstral 2) | Memory bandwidth (~215 GB/s) caps dense models near 5 tok/s | measured ~4.6-5.3 tok/s for a 70B |

Keep **both US-origin and Chinese-origin** weights in the archive (see the legal note in the chat that produced this plan, and below).

## Scope v2 (2026-10-16): the Halo as the whole AI workshop; development stays on the gaming PC

The Captain wants coding, image generation, speech in/out and music generation on the Halo too, and the gaming PC left for development and games.
Confidence differs a lot by workload (searched 2026-10-16; community numbers are single-author tests unless stated):

| Workload | Today | On the Halo | Confidence | Notes |
|---|---|---|---|---|
| Chat / Skippy tiers | Ollama, CUDA (4070) | llama.cpp or Ollama on the iGPU | **high** | Others measure 46-86 tok/s on MoE models. |
| Coding fallback ("Haiku-class") | none | Qwen3-Coder-Next through Claude Code (`ANTHROPIC_BASE_URL`) | **medium** | Useful, **not equivalent**: vendor figures are Qwen3-Coder-Next 70.6% SWE-Bench Verified (SWE-Agent) and 44.3% SWE-Bench Pro; Haiku 5.5 published 64.8 on SWE-Bench Pro and no Verified score. Different scaffolds and effort levels, so not like-for-like, but the direction is "below Haiku on hard tasks". The bake-off decides. |
| Speech to text | faster-whisper, CUDA | faster-whisper on ROCm | **medium-high** | One report: large-v3 about 9x real time. Needs a ROCm >= 6.4.3 image; ROCm 6.1 images lack gfx1151 and silently fall back to CPU. |
| Text to speech | ElevenLabs cloud (admin), device voice (others) | unchanged at first; local clone is an optional, staged replacement (below) | **low for a local clone** | Not GPU-bound today. Open voice-cloning models are mostly CUDA-first, and none was benchmarked on AMD in what I found. |
| Image generation | Stable Diffusion on the host (4070) | ComfyUI on ROCm | **medium** | SDXL-class works; FP8 Flux-class reported at ~6 min/image on this chip; some models crash; a `--force-fp32` flag is the reported fix for NaN output on gfx1151. Diffusion is compute-bound, so the 4070 may well be faster. |
| Music (ACE-Step 1.5) | CUDA container (4070) | unknown | **low** | No ROCm report found. The service's own notes say the model is plain PyTorch, so ROCm PyTorch might run it. Test; otherwise it stays on the 4070. |

**Memory and bandwidth budget (128 GB shared).** Weights: gpt-oss-120b ~66 GB, Qwen3-Coder-Next ~46 GB, so the two big models together (112 GB)
cannot be resident with a KV cache, the OS and a diffusion model. Keep a small resident set (the fast chat model plus Whisper) and **load big
models on demand**; before an image or music job an arbiter unloads the big models first, which is exactly what `reporter.py` already does for
ACE-Step. Every concurrent job also takes memory bandwidth from the LLM, so token speed drops while anything else runs. A coding agent sends large
contexts: a 40K-token first turn at ~850-1,000 tok/s prefill is roughly 40-50 s (estimate), faster once the KV cache is warm.

**Placement by measurement (added to the burn-in).** Run the same jobs on both machines: SDXL 1024x1024 at 30 steps, a 60 s ACE-Step song,
Whisper large-v3 on 10 minutes of audio, one coding task. A workload moves to the Halo only if it lands within about 2x of the 4070's time
and frees the PC; otherwise it stays. The reporter's capability probes already show an absent service as "not ready" instead of failing.

**Replacing ElevenLabs for Skippy's voice (optional, staged).** The plumbing is one seam: `voice-gate` and the Android app call flask-api's `/tts`
(`routes/tts.py`), which calls `clients/elevenlabs_client.py` and streams `audio/mpeg`. (`intent.py` and `health.py` also mention ElevenLabs; not yet read.)
A local engine plugs in behind that route; `voice-gate`'s ffmpeg chain already takes mp3 in/out, so a WAV from a local engine only needs converting.
The hard part is the voice, not the plumbing:
- **Emotion tags.** The persona and `speak` field use ElevenLabs v3 tags (`[sighs]`, `[chuckles]`, `[sarcastic]`, ALL CAPS, "..."). Each local engine
  has its own (or no) tags, so a small per-engine mapping layer is needed (translate, or strip).
- **Clone quality.** The reference is audiobook audio the repo itself measures as rough and noisy (HNR about 3 dB); open models are likely sensitive to that.
  Local also allows fine-tuning on more audio, which an instant clone cannot.
- **Latency.** ElevenLabs streams in ~400 ms. Local first-audio latency and real-time factor are unknown on this hardware: measure them.
  **Bar to beat**, from the Captain's ElevenLabs request log (66 stream calls, Oct 10-14 2026, all HTTP 200): median 0.33 s, p90 0.46 s, max 0.87 s
  (assuming the log's latency is time to response start). The log shows the API and the clone were still answering on Oct 14.
- **Candidates** (licences from secondary roundups, verify on each repo): Chatterbox (MIT), Orpheus 3B (Apache 2.0 per some sources, emotion tags),
  Qwen3-TTS (Apache 2.0 per one list). XTTS-v2 (non-commercial, the company closed) and F5-TTS (non-commercial weights) are acceptable for strictly
  personal use but not for anything shared. Kokoro (already in `services/tts`) does not clone voices.
  ([roundup](https://pinggy.io/blog/best_open_source_self_hosted_text_to_speech_models/), [roundup](https://www.yaps.ai/blog/best-local-tts-models))
- **Staging:** (1) make the engine pluggable, ElevenLabs still default; (2) use the local voice as the fallback when the daily character budget is
  spent, instead of the device voice; (3) blind A/B on ~20 real Skippy lines with tags, ElevenLabs vs each candidate, plus latency; (4) switch the default only if the Captain prefers it.
- **Voice distillation pipeline (the Captain's idea, 2026-10-16): ElevenLabs as the teacher, an own single-voice model as the student.**
  1. *Source and clean, locally (free).* Collect the reference clips; isolate speech from music/effects and denoise with local tools (options such as
     Demucs, DeepFilterNet, Resemble Enhance, plus the RNNoise step `voice-gate` already has), so credits are not spent on cleaning. Cut into 3-15 s
     segments, transcribe with local Whisper, spot-check the text.
  2. *Teacher generation (spends credits, once, planned).* About 8,500 credits remain; the Captain's observed burn is ~1,700 in two weeks of
     app-integration testing (~120 a day), so the balance is a ~10-week runway, not days. Budget ~3,000-4,000 for the capture. Tests should use a
     stub or local engine so they stop spending credits, and `VOICE_DAILY_CHARS` (4,000 today) can drop to ~1,000 as a guard. If 1 credit is 1 character (my assumption; some models may bill half)
     that is roughly 9-19 minutes of speech at ~15 characters/s. Keep ~1,000 in reserve for the A/B baseline. Lines must come from real Skippy
     chat/persona text and cover **every tag** (`[sighs]`, `[chuckles]`, `[laughs]`, `[sarcastic]`, `[whispers]`, `[excited]`), ALL CAPS, "...",
     short and long lines, and the proper nouns and physics terms Skippy actually says. Save each as audio + a manifest row
     (`id`, `text_with_tags`, `plain_text`, `audio`, `source`, `seconds`), hold out 10% for the blind test. Sample rate and format follow the chosen student.
  3. *Baseline.* Zero-shot candidates on the held-out lines, to learn how far fine-tuning must move the needle.
  4. *Fine-tune on CUDA.* Train on the gaming PC (mature CUDA stack; a LoRA on a small TTS should fit in 12 GB, to be verified), then serve from wherever it measures best.
     The student learns the tag vocabulary only from the teacher's examples, so tag coverage in step 2 matters more than total minutes.
  5. *Evaluate blind* against ElevenLabs and the zero-shot baseline; iterate; deploy behind the pluggable engine. Optionally pre-render Skippy's signature
     phrases with ElevenLabs while credits last and seed `voice-gate`'s cache with them.
  Limits: the student cannot beat its teacher, and the teacher is itself a clone from a noisy source; 10-20 minutes of data is thin for a fine-tune
  (the audiobook source holds far more, but see the personal-use note); expect a narrower emotional range than ElevenLabs v3 and occasional
  glitches (a mispronounced name, a repeated word), which sentence-level chunking, retries and the cache soften.
- **Capture kit (written 2026-10-16, tested, not yet run):** `tools/voice_capture.py` + `tools/voice_capture_lines.jsonl` (70 fresh Skippy lines in the persona's
  voice, tagged; tests in `tools/tests/test_voice_capture.py`). It runs on skippy-pc, where the vault `.env` lives (the cloud sandbox has neither the key nor a
  route to `api.elevenlabs.io`). Default is a dry run; spending needs `--go --max-credits N`; it resumes, stops on an empty balance, keeps the key out of the output.
  Cost at 1 credit per character: tier 1 = 2,625 credits (37 lines, ~2.9 min of speech, every tag, caps, pauses, stingers), +tier 2 = 4,772 (61 lines, ~5.3 min),
  +tier 3 = 5,854 (70 lines, ~6.5 min). The full Skippy set leaves ~2,600 of the 8,500 for Nagatha, Bilby and a reserve.
  Run order: a ~300-credit trial (listen to those few files), then tier 1, listen again, then tier 2, then tier 3.
  Check the dry-run's `voice=` against the voice id in the Oct 10-14 request log (`OhzNXjLB3k0MK59KIJXI`) and that `model=` is the v3 model production uses.
- **Reference sample measured (not listened to), 2026-10-16:** the Captain's 171 s MP3 (48 kHz, 128 kbps, two identical channels = mono); the 0:08-1:28 segment is
  -17.7 LUFS, LRA 3.7 LU, peak -1.9 dBFS, no clipping, bandwidth to ~16.5 kHz (the MP3's lowpass). Gaps between lines are digital silence (frame RMS floor
  below -86 dBFS) and the signal falls 30 dB within 10-90 ms of each pause: no long reverb tail, so the "hollow room" is a short effect inside the voice, and the
  silence suggests generated or gated audio, not a raw recording. Nine gaps of 0.5-0.8 s split the segment into about ten utterances; a ~7 s silence starts at ~1:28.
  A faint constant dip near 0.75 kHz runs through the whole file (cause unknown). For training prefer a higher-bitrate or lossless original over this 128 kbps MP3.
- The cloned voice stays personal-use only (see `elevenlabs_client.py`), and the `speak` scope stays admin-only, locally or not.

**Development stays on the gaming PC.** Claude Code / the VS Code extension run on the PC with a `claude-local` wrapper pointed at the Halo over the
tailnet; clones, git and the Workshop bridge stay on the PC. Only inference is remote.

Sources: [AMD: ComfyUI on Ryzen AI Max+](https://rocm.blogs.amd.com/artificial-intelligence/comfyui-windows/README.html),
[Gigazine](https://gigazine.net/gsc_news/en/20260107-amd-comfyui-rocm), [Framework forum: image gen on Linux](https://community.frame.work/t/anybody-tried-running-image-generation-e-g-stable-diffusion-xl-3-5-or-similar-on-linux/76932),
[whisper-rocm](https://github.com/davidguttman/whisper-rocm), [Qwen3-Coder-Next](https://qwen.ai/blog?id=qwen3-coder-next),
[Haiku 5.5 benchmarks](https://apidog.com/blog/claude-haiku-5-5-benchmarks/), [Phoronix: Strix Halo ROCm 7.0](https://www.phoronix.com/review/amd-rocm-7-strix-halo).

## Phases

**0. Buy.** Re-check prices and stock; pick the unit; Linux, 128 GB. (Captain's decision.)

**1. Prep in the repo (before the box arrives; tests first).**
- Per-service LLM endpoints: flask-api (`OLLAMA_BASE_URL`), Mentat (`OLLAMA_URL`), reporter (unload target) become independent settings.
  The reporter must **not** point its unload step at the Halo, or every song would evict the Halo's models.
- `/health` and the portal's `core_check` understand a *remote* LLM box (a Halo outage reads as `degraded`, not a mystery).
- Tiers as config (endpoint, model, API style), so swapping hosted/local is a setting.

**2. Bring-up.** OS, Mesa/ROCm, llama.cpp (Vulkan and HIP), Tailscale, download the models, verify checksums, run the smoke tests,
**record baseline numbers** (tokens/s per model per backend, load times, idle and load wattage, temperatures).

**3. Skippy cutover.** Default tier to the Halo. The 4070 box keeps Whisper/voice-gate, ACE-Step, Stable Diffusion, Sunshine and the portal bridge,
and no LLM VRAM. The "unload Ollama before a song" workaround is retired once nothing LLM lives there.

**4. Fallback coding agent.** `ANTHROPIC_BASE_URL` + model alias against the Halo's `llama-server`.
**The Workshop's model is deliberately hard-coded** (`workshop_engine.py` `MODEL`/`EFFORT`: "no environment variable and no request
field", and tests assert it). So this is a **separate, explicit fallback mode that the Captain turns on by hand**, never a silent failover,
and the existing tests keep proving the default is still Sonnet 5.5. Context must be generous (>= 32K, ideally 64K+), and Claude Code's
per-request attribution header can defeat the KV cache on local servers, so check and neutralise it.

**5. Archive ("this moment in software history").**
- Weights for every model above plus tokenizer/config files, in `D:\models` with SHA-256 manifests (`tools/models_archive.py verify`), and a second copy elsewhere.
- Each model's licence text and model card, saved next to it.
- The exact software: llama.cpp commit/source tarball and container image digest, Mesa/ROCm/kernel versions, the configs and unit files.
- A dated **snapshot note**: hardware, versions, baseline numbers and the bake-off results (below).

**6. Bake-off.** 30-50 real Skippy and coding prompts; local vs hosted, answers shown blind side by side, the Captain votes. It turns "is local
good enough yet?" into a number, and it is the archive's headline result. It can live as an Experiments page.

## Risks and open questions

- Price and stock move weekly; verify before ordering.
- Strix Halo software (ROCm, Mesa, kernel) is young; expect tinkering. Vulkan llama.cpp is the safest first path.
- Agentic coding with a 3B-active model will be visibly weaker and slower than Sonnet over long runs; set expectations and use the bake-off to see where.
- A box that is always on needs power/UPS/cooling thought (not in budget).
- Open-weight availability is not guaranteed forever: download and verify now (see Archive).

## Sources

- Prices: [Wccftech Bosgame M5](https://wccftech.com/review/bosgame-m5-review-the-cheapest-128-gb-strix-halo-mini-pc/), [Tom's Hardware EVO-X2](https://www.tomshardware.com/desktops/mini-pcs/gmktec-evo-x2-ai-mini-pc-review), [Compute Market](https://www.compute-market.com/blog/strix-halo-mini-pc-local-ai-2026), [Computing for Geeks](https://computingforgeeks.com/ryzen-ai-max-395-mini-pc-comparison/), [Halo launch](https://videocardz.com/newz/amd-launches-3999-ryzen-ai-halo-pc-limited-to-one-us-retailer)
- Speeds: [Level1Techs](https://forum.level1techs.com/t/strix-halo-llm-inference-notes/249466), [Framework forum](https://community.frame.work/t/amd-strix-halo-ryzen-ai-max-395-gpu-llm-performance-tests/72521)
- Models: [Pinggy: what fits in 128 GB](https://pinggy.io/blog/top_5_local_llm_tools_and_models/), [Pinggy: self-hosted coding LLMs](https://pinggy.io/blog/best_open_source_self_hosted_llms_for_coding/)
- Claude Code with local models: [Hugging Face](https://huggingface.co/blog/ggml-org/anthropic-messages-api-in-llamacpp), [KDnuggets](https://kdnuggets.com/pairing-claude-code-with-local-models)
