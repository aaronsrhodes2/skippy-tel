"""One benchmark prompt, sent the same way to Claude or to skippy-core: a bare Anthropic /v1/messages call.

No system prompt, no tools, no skills: the raw model. llama-swap passes /v1/messages through, so the same request
goes to either side and the comparison is the model, not the plumbing. Standard library only.

  python3 core/bench/run.py arkanoid --model claude-haiku-5-5 --vault /etc/skippy-core/vault.env
  python3 core/bench/run.py arkanoid --model coder --images describe:vision --base http://localhost:8080   # the box

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


DESCRIBE = ("Describe this image precisely for a programmer who cannot see it and must recreate it: the layout from top "
            "to bottom, every element, exact colours (as hex where you can), counts, sizes relative to the frame, shapes "
            "and any text. No opinions, only what is there.")


def images(task: Path) -> list[dict]:
    return [{"type": "image", "source": {"type": "base64", "media_type": MEDIA[p.suffix.lower()],
                                         "data": base64.b64encode(p.read_bytes()).decode()}}
            for p in sorted(task.iterdir()) if p.suffix.lower() in MEDIA]


def call(base: str, key: str, body: dict) -> dict:
    req = urllib.request.Request(base.rstrip("/") + "/v1/messages", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "anthropic-version": "2023-06-01",
                                          "x-api-key": key})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read())


def text_of(reply: dict) -> str:
    return "".join(b.get("text", "") for b in reply.get("content", []) if b.get("type") == "text")


def request(task: Path, model: str, max_tokens: int, mode: str, base: str, key: str) -> tuple[dict, str]:
    """mode: send (the images go to the model), drop (text only), describe:<model> (that model describes each image
    and the descriptions go to the model instead: eyes for a text-only coder). Returns the body and any description."""
    prompt = (task / "prompt.txt").read_text().strip()
    pics, described = images(task), ""
    if mode.startswith("describe:") and pics:
        eyes = mode.split(":", 1)[1]
        described = "\n\n".join(text_of(call(base, key, {"model": eyes, "max_tokens": 2048, "messages": [
            {"role": "user", "content": [pic, {"type": "text", "text": DESCRIBE}]}]})) for pic in pics)
        content = [{"type": "text", "text": f"{prompt}\n\nThe attached reference image, described:\n{described}"}]
    elif mode == "drop":
        content = [{"type": "text", "text": prompt}]
    else:
        content = [*pics, {"type": "text", "text": prompt}]
    return {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]}, described


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", help="output name (default: the model)")
    ap.add_argument("--base", default="https://api.anthropic.com")
    ap.add_argument("--vault", help="a .env file holding ANTHROPIC_API_KEY (for the Anthropic side)")
    ap.add_argument("--max-tokens", type=int, default=32000)
    ap.add_argument("--images", default="send", help="send | drop | describe:<model> (default send)")
    a = ap.parse_args()

    task, key = HERE / a.task, key_from(a.vault)
    t0 = time.time()
    body, described = request(task, a.model, a.max_tokens, a.images, a.base, key)
    t1 = time.time()
    reply = call(a.base, key, body)
    secs = round(time.time() - t1, 1)

    text = text_of(reply)
    m = re.search(r"```html\s*\n(.*?)```", text, re.S) or re.search(r"(<!DOCTYPE html.*</html>)", text, re.S | re.I)
    out = task / "out"
    out.mkdir(exist_ok=True)
    label = a.label or a.model.replace("/", "_")
    (out / f"{label}.txt").write_text(text)
    if m:
        (out / f"{label}.html").write_text(m.group(1))
    if described:
        (out / f"{label}.described.txt").write_text(described)
    usage = reply.get("usage", {})
    meta = {"model": reply.get("model", a.model), "base": a.base, "images": a.images,
            "describe_seconds": round(t1 - t0, 1), "seconds": secs, "stop_reason": reply.get("stop_reason"),
            "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
            "tokens_per_second": round((usage.get("output_tokens") or 0) / secs, 1) if secs else None,
            "html_found": bool(m), "html_bytes": len(m.group(1)) if m else 0}
    (out / f"{label}.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return 0 if m else 1


if __name__ == "__main__":
    raise SystemExit(main())
