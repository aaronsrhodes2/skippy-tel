# SkippyCore build: gotchas to beat before they bite (2026-10-16)

Checked against `docs/skippycore-handoff.md` and `core/` at commit 955874a, and the pinned llama-swap **v265** source
(cloned at tag). Hardware tuning (kernel args, `-fa on --no-mmap`, the 2507 chat model) is already in
[proven-setups.md](proven-setups.md) and is not repeated here; note that **none of it is in `core/models.json` or
`render()` yet**. "Verified" = read in source or docs today. "Inferred" = reasoning, check it at burn-in.

## Contradicts the current plan (fix the plan first)

| # | Gotcha | Fix / check |
|---|---|---|
| 1 | **The deployer will stall.** Step 8 runs `core.yml` only on pushes touching `core/**`, but step 4 requires a green `core` check *on the commit being deployed*. Any commit to main that doesn't touch `core/**` has no `core` check, so main's head is never deployable. Worse, `compose.core.yml` builds `services/flask-api`, which is outside `core/**`, so flask-api changes would ship to the box without going through the simulator. (Inferred from the plan.) | Run `core.yml` on every push to main, with a cheap first job that exits green when nothing relevant changed, and put `services/flask-api/**` and every other service the core compose builds into the relevant paths. Or have the deployer walk back to the newest commit with a green `core` run. |
| 2 | **The config mount path doesn't match the image.** The plan mounts `${CORE_STATE}/llm:/config:ro`, but the llama-swap image's ENTRYPOINT is `/app/llama-swap -config /app/config.yaml -watch-config`, so it would serve the bundled example config. (Verified: `docker/llama-swap.Containerfile` at v265.) | Set `entrypoint: ["/app/llama-swap","-config","/config/config.yaml","-watch-config"]` explicitly in the compose. |
| 3 | **`resident: true` doesn't load anything at startup.** A `persistent` group only stops other groups from unloading its members. Nothing loads chat until the first request, so the first Skippy reply after every deploy or reboot pays an 18 GB cold load. (Verified: v265 config docs; `render()` emits no hook.) | Render `hooks: {on_startup: {preload: ["chat"]}}`. |
| 4 | **The reporter's `free_gpu` will evict the resident chat model.** It calls `/api/ps`, then `keep_alive: 0` for every model it gets back. If the shim maps that to llama-swap's `POST /api/models/unload/:id`, chat gets unloaded. (Verified: `reporter.py:335`, llama-swap README.) | Until the "don't unload the Halo" rule lands, make the shim treat `keep_alive: 0` as a no-op for resident slots and unload only on-demand ones. |
| 5 | **The GitHub check gate needs the right token.** Fine-grained PATs are listed as unable to use the Checks API (GitHub's token docs list it as a known gap). | The repo is public (API returns 200 unauthenticated). Poll `GET /repos/aaronsrhodes2/skippy-tel/actions/workflows/core.yml/runs?head_sha=<sha>` instead, and authenticate with a classic PAT that has **no scopes**. That is read-only access to public data, 5000 requests/h instead of 60/h shared with skippy-pc's other tools behind the same home IP. `git fetch` of a public repo needs no deploy key at all. |
| 6 | **You may not need the nested Ubuntu.** GitHub-hosted `ubuntu-26.04` runners have been in public preview since June 2026 (kernel 7.0). | Run the simulator directly on `runs-on: ubuntu-26.04`. Keep docker-in-docker only if you want the bare-origin/rollback scenario sealed off from the runner; see #14 if you do. |
| 7 | **Branch rule clash.** The handoff keeps work on `claude/focused-sagan-12axe2` with "merging is the Captain's call", but CLAUDE.md and PLATINUM §17 say no PRs and push straight to main. Also the deployer watches `origin/main`, so nothing reaches the box until it is merged. | Pick one, Captain. The repo's own rule says main. |

## LLM server (llama-swap + llama-server)

| # | Gotcha | Fix / check |
|---|---|---|
| 8 | **`-ngl 0` isn't pure CPU on a Vulkan build.** llama-server still offloads big-batch host ops to any device it sees (`--op-offload` defaults to true). On a GPU-less runner, a Mesa image can expose lavapipe (software Vulkan), which is slow or crashes. (Op-offload verified in the llama-server README; lavapipe in the image is inferred.) | In sim/CI, add `--device none` alongside `GPU_LAYERS=0`. Log `llama-server --list-devices` in CI so a surprise device shows up. |
| 9 | **The stand-in chat model will "think" its whole budget away.** Qwen3-0.6B with `--jinja` reasons by default, and llama-server moves that text into `reasoning_content`. A smoke test with a small `num_predict` can get an **empty `message.content`** and fail for a bad reason. The same applies to gpt-oss on general. (Inferred; `--reasoning-format`/`--reasoning-budget` verified in the README.) | Give the stand-in chat slot `--reasoning-budget 0`, or use the 2507 Instruct (non-thinking) in both tiers. The smoke test should assert non-empty content with a generous token cap. |
| 10 | **Context is shared, not per request.** With `-np` unset, llama-server picks 4 slots and a unified KV pool, so `--ctx-size 65536` is the pool all concurrent requests share. Claude Code's parallel subagents on coder can then evict or truncate each other. (Verified: README, issue #17989, discussion #22658.) | Set `-np` explicitly per slot (1-2 for coder; 8 or fewer per proven-setups), and size `--ctx-size` = slots × per-request need. Read `n_ctx_seq` and `kv_unified` in the startup log at burn-in. |
| 11 | **General↔coder swap thrash.** Each swap re-reads 46-63 GB from NVMe, because `--no-mmap` means no page-cache shortcut. That is roughly 10-30 s per swap (inferred). Two callers alternating, such as a coding session plus Skippy's tool tier, will ping-pong. | Burn-in: time a swap. If it hurts, route Skippy's tool calls to chat while a coder session is active, or use v265's `matrix` engine with evict costs. |
| 12 | **The 'legacy' risks.** Top-level `groups:` is now "legacy" and gets normalized into `routing.router`. Mixing the two forms is a hard config error, and the `vulkan` image tag is "legacy" in favour of `unified-vulkan`. (Verified: `internal/config/load.go:195`, README.) | Fine at the pinned digest. Render `routing.router.settings.groups` next time `render()` is touched, so a future bump doesn't break it. |
| 13 | **Config re-render vs. running models.** Compose won't recreate `llm` when only a bind-mounted file changes. `-watch-config` hot-reloads instead, and a half-written file can be read mid-write. (Inferred.) | Write `config.yaml` to a temp file and `rename()` it into place in the mounted *directory*. Never bind-mount the single file, because inode swaps go stale. Keep `sendLoadingState` off (the default), because the shim drops reasoning anyway. |

## Containers on the box

| # | Gotcha | Fix / check |
|---|---|---|
| 14 | **`/dev/dri` permission denied.** The llama-swap image runs as UID/GID 10001. `renderD128` is `root:render 0660`, and the host's render GID doesn't exist inside the image, so `group_add: [render]` by name fails. kyuz0's compose just uses `privileged: true`. (Verified: Containerfile; ROCm-docker #90; Intel troubleshooting.) | `group_add: ["${RENDER_GID}"]` set from `getent group render \| cut -d: -f3` on the box, plus `devices: [/dev/dri]`. Don't use privileged. Day-one check: `docker compose exec llm ls -ln /dev/dri` and `vulkaninfo --summary` (or `--list-devices`). |
| 15 | **DinD if you keep it.** A plain `ubuntu:26.04` running dockerd fails on cgroup v2 ("domain controllers... threaded mode"), and overlay-on-overlay storage breaks. (Verified: moby `hack/dind`, docker-library dind `VOLUME /var/lib/docker`.) | Copy moby's `hack/dind` cgroup-nesting block into the sim entrypoint, and make `/var/lib/docker` a volume. |
| 16 | **`up -d` returns before anything works.** llama-swap answers health before any model is loaded. | `docker compose up -d --wait --wait-timeout 900`, with healthchecks on every service. The smoke test should then make its first chat call with a long timeout, because of the cold load. |

## Ollama shim

| # | Gotcha | Fix / check |
|---|---|---|
| 17 | **`/api/tags` must answer in under 2 s and tell the truth.** flask-api `/health` uses a 2 s timeout. If tags waits on llama-swap, health flaps; if it lists names straight from `shim.json`, `default_present` says true while chat is broken. That is the exact failure CLAUDE.md warns about for Ollama. (Verified: `routes/health.py`.) | Serve names from `shim.json`, but drop the default when llama-swap's `/running` (cached, 1 s timeout) reports the chat slot failed. |
| 18 | **Long silences kill streams.** `clients/ollama.py` uses httpx `timeout=120` per read. A cold 63 GB load plus reasoning can go more than 120 s without a content byte. (Inferred.) | While upstream sends reasoning deltas or nothing yet, have the shim emit `{"message":{"role":"assistant","content":""},"done":false}` NDJSON keep-alives. |
| 19 | **Field fidelity.** Strict Ollama clients expect `created_at`, `done_reason`, `model` echoing the requested name (llama-server reports the slot alias `chat`), and 404 `{"error":"model '...' not found"}` for unknown names. `format` can be `"json"` *or a JSON schema object*. | Map a schema to `response_format: {type: json_schema, ...}`. Test each mapping against a fake upstream (handoff step 3). |

## Claude Code coding fallback (coder slot)

| # | Gotcha | Fix / check |
|---|---|---|
| 20 | **Claude Code asks for `claude-*` model names**, which llama-swap 404s. Background "Haiku" calls use a different name than the main loop. (Inferred from Claude Code's model settings.) | Set `ANTHROPIC_MODEL=skippy-coder`, `ANTHROPIC_DEFAULT_SONNET_MODEL`/`_OPUS_MODEL=skippy-coder`, `ANTHROPIC_DEFAULT_HAIKU_MODEL=skippy-chat` and `CLAUDE_CODE_SUBAGENT_MODEL=skippy-coder`. llama-swap v265 passes `/v1/messages` and `/v1/messages/count_tokens` (verified, README); tool use needs `--jinja` (llama-server README). |
| 21 | **Context floor.** Claude Code's system prompt plus tools alone is tens of thousands of tokens; guides recommend 64K or more. A per-request attribution header can also invalidate the KV prefix cache on local servers. | Coder ctx ≥ 65536 *per slot* (see #10). The CI stand-in (8192) can't run real Claude Code, so smoke-test `/v1/messages` with a tiny request only. At burn-in, check prompt-cache hits in llama-server's log. |

## Pull deployer and rollback

| # | Gotcha | Fix / check |
|---|---|---|
| 22 | **The deployer must not deploy itself.** `deployer.py` lives in the repo. A bad commit to it breaks the very rollback that should save you. (Inferred; standard pull-agent practice.) | Run it from an installed copy (`/opt/skippy-core/deployer`) that is updated only after a deploy passes smoke, and keep the previous copy. |
| 23 | **Rollback that rebuilds isn't a rollback.** `up --build` from the same checkout means going back re-runs pip and apt, and that fails when the internet is the problem. | Build images tagged `skippycore/<svc>:<sha>`, keep the last good tag's images (don't `image prune -a`), and roll back with `up -d` on the old tag. Use release dirs `/srv/skippy-core/releases/<sha>` plus a `current` symlink. Use absolute paths only in the compose, because relative bind mounts resolve per release dir. |
| 24 | **Don't poison good commits.** A transient failure (GHCR or PyPI down, HF 429) would mark a fine commit bad forever. | Mark bad only when **smoke fails** after a clean `up`. Retry build and pull failures with backoff. A new commit or `deployer retry <sha>` clears a mark. Put the reason in the status JSON. |
| 25 | **Overlap and catch-up.** | A systemd timer won't start a oneshot that is still running, but add `flock` anyway, plus `Persistent=true` and `RandomizedDelaySec=20`. Test the units with `systemd-analyze verify` in CI, since the sim has no systemd. |

## CI and model files

| # | Gotcha | Fix / check |
|---|---|---|
| 26 | **Rate limits in CI.** Docker Hub allows 100 anonymous pulls per 6 h per IP, and shared runner IPs share that. HF throttles anonymous downloads. An inner dockerd has none of the outer image cache. (Verified: Docker docs.) | `docker/login-action` (or GHCR-only images). Add an `HF_TOKEN` secret. Cache stand-ins by manifest hash (about 1.6 GB, well under the 10 GB repo cache). For DinD, `docker save \| docker load` images from the outer daemon. |
| 27 | **Pin hashes now, not after download.** HF's tree API returns each LFS file's `lfs.oid`, which is its sha256, without downloading. Also pin a `revision` (the commit sha) per entry. Unsloth re-uploaded Coder-Next GGUFs in February under the same names, so unpinned paths can change under you. | Add `"revision"` per variant in `models.json`, fill the sha256 values from `/api/models/<repo>/tree/<rev>`, and fetch via `resolve/<rev>/`. |

## skippy-pc's midnight archive job

| # | Gotcha | Fix / check |
|---|---|---|
| 28 | **Wake timers often don't fire.** Wake timers must be enabled in the power plan, and Modern Standby (S0) PCs commonly ignore task wake timers. Desktops with S3 are fine. | On skippy-pc: run `powercfg /a`. S3 listed means good; "Standby (S0 Low Power Idle)" means expect misses. Also run `powercfg /waketimers` after registering. Turn on "run as soon as possible after a missed start" and turn off "only on AC power". |

## Sources
- llama-swap v265 source (Containerfile, `internal/config/load.go`, `docs/config.example.yaml`, README): https://github.com/mostlygeek/llama-swap/tree/v265
- llama-server README (flags, Anthropic API, router mode): https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- Slots and unified KV: https://github.com/ggml-org/llama.cpp/issues/17989 , https://github.com/ggml-org/llama.cpp/discussions/22658
- Render GID: https://github.com/ROCm/ROCm-docker/issues/90 , https://docs.openedgeplatform.intel.com/2026.2/edge-ai-suites/surgical-instrument/troubleshooting.html
- kyuz0 compose how-to: https://github.com/kyuz0/amd-strix-halo-toolboxes/blob/main/docs/docker-compose-how-to.md
- DinD cgroup v2 nesting: https://github.com/moby/moby/blob/master/hack/dind ; docker-library dind template (`VOLUME /var/lib/docker`)
- Ubuntu 26.04 runners: https://github.blog/changelog/2026-06-11-new-runner-images-in-public-preview/
- Fine-grained PAT gaps: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens ; https://zenn.dev/enjyu/articles/47764b1aaf2d02
- Docker Hub limits: https://docs.docker.com/docker-hub/usage/pulls/
- Claude Code on local models: https://jonathansblog.co.uk/?p=27516 , https://glukhov.org/ai-devtools/claude-code/ , https://github.com/lukoshkin/llmm
- Wake timers vs Modern Standby: https://learn.microsoft.com/en-us/answers/questions/1660115/regarding-waking-up-from-sleep-on-a-modern-standby , https://h30434.www3.hp.com/t5/Notebook-Operating-System-and-Recovery/Scheduled-task-does-not-wake-laptop-reliably/m-p/9564506
- Pull-agent prior art: https://github.com/kimdre/doco-cd ; https://pkg.go.dev/github.com/jclement/pullpilot@v0.1.1 (health-gated rollback to previous digest)
