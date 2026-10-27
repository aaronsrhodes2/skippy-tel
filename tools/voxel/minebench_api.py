"""MineBench the way MineBench itself runs a model: one API call with its system prompt (its own prompts.ts, rendered for a 256 grid
and the simple palette) and its user message, and whatever comes back is the build. No runtime, no retries, no agent.
    python tools/voxel/minebench_api.py PROMPT.md "a choo-choo train" out_dir --model claude-haiku-5-5
PROMPT.md is MineBench's system prompt (skippy-minebench/MINEBENCH_PROMPT.md: everything before "## Build Request"). Writes
out_dir/build.json, out_dir/raw.txt and out_dir/run.json (model, seconds, tokens). The key comes from ANTHROPIC_API_KEY or the vault.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

VAULTS = ("G:/My Drive/vault/.env", "D:/Aaron/development/.env")


def api_key() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    for v in VAULTS:
        p = Path(v)
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("no ANTHROPIC_API_KEY")


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt_file")
    ap.add_argument("request")
    ap.add_argument("out_dir")
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--max-tokens", type=int, default=32000)
    a = ap.parse_args(argv)
    import anthropic
    system = Path(a.prompt_file).read_text(encoding="utf-8").split("## Build Request")[0].rstrip()
    user = f"## Build Request\nHere is what you need to build:\n<build_request>\n{a.request}\n</build_request>"   # MineBench's buildUserPrompt
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic(api_key=api_key())
    t0 = time.time()
    with client.messages.stream(model=a.model, max_tokens=a.max_tokens, system=system,
                                messages=[{"role": "user", "content": user}]) as stream:
        final = stream.get_final_message()
    secs = round(time.time() - t0)
    text = "".join(b.text for b in final.content if getattr(b, "type", "") == "text")
    (out / "raw.txt").write_text(text, encoding="utf-8")
    run = {"model": final.model, "seconds": secs, "stop_reason": final.stop_reason,
           "input_tokens": final.usage.input_tokens, "output_tokens": final.usage.output_tokens}
    try:
        call = first_json(text)
        (out / "build.json").write_text(json.dumps(call), encoding="utf-8")
        run["build"] = "ok"
    except ValueError as e:
        run["build"] = f"failed: {e}"
    (out / "run.json").write_text(json.dumps(run, indent=1), encoding="utf-8")
    print(json.dumps(run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
