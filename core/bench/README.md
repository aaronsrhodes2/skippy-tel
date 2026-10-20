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
