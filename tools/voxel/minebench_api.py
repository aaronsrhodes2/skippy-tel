"""MineBench the way MineBench itself runs a model (github.com/Ammaar-Alam/minebench lib/ai/generateVoxelBuild.ts, tool mode, its
default): its system prompt with the TOOL MODE section (tools/voxel/MINEBENCH_SYSTEM.md, checked against prompts.ts 2026-10-31) and
its user message; a reply that fails (no JSON call, code that throws, under 500 blocks, a footprint under 38) goes back to the model
in its repair prompt, a fresh request each time, up to 8 attempts. No agent, no tools of ours beyond voxel.exec's own rules.
    python tools/voxel/minebench_api.py tools/voxel/MINEBENCH_SYSTEM.md "a choo-choo train" out_dir --model claude-haiku-5-5
    python tools/voxel/minebench_api.py tools/voxel/MINEBENCH_SYSTEM.md "a choo-choo train" out_dir --core --model coder
        (a Skippy Core slot: skippy-chat, coder, qwen3-8b-stock: the same Anthropic API on llama-swap, SKIPPY_CORE_URL and
        SKIPPY_CORE_API_KEY from the vault)
Writes out_dir/raw-<attempt>.txt, out_dir/build.json (when one passes), out_dir/raw.txt (the last reply) and out_dir/run.json
(model, started, seconds, tokens summed, each attempt and its error). The key comes from ANTHROPIC_API_KEY or the vault.
Not copied: MineBench's JSON-schema response format (Core's Anthropic endpoint takes none).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_BLOCKS, MIN_FOOTPRINT = 500, 38      # MineBench at grid 256: MIN_BLOCKS_BY_GRID, max(6, floor(256 * 0.15))
VAULTS = ("G:/My Drive/vault/.env", "D:/Aaron/development/.env")


def vault(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    for v in VAULTS:
        p = Path(v)
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit(f"no {name}")


def first_json(text: str) -> dict:
    """The voxel.exec call in the reply: the whole reply, or a fenced block, or the outermost {...}."""
    for candidate in (text, *re.findall(r"```(?:json)?\s*(.*?)```", text, re.S), text[text.find("{"): text.rfind("}") + 1]):
        try:
            got = json.loads(candidate)
            if isinstance(got, dict):
                return got
        except (ValueError, TypeError):
            continue
    raise ValueError("no JSON object in the reply")


def check(text: str, out: Path) -> tuple[dict | None, str]:
    """MineBench's processVoxelBuildResponse, as far as a build can fail it: the JSON call, the code run as voxel.exec runs it
    (runtime.mjs), at least 500 blocks, a footprint of at least 38 across x or z. Returns (the call, "") or (None, the reason)."""
    try:
        call = first_json(text)
    except ValueError:
        return None, "Could not find a valid JSON object in the response"
    if call.get("tool") != "voxel.exec" or not isinstance(call.get("input"), dict) or "code" not in call["input"]:
        return None, "Expected a voxel.exec tool call object"
    (out / "build.json").write_text(json.dumps(call), encoding="utf-8")
    r = subprocess.run(["node", str(HERE / "runtime.mjs"), str(out / "build.json")], capture_output=True, text=True)
    if r.returncode:
        errors = [ln.strip() for ln in r.stderr.splitlines() if "Error" in ln]
        return None, errors[0] if errors else "voxel.exec failed"
    got = json.loads(r.stdout.strip().splitlines()[-1])
    if got["blocks"] < MIN_BLOCKS:
        return None, f"Build too small ({got['blocks']} blocks). Create at least ~{MIN_BLOCKS} blocks so the result is recognizable."
    lo, hi = got["bounds"]["min"], got["bounds"]["max"]
    span = max(hi[0] - lo[0] + 1, hi[2] - lo[2] + 1)
    if span < MIN_FOOTPRINT:
        return None, (f"Build footprint too small (span {span}). Expand the build to span at least ~{MIN_FOOTPRINT} blocks "
                      "across x or z for more detail.")
    return call, ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt_file", help="MineBench's system prompt: tools/voxel/MINEBENCH_SYSTEM.md (its tool mode, the default there)")
    ap.add_argument("request")
    ap.add_argument("out_dir")
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--max-tokens", type=int, default=32000)
    ap.add_argument("--core", action="store_true", help="the model is a Skippy Core slot, not Claude")
    ap.add_argument("--attempts", type=int, default=8, help="MineBench's own: 8 in tool mode, each failure sent back to repair")
    a = ap.parse_args(argv)
    import anthropic
    system = Path(a.prompt_file).read_text(encoding="utf-8").split("## Build Request")[0].rstrip()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if a.core:   # the key both ways, as the bridge sends it; a local model can take many minutes
        key = vault("SKIPPY_CORE_API_KEY")
        client = anthropic.Anthropic(base_url=vault("SKIPPY_CORE_URL"), api_key=key, auth_token=key, timeout=3600)
    else:
        client = anthropic.Anthropic(api_key=vault("ANTHROPIC_API_KEY"))
    t0 = time.time()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0))
    run = {"model": a.model, "started": started, "input_tokens": 0, "output_tokens": 0, "attempts": []}
    text, error, call = "", "", None
    for attempt in range(1, a.attempts + 1):
        if attempt == 1:     # MineBench's buildUserPrompt
            user = f"## Build Request\nHere is what you need to build:\n<build_request>\n{a.request}\n</build_request>"
        else:                # its buildRepairPrompt and the tool-mode reminder: a fresh request, not a conversation
            user = (f"Your previous output was invalid.\nReason: {error}\n\nYou are still building: {a.request}\n\n"
                    f"Fix it by returning ONLY a corrected JSON object.\n\nPrevious output:\n{text[:20000]}"
                    "\n\nReminder: return ONLY the voxel.exec tool call JSON (not the build JSON).")
        with client.messages.stream(model=a.model, max_tokens=a.max_tokens, system=system,
                                    messages=[{"role": "user", "content": user}]) as stream:
            final = stream.get_final_message()
        text = "".join(b.text for b in final.content if getattr(b, "type", "") == "text")
        (out / f"raw-{attempt}.txt").write_text(text, encoding="utf-8")
        run["input_tokens"] += final.usage.input_tokens
        run["output_tokens"] += final.usage.output_tokens
        call, error = check(text, out)
        run["attempts"].append({"stop_reason": final.stop_reason, "output_tokens": final.usage.output_tokens, "error": error or None})
        print(json.dumps({"attempt": attempt, "error": error or None}), flush=True)
        if call:
            break
    if not call:
        (out / "build.json").unlink(missing_ok=True)
    (out / "raw.txt").write_text(text, encoding="utf-8")
    run.update(seconds=round(time.time() - t0), build="ok" if call else f"failed: {error}")
    (out / "run.json").write_text(json.dumps(run, indent=1), encoding="utf-8")
    print(json.dumps({k: run[k] for k in ("model", "seconds", "build")} | {"attempts": len(run["attempts"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
