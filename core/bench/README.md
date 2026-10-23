# SkippyCore bench: the same task, Claude vs. skippy-core

One task folder per prompt (`prompt.txt` plus any images); `run.py` sends it as a bare Anthropic `/v1/messages` call
(no system prompt, no tools, no skills) to Claude or to skippy-core's llama-swap, and saves the page, the whole reply
and the timing to `<task>/out/`. Same request both ways, so a difference is the model, not the plumbing.

```bash
python3 core/bench/run.py arkanoid --model claude-haiku-5-5 --label haiku --vault /etc/skippy-core/vault.env
```

```bash
python3 core/bench/run.py arkanoid --model coder --label core-coder --base http://localhost:8080
```

A task with a `check.py` is graded: `run.py` saves the first ```` ```python ```` block and runs `python -I check.py
<answer>.py`, which prints passed/total (`calc`: an expression evaluator, 40 cases; `ttlcache`: an LRU cache with expiry,
14 cases; both checkers pass a reference answer in full). `--no-think` sends llama.cpp's
`chat_template_kwargs: {"enable_thinking": false}`. A candidate model is served for a bench with
`models.py render --bench <name>` (`core/models.json` → `candidates`).

## arkanoid (2026-10-19)

Prompt: *write a ansii graphics game similar to the game "arkanoid" in a single .html page*, plus `reference.webp`
(a screenshot: grey/red/yellow/blue/green brick rows, the Vaus paddle, the logo).

| Run | Time | Tokens out | tok/s | Page | Plays? | Follows the picture? |
|---|---|---|---|---|---|---|
| Claude Haiku 5.5 (raw API) | 40.2 s | 9,105 | 226 | 8.8 KB | Yes: launch, break bricks, score, 3 lives, levels, pause, keys and mouse; no console errors | Loosely: its own colour rows (yellow/cyan/green/magenta), no silver row, `═` paddle |

| skippy-core: vision describes, coder writes (`--images describe:vision`) | 50.8 s describe + 116.8 s code | 5,072 | 43 | 19.2 KB | Yes: launch, break bricks, score, lives, pause; no console errors | Closely: blue hex background, coloured rows, the paddle, an "ARKANOID" logo with an orb O (misplaced: it reads "ARKAONID") |

Haiku used no tools: one shot, reading the image itself. skippy-core (Qwen3-VL-8B Q8 → Qwen3-Coder-Next UD-Q4_K_XL,
Vulkan on the M5, no tools) took 4× as long and followed the picture better, because the vision pass spelled out every
row, colour and element; its one visible bug (the logo's letter order) is what a look at its own screenshot catches.

## What skippy-core needs to match it

Haiku's result came from three abilities in one model. skippy-core splits them across models, so the harness has to
join them up:

1. **Seeing the picture.** The coder slot (Qwen3-Coder-Next) is text-only. A **vision slot** (Qwen3-VL-8B + its
   mmproj, 9.9 GB) turns the image into a precise description (rows, colours, paddle, layout) that the coder gets
   with the prompt. As a tool: `describe_image(path) -> text`.
2. **Writing the file.** A raw reply works (`run.py` pulls the ```` ```html ```` block), but an editor needs
   `write_file` / `edit_file` so it can fix one function instead of re-emitting 9 KB.
3. **Checking its own work.** This is where a local model gains the most: Haiku got it right first time; a smaller
   model needs a loop. `open_page(path) -> screenshot + console errors` (headless Chromium on skippy-pc, the same thing
   `tools/workshop_screenshot.py` does for the portal) lets it see a blank canvas or a `ReferenceError` and fix it.
   The screenshot goes back through `describe_image`, so the coder can compare it with the reference.

The harness: Claude Code itself, pointed at skippy-core (`ANTHROPIC_BASE_URL` = llama-swap, model `coder`). It already
has file tools, Bash and the portal's Skippy chat as its UI (the Workshop runs Claude Code on skippy-pc), so the
"generic editor" and tomorrow's "Skippy as the higher-thinking model" are the same harness with a different model.
Needs checking: that llama-swap/llama-server's `/v1/messages` handles Claude Code's tool calls with this model.

## Qwen3.6-35B-A3B vs. the slots it replaced (2026-10-23)

On the M5 (llama-swap v265, llama.cpp b11641 Vulkan; no image bump needed: it already reads `qwen35moe` and has
`--spec-type draft-mtp`). Candidates: Unsloth's Qwen3.6-35B-A3B **UD-Q4_K_XL** (20.8 GB), **Q8_0** (34.4 GB) and the
**MTP Q8_0** from `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` (35.2 GB). The plain GGUFs carry **no MTP heads** (0 nextn tensors),
so MTP needs the separate MTP upload, and llama.cpp runs MTP only with `--parallel 1` and no `--mmproj`.

**Speed** (generation tok/s from the runs below, model already loaded; prompt processing at a 10K-token prompt):

| Model | Gen tok/s | Prompt tok/s |
|---|---|---|
| Qwen3-30B-A3B Q4_K_M (old chat) | 70-83 | - |
| Qwen3-Coder-Next UD-Q4_K_XL (old coder) | 40-48 | - |
| Qwen3.6 UD-Q4_K_XL (new chat) | 52-58 | 1,469 |
| Qwen3.6 Q8_0 | 49-52 | - |
| Qwen3.6 MTP Q8_0, `--spec-draft-n-max 2` (new coder) | 64-76 (~70% of drafts accepted) | 1,326 |

**One shot, raw `/v1/messages`** (`run.py`, no tools; grade = cases passed; time includes the thinking):

| Model | calc | ttlcache | Arkanoid (vision describes, model writes) |
|---|---|---|---|
| Old chat, thinking | 30/40, 295 s | 14/14, 152 s | |
| Old chat, no thinking | 12/40, 9 s | 14/14, 8 s | |
| Old coder (3 runs of calc) | 39/40, 31/40, syntax error | 14/14, 19 s | 117 s, plays (the 2026-10-19 run) |
| Qwen3.6 Q8, thinking | 31/40, 241 s | 10/14, 149 s | 151 s, plays, no console errors; garbled pixel logo |
| Qwen3.6 Q8, no thinking | 38/40, 29 s | 14/14, 18 s | |
| Qwen3.6 MTP Q8, no thinking (2 runs of calc) | 12/40, 33/40 | 14/14, 12 s | **51 s**, plays, follows the picture's grey/red/yellow/blue/green rows |
| Qwen3.6 Q4, no thinking (2 runs of calc) | 37/40, 38/40 | 14/14, 14 s | |

One-shot code is noisy (the old coder went 39 → syntax error → 31 on the same prompt); thinking did not help a single
reply on either model.

**Through Claude Code** (the bridge's Core brain recipe: own `CLAUDE_CONFIG_DIR`, `ANTHROPIC_BASE_URL` = the Funnel,
`CLAUDE_CODE_ATTRIBUTION_HEADER=0`, a scratch folder; told to write the answer, run the checker and fix until it passes).
The "repo" task is a copy of `core/llm/models.py` + tests: add `plan --json` and a test, make pytest pass.

| Model | calc | ttlcache | repo |
|---|---|---|---|
| Old coder | 40/40, 172 s, 8 tool calls | 14/14, 80 s | all green, **490 s**, 25 tool calls; ~50 added lines: a second plan_json() that redoes what plan() already computes |
| Qwen3.6 MTP Q8, thinking (Qwen's default) | 40/40, 360 s | 14/14, 103 s (76 s on the deployed slot) | all green, **170 s**, 27 tool calls; a clean 8-line change |
| same, thinking off | 40/40, 439 s, 38 tool calls | | all green, 211 s |
| same, `--reasoning-budget 4096` | 40/40, 759 s | | all green, 198 s |

Every tool call from every model came back as a real `tool_use` block (none written as text). Without thinking,
Qwen3.6 makes up for it with trial and error (more tool calls, longer runs), so the coder keeps thinking on.

**Chat behaviour** (`door_check.py` and 5 physics + 5 other questions with an `ask_mentat` tool, model loaded):

| Model | Tool on physics / on other | Median reply, thinking off | Median reply, thinking on |
|---|---|---|---|
| Old chat | 5/5 / 0/5 | 0.4 s | 2.4-3.9 s |
| Qwen3.6 Q4 | 5/5 / 0/5 | 0.8-1.2 s | 10-11 s |
| Qwen3.6 Q8 | 5/5 / 0/5 | 0.9-1.3 s | 14-21 s |

**Decision (deployed 2026-10-23):** both slots move to Qwen3.6. **coder** = MTP Q8_0, thinking on, `--parallel 1`, 128K
context (Qwen asks for at least 128K to keep its thinking intact): about 3× faster than Qwen3-Coder-Next on the repo task with a
cleaner change, and 11 GB smaller. **chat** = UD-Q4_K_XL (as good as Q8 here and faster), 64K context, `--parallel 4`,
with thinking **off by default on the server** (a request can still turn it on): ~1 s replies, and a much better one-shot
coder than the old chat model with thinking off (37-38/40 vs. 12/40). Qwen's recommended sampling is set per slot
(chat: temp 0.7, top-p 0.8, top-k 20, presence penalty 1.5; coder: temp 0.6, top-p 0.95, top-k 20). Qwen3-30B-A3B and
Qwen3-Coder-Next stay on disk as candidates `qwen3-30b-a3b` and `qwen3-coder-next-q4` (`models.py render --bench ...`).
