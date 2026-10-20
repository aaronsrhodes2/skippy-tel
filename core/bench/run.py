"""One benchmark prompt, sent the same way to Claude or to skippy-core: a bare Anthropic /v1/messages call.

No system prompt, no tools, no skills: the raw model. llama-swap passes /v1/messages through, so the same request
goes to either side and the comparison is the model, not the plumbing. Standard library only.

  python3 core/bench/run.py arkanoid --model claude-haiku-5-5 --vault /etc/skippy-core/vault.env
  python3 core/bench/run.py arkanoid --model coder --base http://localhost:8080          # skippy-core's llm port

Writes core/bench/<task>/out/<label>.html (the first ```html block), .txt (the whole reply) and .json (timing, tokens).
The key is read from ANTHROPIC_API_KEY or the vault file, and never printed.
"""
import argparse
import base64
import json
import os
import re
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEDIA = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}


def key_from(vault: str | None) -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    for line in Path(vault).read_text().splitlines() if vault else []:
        if line.startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def request(task: Path, model: str, max_tokens: int) -> dict:
    content = []
    for img in sorted(p for p in task.iterdir() if p.suffix.lower() in MEDIA):
        content.append({"type": "image", "source": {"type": "base64", "media_type": MEDIA[img.suffix.lower()],
                                                    "data": base64.b64encode(img.read_bytes()).decode()}})
    content.append({"type": "text", "text": (task / "prompt.txt").read_text().strip()})
    return {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", help="output name (default: the model)")
    ap.add_argument("--base", default="https://api.anthropic.com")
    ap.add_argument("--vault", help="a .env file holding ANTHROPIC_API_KEY (for the Anthropic side)")
    ap.add_argument("--max-tokens", type=int, default=32000)
    a = ap.parse_args()

    task = HERE / a.task
    body = json.dumps(request(task, a.model, a.max_tokens)).encode()
    req = urllib.request.Request(a.base.rstrip("/") + "/v1/messages", data=body, method="POST", headers={
        "content-type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": key_from(a.vault)})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        reply = json.loads(r.read())
    secs = round(time.time() - t0, 1)

    text = "".join(b.get("text", "") for b in reply.get("content", []) if b.get("type") == "text")
    m = re.search(r"```html\s*\n(.*?)```", text, re.S) or re.search(r"(<!DOCTYPE html.*</html>)", text, re.S | re.I)
    out = task / "out"
    out.mkdir(exist_ok=True)
    label = a.label or a.model.replace("/", "_")
    (out / f"{label}.txt").write_text(text)
    if m:
        (out / f"{label}.html").write_text(m.group(1))
    usage = reply.get("usage", {})
    meta = {"model": reply.get("model", a.model), "base": a.base, "seconds": secs, "stop_reason": reply.get("stop_reason"),
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
            "tokens_per_second": round((usage.get("output_tokens") or 0) / secs, 1) if secs else None,
            "html_found": bool(m), "html_bytes": len(m.group(1)) if m else 0}
    (out / f"{label}.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return 0 if m else 1


if __name__ == "__main__":
    raise SystemExit(main())
