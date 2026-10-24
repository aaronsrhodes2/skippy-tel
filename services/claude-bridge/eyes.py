"""Eyes for Skippy Core's coder (the Captain's rule, 2026-10-24: Private mode must work for real, not be routed around).

The coder slot (Qwen3-Coder-Next) is text-only. Handed a picture, Core refuses it ("image type not supported") and the whole run
fails. Two places a picture reaches it, both covered here with Core's own vision slot (Qwen3-VL, with its mmproj, always loaded):

  * a picture the Captain attaches: the bridge describes it before the run starts (workshop_engine._describe_pictures);
  * a picture the agent opens itself (a screenshot of its own portal page, an image in a repo): this file is also a PreToolUse
    hook on Read. On a Core run (SKIPPY_CORE_EYES=1, set by workshop_engine.core_env) a Read of an image is refused, and the
    refusal carries what the vision slot sees, every word copied out, which Claude Code shows the model in its place.

Exit 2 = refused with the description on stderr; 0 = carry on (any other file, or a Claude-brain run, which sees pictures itself).
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
MAX_BYTES = 8 * 1024 * 1024
PROMPT = ("Describe this picture precisely for someone who can't see it and needs every detail: what it is, the layout from top "
          "to bottom, and ALL text in it copied exactly as printed (every word, number, code, symbol and logo; say which you can't "
          "read rather than guess), then colours, counts and anything unusual. No opinions.")


def describe(url: str, key: str, path: str, post=None, timeout: float = 180) -> str:
    """What Skippy Core's vision slot sees in the picture at `path`. Raises when Core can't be asked or says nothing."""
    import urllib.request
    if not (url and key):
        raise RuntimeError("Skippy Core isn't configured")
    data = Path(path).read_bytes()
    if len(data) > MAX_BYTES:
        raise RuntimeError(f"the picture is {len(data) // 1048576} MB, too big to send")
    mime = IMAGE_TYPES.get(Path(path).suffix.lower(), "image/jpeg")
    body = json.dumps({"model": "vision", "max_tokens": 1500, "system": "You describe pictures precisely.",
                       "messages": [{"role": "user", "content": [
                           {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(data).decode()}},
                           {"type": "text", "text": PROMPT}]}]}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/v1/messages", data=body, method="POST",
                                 headers={"x-api-key": key, "Authorization": "Bearer " + key,
                                          "anthropic-version": "2023-06-01", "Content-Type": "application/json"})
    if post is not None:
        got = post(req)
    else:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.loads(r.read().decode("utf-8", "replace"))
    text = " ".join(b.get("text", "") for b in (got or {}).get("content", []) if isinstance(b, dict) and b.get("type") == "text").strip()
    if not text:
        raise RuntimeError("the vision model said nothing")
    return text


def check(event: dict, env=os.environ, post=None) -> tuple[int, str]:
    """(exit code, message) for one PreToolUse event."""
    if env.get("SKIPPY_CORE_EYES") != "1" or event.get("tool_name") != "Read":
        return 0, ""
    path = str((event.get("tool_input") or {}).get("file_path") or "")
    if Path(path).suffix.lower() not in IMAGE_TYPES:
        return 0, ""
    if not Path(path).is_absolute():
        path = str(Path(env.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".") / path)
    if not Path(path).is_file():
        return 0, ""                                             # let Read say the file isn't there
    try:
        seen = describe(env.get("ANTHROPIC_BASE_URL", ""), env.get("ANTHROPIC_API_KEY", ""), path, post=post)
    except Exception as exc:                                     # noqa: BLE001 — still refuse: the coder can't take the picture either way
        return 2, (f"You can't see pictures, and Skippy Core's vision model couldn't look at {path} for you "
                   f"({type(exc).__name__}: {exc}). Don't open image files; say what you needed from it.")
    return 2, (f"You can't see pictures, so this Read was not done (it would fail the whole run). Skippy Core's vision model "
               f"looked at {path} for you instead. What it sees:\n\n{seen}")


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    code, message = check(event)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
