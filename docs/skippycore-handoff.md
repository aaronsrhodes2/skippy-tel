# SkippyCore: handoff (2026-10-16)

The Bosgame M5 (128 GB, Asurion 3-year plan) is ordered. Goals for this work:

1. **A simulator.** A Docker setup that mimics SkippyCore (Ubuntu 26.04, llama.cpp on Vulkan) and runs **small stand-in models in the slots where the big ones go**.
2. **A complete CI/CD loop.** When the box arrives, deploying and onlining the already-downloaded big models is a config change, not new work.
3. **A baseline model archive.** A midnight job on skippy-pc downloads every potentially useful model into `D:\models`.

Decisions the Captain made:
- **Deploy path:** a *pull agent* on SkippyCore, with nothing listening for inbound connections.
- **LLM server:** *llama.cpp (llama-swap) plus an Ollama-API shim*, so the existing services don't change.

Branch: `claude/focused-sagan-12axe2` (feature branch; merging to `main` is the Captain's call). The full plan is `docs/local-ai-fallback-plan.md` on `claude/confident-heisenberg-tcefjd`.

## Update: steps 2-9 built (branch `claude/local-haiku-substrate-6ph7mj`)

The unit tests, the Ollama shim, the pull deployer, `core/compose.core.yml`, `core/smoke.py`, the simulator, `core.yml` CI and
`core/README.md` (with the day-one runbook) are in. From the Strix Halo research (`/mnt/project-files/skippycore/proven-setups.md`
in the project): every model launches with `-fa on --no-mmap`, `--parallel` is capped at 8, and the coder slot is Unsloth's
post-fix `UD-Q4_K_XL`. Still open: step 1 (register the midnight job on skippy-pc), step 10, and the chat-model choice.
`core/README.md` is the current description; the table below is the first session's record.

## Done

| Piece | File(s) | State |
|---|---|---|
| Model slots manifest | `core/models.json` | Three slots: **chat** (resident), **general** and **coder** (loaded on demand, they swap). Each has a stand-in (Qwen3-0.6B, Qwen2.5-0.5B, Qwen2.5-Coder-0.5B) and the full model (Qwen3-30B-A3B Q4_K_M, gpt-oss-120b MXFP4, Qwen3-Coder-Next Q4_K_M). Sizes come from Hugging Face listings. The aliases `qwen3:8b` and `qwen2.5:7b` map to chat, so today's callers keep working. All `sha256` values are `null` (not pinned yet). |
| Slot tool | `core/llm/models.py` | `plan` / `check` / `render` / `fetch`. `CORE_FULL=chat,general` or `all` onlines slots. A listed slot whose files are missing or the wrong size is refused, never silently swapped. The memory budget check: full tier peak 84 GB of 110 GB fits; stand-ins 9 GB. It renders llama-swap's `config.yaml` (written as JSON, which is valid YAML) and `shim.json`, the name table for the shim. Standard library only. **Dry runs checked by hand; no unit tests yet.** |
| LLM image choice | (to pin in the compose) | `ghcr.io/mostlygeek/llama-swap:v265-vulkan-b11641@sha256:7a1d92c796dadb96fee7deac9ba41d46a2e0f50dabc6578f88f2d8fd21878110`. Vulkan build: the box runs `-ngl 999`; the simulator and CI run the same image with `GPU_LAYERS=0` (CPU). |
| Baseline model list | `tools/models_baseline.json` | ~35 entries in two groups. **core**: the full and stand-in LLMs, Qwen3-8B, gpt-oss-20b; Whisper (faster-whisper medium, large-v3, turbo; whisper.cpp ggml for Vulkan); NVIDIA Parakeet and Canary; TTS (Chatterbox, Orpheus, Qwen3-TTS ×4, Kokoro, F5, XTTS, Piper en); Silero VAD, RNNoise, DeepFilterNet; bge-m3. **extended**: Qwen3-VL-8B, SDXL, FLUX.1-schnell, Whisper originals, Q8 quants. About 411 GB in total. Repo names were checked on the Hub. |
| Baseline downloader | `tools/models_baseline.py`, `tools/models_baseline_run.sh` | Built on `tools/models_archive.py`, using the same tree, `SOURCE.json` and `MANIFEST.json`. Core group first. It resumes partial downloads. A finished entry is never re-fetched, because this is a baseline, not a mirror. A gated repo is reported, not treated as fatal. It stops before free space drops under 200 GB, reads `HF_TOKEN` from the environment or the vault, and hashes the archive at the end. **No unit tests yet.** |
| Midnight task | `tools/install_models_baseline_task.sh`; `tools/install_bridge_startup.sh` gained `STARTUP_AT` (daily trigger), `STARTUP_RESTART=0` and `STARTUP_WAKE=1` | It wakes the PC at 00:00 every night, and once the archive is complete the run does nothing. The existing installer tests pass (27), and the logon trigger is unchanged. **Not registered yet:** that has to happen on skippy-pc. |

Sandbox limits found: Docker runs here, but image pulls are blocked (GHCR blobs 403, Docker Hub 429), and so is huggingface.co. **The simulator's real run has to happen in GitHub Actions or on skippy-pc.**

## Left to do (in order)

1. **Register the midnight job, on skippy-pc** (the Captain or the private Skippy, once this branch is on `main` or checked out there):
   `bash tools/install_models_baseline_task.sh` (dry run), then `--apply`. Check progress with `python tools/models_baseline.py plan`; the log is `state/models-baseline.log`.
   Optional: add `HF_TOKEN=` to the vault and accept gated licences on the Hub (the run lists which ones). NGC-only NVIDIA models are not included; NVIDIA's open speech models are on HF.
2. **Unit tests** for `core/llm/models.py` (manifest validation, `CORE_FULL` parsing, budget, check, render shape) and `tools/models_baseline.py` (order, done-marking per entry, reserve stop, gated vs failed), with a fake `snapshot_download`.
3. **Ollama shim** `core/ollama-shim/` (Flask + httpx, like flask-api). It has to cover:
   - `GET /` and `/api/version`
   - `/api/tags`: names from `shim.json`. flask-api `/health` checks `default in models`, and Mentat reads `name`.
   - `/api/ps`: from llama-swap `/running`.
   - `/api/chat`: non-stream returns `message.content`; stream returns NDJSON lines ending `done: true`, which is what `clients/ollama.py` parses.
   - `/api/generate`: `format:"json"` maps to `response_format`; `options.temperature/num_predict` map to `temperature/max_tokens`; `keep_alive: 0` with no prompt unloads, which is how the reporter's `free_gpu` calls it.
   - Tests against a fake upstream.
4. **Pull deploy agent** `core/deployer/deployer.py` (stdlib, a systemd timer every 2 minutes). Each run:
   - fetch `origin/main`;
   - require the `core` CI check to be green for that commit (GitHub API; token in `/etc/skippy-core/core.env`);
   - `models.py check` then `render`;
   - `docker compose -p skippycore -f core/compose.core.yml up -d --build --remove-orphans`;
   - `core/smoke.py`;
   - on failure, go back to the last good commit and mark the bad one so it isn't retried forever;
   - write a status JSON that the home reporter later carries to the portal.

   The deploy key is commit + `CORE_FULL`, so onlining a slot redeploys. Tests: fake git, compose and smoke.
5. **`core/compose.core.yml`**: services `llm` (pinned llama-swap, `/dev/dri`, `${MODELS_ROOT}:/models:ro`, `${CORE_STATE}/llm:/config:ro`), `ollama` (the shim, same name and port 11434 so `OLLAMA_URL` doesn't change) and `flask-api` (vault path `${CORE_VAULT_ENV:-/etc/skippy-core/vault.env}`, music `${MUSIC_ROOT}`; no `G:`/`D:` paths, no `driver: nvidia`). Add the reporter, Mentat, the gates, Dozzle and Kuma in later stages, as the plan's Phase 5 describes.
6. **`core/smoke.py`**, used by both CI and the deployer:
   - the shim's `/api/tags` lists `qwen3:8b`;
   - chat (stream and non-stream) and generate with JSON;
   - `/v1/chat/completions` on every slot, which exercises the general↔coder swap;
   - Anthropic `/v1/messages` on coder, the coding-fallback path (check that llama-swap passes it through);
   - flask-api `/health` reports `default_present: true`;
   - `/intent/unmatched` gets a reply from Skippy.
7. **The simulator** `core/sim/`: an `ubuntu:26.04` image with `docker.io`, `docker-compose-v2`, git and python3, running dockerd inside (privileged), with stand-in models mounted. The scenario:
   - a bare git repo plays `origin`, and the deployer deploys HEAD; the smoke test must pass;
   - push a deliberately broken commit; the deployer must roll back to the last good commit;
   - set `CORE_FULL=general` with the files missing; the deploy must be refused and the stack must stay healthy.
8. **CI** `.github/workflows/core.yml`, on pushes that touch `core/**`, plus manual runs:
   - the unit tests;
   - `models.py fetch` for the stand-ins, cached by the manifest hash;
   - the simulator scenario.

   Pin the stand-ins' `sha256` from the first run's log. Make the check name the one the deployer requires.
9. **`core/README.md`**: what SkippyCore is, how to run the simulator locally (`make core-sim`), how to online the big models, and the box's day-one runbook. The runbook: Ubuntu 26.04, Docker, Tailscale, copy `D:\models` to `/srv/models`, `models.py check --hash --full all`, install the deployer units, then `CORE_FULL=chat`, `general` and `coder` one at a time.
10. **Later**:
    - a speech-to-text slot (whisper.cpp on Vulkan, using the stand-in `ggml-tiny.en`) and a TTS slot behind flask-api `/tts`;
    - the reporter's "don't unload the Halo" rule;
    - a key on the `llm` port (llama-swap/llama-server `--api-key`);
    - the reporter reading the deployer's status file;
    - folding `models_baseline.json`'s core slots into `core/models.json`'s pins once hashes are known.

## Open questions for the Captain
- Merge this branch to `main` now, so the midnight job can be registered from skippy-pc's `main` checkout? Or check out the branch there just for tonight?
- How large is D: (the job keeps 200 GB free, and the full list is about 411 GB)?
- Is there an HF account token for gated repos?
