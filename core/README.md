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
| Model slots | `models.json`, `llm/models.py` | Four slots: **chat** and **vision** (always loaded), **general** and **coder** (loaded on demand, they swap). Each has a stand-in and the full model. `CORE_FULL` picks which slots run full-size. `models.py` checks the files, checks the memory budget and renders llama-swap's config plus the shim's name table. `--bench a,b` (or `BENCH=`) also serves models.json's **candidates** under their own names, on demand, to try a model before it takes a slot. |
| LLM server | `compose.core.yml` → `llm` | llama-swap with llama.cpp's Vulkan build (pinned digest). Every model launches with `-fa on --load-mode none` (flash attention, no mmap: both Strix Halo community guides require it; `--load-mode none` is what this llama.cpp build calls `--no-mmap`) and `--parallel` at most 8. |
| Ollama shim | `ollama-shim/` | Ollama's API on port 11434 in front of llama-swap, so flask-api, Mentat, the reporter, voice-gate and Bilby keep calling `OLLAMA_URL` unchanged. `keep_alive: 0` never unloads the resident chat slot. `/v1/...` passes straight through (OpenAI and Anthropic `/v1/messages`). |
| Stack | `compose.core.yml`, `compose.gpu.yml` | `llm`, `ollama` (the shim) and `flask-api`. Paths come from the deployer's settings, not Windows drives. `compose.gpu.yml` hands `/dev/dri` to `llm` on the box only. |
| Picture studio | `image-studio/` | The portal chat's QUALITY pictures (`make_image_hq`; the quick tier stays on skippy-pc): Qwen-Image-2512 (Q8_0, Apache 2.0) with its Qwen2.5-VL-7B text encoder, through stable-diffusion.cpp's Vulkan build (pinned release + sha256). One `sd-cli` run per picture with `--mmap` (~30 GB while drawing, nothing held between pictures; without `--mmap` the weights sit in RAM twice and the OOM killer fires). ~20 s a step at 1024 px, 40 steps by default, so ~13 min a picture: quality over speed, by the Captain's call. `/health` gives `gpu_free_gib`, `gpu_free_pct`, `gpu_util_pct` for the portal's patient queue. Host networking on 7347: ufw keeps the LAN out, Tailscale's own rules let the tailnet in, and `ALLOW_FROM` answers only skippy-pc (and the box itself). |
| Smoke test | `smoke.py` | Tags, chat (stream and not), JSON output, every slot including the general↔coder swap, Anthropic `/v1/messages` on coder, a wrong key refused (when `LLM_API_KEY` is set), flask-api `/health` and a real `/intent/unmatched` reply. |
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
   `GITHUB_TOKEN` to the vault's `GITHUB_DEPLOYER_TOKEN`: the repo is **private**, so the token is what lets the
   box read Actions runs and `git fetch` at all (fine-grained, this repo only: Contents and Actions read). Copy the
   vault to `/etc/skippy-core/vault.env` (0600). `core/install/README.md` shows how both go over SSH without
   anyone typing them. The first tick deploys `main` with stand-ins.
7. Online the slots one at a time (`CORE_FULL=chat`, then `chat,general`, then `all`), checking
   `python3 core/deployer/deployer.py status` after each.
8. Record the baseline numbers (tokens/s per model, load times, watts, temperatures) for the snapshot note.

## The portal's door (the cloud reaches the LLM)

The cloud portal can't reach the house, so skippy-core puts llama-swap on the internet through **Tailscale Funnel**, locked
by a key. The portal calls it with the **Anthropic SDK** and another `base_url`:

```python
client = anthropic.Anthropic(base_url=SKIPPY_CORE_URL, api_key=SKIPPY_CORE_API_KEY)
client.messages.stream(model="chat", max_tokens=..., tools=[...], messages=[...],
                       extra_body={"chat_template_kwargs": {"enable_thinking": False}})
```

- **The key:** `LLM_API_KEY` in `/etc/skippy-core/core.env` (root-only). The deployer hands it to `models.py render`, which
  writes `apiKeys: ["${env.LLM_API_KEY}"]` into llama-swap's config (the value stays in the container's environment, never
  in the file), to the shim (it sends it upstream) and to `smoke.py` (which also checks that a wrong key gets 401). With
  the key, every endpoint except `/health` answers 401 without it, `Authorization: Bearer` or `x-api-key` alike.
  The vault holds the same value as `SKIPPY_CORE_API_KEY`, and `SKIPPY_CORE_URL` is `https://skippy-core.tailab2cc7.ts.net`.
- **The Funnel:** `sudo bash core/install/funnel.sh` (dry run), `--apply`, `--check`, `--off`. `--apply` makes the key if
  there is none, and it opens the Funnel (`:443 -> http://127.0.0.1:8080`) **only if the port already answers 401 to a
  wrong key**, so an open port never reaches the internet. No firewall change: tailscaled proxies to localhost, and ufw
  keeps denying everything inbound (`harden.sh --check` stays green).
- **Check it the portal's way** (from skippy-pc, key and URL from the vault, nothing printed):
  `python core/llm/door_check.py` (thinking off) and `--think`. It checks: no key gets 401, a plain reply, a streamed
  reply, and a streamed tool round trip (`ask_mentat`: a `tool_use` block, then a `tool_result`, then the answer).

**Since 2026-10-23 chat and coder run Qwen3.6-35B-A3B** (`core/bench/README.md` has the bench): the chat slot (UD-Q4_K_XL)
starts with **thinking off by default** (`--chat-template-kwargs {"enable_thinking": false}` on the server), so a reply is
~0.4-1 s and the portal needs to send nothing; a request that wants thinking sends `enable_thinking: true` and gets it
(10-20 s). Tool use on Qwen3.6, 2026-10-23: a physics question called the tool 5/5 and other questions 0/5, thinking off
or on, never as text. The table and notes below were measured on the previous chat model (Qwen3-30B-A3B Q4_K_M, llama-swap v265, 2026-10-19):

| | Thinking off (`enable_thinking: false`) | Thinking on (Qwen3's default) |
|---|---|---|
| Plain `/v1/messages` | 0.2 s for a short hello | ~6 s (a thinking block first; leave max_tokens room for it) |
| Streaming (SSE `text_delta`) | yes | yes |
| Tool use (`tool_use` -> `tool_result` -> answer, streamed) | yes, 0.8 s round trip | yes, ~7 s |

- Anthropic's own `thinking: {"type": "disabled"}` is **ignored** by llama-server; only `chat_template_kwargs` turns
  thinking off (or `/no_think` at the end of the user's message). With thinking on, the reply carries `thinking` blocks
  with an empty signature: send them back unchanged in the assistant turn, or drop them.
- The portal should use `/v1/messages`; `/v1/chat/completions` is not needed for tools.
- **How the chat slot uses tools** (5 tries each, one `ask_mentat` tool, 2026-10-19):
  - A physics question calls the tool **every time** (10/10 with thinking off or on, with or without "use the tool"),
    always as a real `tool_use` block. It never wrote a tool call as text (unlike Qwen3-Coder on the coder slot).
  - **A forced tool is not honoured**: `tool_choice: {"type": "any"}` or `{"type": "tool", "name": ...}` on
    `/v1/messages`, and `"required"` or a named function on `/v1/chat/completions`, still give a plain answer to a
    non-physics question (at best 2 of 5 with thinking on). The portal must not rely on forcing: when a reply needs
    Mentat and didn't call it, the portal asks Mentat itself (brains.py already does).
  - `tool_choice: {"type": "none"}` held with thinking off (0/5 calls) and was ignored with thinking on (5/5 called).
- **The portal's settings:** `SKIPPY_CORE_URL` (the Funnel URL, no `/v1`) and `SKIPPY_CORE_API_KEY`, which deploy.yml
  copies into Cloud Run from the repo secrets `PORTAL_SKIPPY_CORE_URL` and `PORTAL_SKIPPY_CORE_API_KEY`;
  `SKIPPY_CORE_MODEL` picks another slot (default `chat`).

## Matter: the house's controller (2026-10-24)

SkippyCore runs the house's **Matter controller**: python-matter-server (the open-source one Home Assistant uses), pinned by digest in
`compose.matter.yml`. Matter is local and open: no account, no cloud, no fee. A device is paired once with its setup code, and from
then on skippy-pc's hub (`services/satellite/matter.py`) controls it over the controller's WebSocket API. The Nest thermostat, the
Amazon Basics Matter plug and most new smart-home gear speak it. Why here and not on skippy-pc: Matter needs IPv6 link-local and mDNS
on the home LAN, which Docker on Windows doesn't give a container; SkippyCore is a Linux box on the LAN, with Bluetooth.

- **Switch it on (once):** `sudo bash core/install/matter.sh` (dry run), then `--apply`. It refuses if IPv6 is off (the controller
  can't start without it), installs **bluez** (a brand-new device gets the Wi-Fi details over Bluetooth), adds the ufw rules (the LAN's
  mDNS and device traffic in; the API, port **5580, on the tailnet only**: it has no password of its own, so it is never on the LAN or the
  Funnel), sets `CORE_MATTER=1` and `MATTER_ARGS` in `core.env`, and runs a repair so it starts now. `--check` says whether all of it is
  live. From then on the deployer runs it with the stack, and `smoke.py` checks its `/info`.
- **The fabric** lives in `${CORE_STATE}/matter`: its keys and every paired device. Lose it and everything has to be paired again.
- **On skippy-pc:** `MATTER_URL=ws://skippy-core:5580/ws` in the vault `.env`; then `homectl.py matter-info`, `matter-pair`, `list`,
  `say`, `prove` (`services/satellite/README.md`).

## Status and recovery

- `python3 /opt/skippy-core/repo/core/deployer/deployer.py status`: the last run (`ok`, `waiting-ci`, `refused`,
  `rolled-back`, `known-bad`, `failed`), with the smoke results. The home reporter will carry it to the portal.
- A key (commit + `CORE_FULL`) that failed is never retried. Push a fix, or after fixing something outside git run
  `deployer.py forget`.
- `journalctl -u skippy-core-deployer` holds each run's log.

## Not yet

- A speech-to-text slot (whisper.cpp on Vulkan, stand-in `ggml-tiny.en`) and a TTS slot behind flask-api `/tts`.
- The reporter: skip the "unload before a song" call for the box, and carry the deployer's status to the portal.
- The gaming PC's Claude Code on the coder slot: the key is there now (`ANTHROPIC_BASE_URL` = the Funnel URL,
  `ANTHROPIC_API_KEY` = `SKIPPY_CORE_API_KEY`, model `coder`), not tried yet.
- Pinning the stand-ins' and full models' `sha256` in `models.json` (CI's fetch step and `models.py fetch` print them).
- Qwen3.6's own vision (its `mmproj-F16.gguf` is on the box) could replace the vision slot, but llama.cpp's MTP doesn't
  run with `--mmproj`, so only the chat slot could carry it. Not tried.
