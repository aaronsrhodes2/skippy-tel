# SkippyCore

SkippyCore is Skippy's always-on brain box: a Bosgame M5 (Ryzen AI Max+ 395, 128 GB unified memory) running Ubuntu 26.04,
llama.cpp on Vulkan behind llama-swap, and SkippyTel's core services. The gaming PC keeps development, games and anything
CUDA- or Windows-bound. The full reasoning is in `docs/local-ai-fallback-plan.md` (on `claude/confident-heisenberg-tcefjd`)
and `docs/skippycore-handoff.md`.

Everything here runs **today**, before the box arrives: the simulator runs the same deployer, compose file and smoke test
with tiny stand-in models on CPU. Onlining the real models later is a config line, not new work.

## What's in `core/`

| Piece | File | What it does |
|---|---|---|
| Model slots | `models.json`, `llm/models.py` | Three slots: **chat** (always loaded), **general** and **coder** (loaded on demand, they swap). Each has a stand-in and the full model. `CORE_FULL` picks which slots run full-size. `models.py` checks the files, checks the memory budget and renders llama-swap's config plus the shim's name table. |
| LLM server | `compose.core.yml` → `llm` | llama-swap with llama.cpp's Vulkan build (pinned digest). Every model launches with `-fa on --load-mode none` (flash attention, no mmap: both Strix Halo community guides require it; `--load-mode none` is what this llama.cpp build calls `--no-mmap`) and `--parallel` at most 8. |
| Ollama shim | `ollama-shim/` | Ollama's API on port 11434 in front of llama-swap, so flask-api, Mentat, the reporter, voice-gate and Bilby keep calling `OLLAMA_URL` unchanged. `keep_alive: 0` never unloads the resident chat slot. `/v1/...` passes straight through (OpenAI and Anthropic `/v1/messages`). |
| Stack | `compose.core.yml`, `compose.gpu.yml` | `llm`, `ollama` (the shim) and `flask-api`. Paths come from the deployer's settings, not Windows drives. `compose.gpu.yml` hands `/dev/dri` to `llm` on the box only. |
| Smoke test | `smoke.py` | Tags, chat (stream and not), JSON output, every slot including the general↔coder swap, Anthropic `/v1/messages` on coder, flask-api `/health` and a real `/intent/unmatched` reply. |
| Pull deployer | `deployer/` | A systemd timer every 2 minutes. It fetches `main`, waits for the commit's `core.yml` run to go green, deploys, runs the smoke test and rolls back to the last good commit if anything is red. A red smoke test marks the commit bad; a failed build or pull is retried. Nothing listens for inbound connections. |
| Simulator | `sim/` | Ubuntu 26.04 with its own Docker inside. It deploys the baseline, rolls back a deliberately broken commit, and refuses a `CORE_FULL` whose files are missing, checking the stack stays healthy throughout. |
| CI | `.github/workflows/core.yml` | Runs on every push: the unit tests, and the simulator scenario on GitHub's `ubuntu-26.04` runner when `core/`, `services/flask-api/` or the workflow changed. The deployer requires its run to be green. |

## Run the simulator

Any Linux or Windows machine with Docker (on skippy-pc: Git Bash, Docker Desktop):

```
make core-sim                                    # stand-ins go to state/core-models (~1.6 GB, downloaded once)
MODELS_ROOT=/d/models make core-sim              # or use the archive the midnight job fills
```

It prints PASS/FAIL per step and `simulator: ALL PASSED` at the end. The unit tests alone: `python -m pytest core/tests`.

## Online the big models

The deploy key is commit + `CORE_FULL`, so changing `CORE_FULL` in `/etc/skippy-core/core.env` redeploys on the next timer tick:

```
CORE_FULL=chat              # then general, then coder, one at a time
CORE_FULL=all
```

A slot whose files are missing or the wrong size is **refused**: the running stack is left alone, and the deploy goes ahead
by itself once the files are in place. `python3 core/llm/models.py plan --full all` shows what each slot would run and whether
it fits the memory budget (full tier: 84 GB peak of 110 GB).

## Day one on the box (runbook)

Inside the return window, do the burn-in from the plan (section 3) before wiping Windows.

1. **BIOS:** set the iGPU memory (UMA frame buffer) to its minimum, 512 MB (2 GB if that is the lowest offered). The GPU
   gets its memory through GTT instead (next step). Note the BIOS version.
2. **Ubuntu 26.04 LTS server**, headless (`sudo systemctl set-default multi-user.target`). Use the in-kernel amdgpu driver,
   not DKMS. Record the kernel, Mesa and `linux-firmware` versions (avoid `linux-firmware-20251125`: it broke ROCm on
   Strix Halo; Vulkan was unaffected).
3. **Kernel options** so the GPU can use ~124 GB: in `/etc/default/grub`, add to `GRUB_CMDLINE_LINUX_DEFAULT`
   `amd_iommu=off amdgpu.gttsize=126976 ttm.pages_limit=32505856`, then `sudo update-grub && sudo reboot`.
   Check: `cat /sys/class/drm/card*/device/mem_info_gtt_total` shows about 124 GB (133,143,986,176 bytes).
   (`amd_iommu=off` is worth 5-12% speed but disables the NPU; drop it if the NPU is ever wanted.)
4. **Docker** (`docker.io docker-compose-v2`), **git**, **python3**, **Tailscale** (`tailscale up`).
5. **Models:** copy `D:\models` to `/srv/models` (same layout: `huggingface/<Org>__<Repo>/...`), then
   `sudo MODELS_ROOT=/srv/models python3 core/llm/models.py check --hash --full all`.
   Anything still missing: `models.py fetch`. Gated repos need the Hugging Face token. It lives in the vault `.env`
   (the Captain's Google Drive vault folder) as `HF_ID`. Export it for that one command
   (`HF_ID=... python3 core/llm/models.py fetch ...`), never into a committed file. The midnight job on skippy-pc
   reads `HF_ID` from the vault by itself. The stand-ins and the three full models are not gated, so CI needs no token.
6. **Deployer:** `sudo bash core/deployer/install.sh` (dry run), then `--apply`. In `/etc/skippy-core/core.env`, set
   `GITHUB_TOKEN` to a **classic** token with **no scopes**: the repo is public, so the token only lifts the API rate
   limit from 60 to 5,000 an hour (fine-grained tokens can't read check results). Copy the vault to
   `/etc/skippy-core/vault.env` (0600). The first tick deploys `main` with stand-ins.
7. Online the slots one at a time (`CORE_FULL=chat`, then `chat,general`, then `all`), checking
   `python3 core/deployer/deployer.py status` after each.
8. Record the baseline numbers (tokens/s per model, load times, watts, temperatures) for the snapshot note.

## Status and recovery

- `python3 /opt/skippy-core/repo/core/deployer/deployer.py status`: the last run (`ok`, `waiting-ci`, `refused`,
  `rolled-back`, `known-bad`, `failed`), with the smoke results. The home reporter will carry it to the portal.
- A key (commit + `CORE_FULL`) that failed is never retried. Push a fix, or after fixing something outside git run
  `deployer.py forget`.
- `journalctl -u skippy-core-deployer` holds each run's log.

## Not yet

- A speech-to-text slot (whisper.cpp on Vulkan, stand-in `ggml-tiny.en`) and a TTS slot behind flask-api `/tts`.
- The reporter: skip the "unload before a song" call for the box, and carry the deployer's status to the portal.
- An `--api-key` on the `llm` port so the gaming PC's Claude Code can use the coder slot over the tailnet.
- Pinning the stand-ins' and full models' `sha256` in `models.json` (CI's fetch step and `models.py fetch` print them).
- The chat slot stays Qwen3-30B-A3B until the Captain decides on the non-thinking Instruct-2507.
