"""Eyes for Skippy Core's coder (the Captain's rule, 2026-10-24: Private mode must work for real, not be routed around).

The coder slot (Qwen3-Coder-Next) is text-only. Handed a picture, Core refuses it ("image type not supported") and the whole run
fails. Two places a picture reaches it, both covered here with Core's own vision slot (Qwen3-VL, with its mmproj, always loaded):

  * a picture the Captain attaches: the bridge describes it before the run starts (workshop_engine._describe_pictures);
  * a picture the agent opens itself (a screenshot of its own portal page, an image in a repo): this file is also a PreToolUse
    hook on Read. On a Core run (SKIPPY_CORE_EYES=1, set by workshop_engine.core_env) a Read of an image is refused, and the
    refusal carries what the vision slot sees, every word copied out, which Claude Code shows the model in its place.

A picture the agent opens itself is looked at the way Claude looks at its own (the Captain, 2026-11-02: "We're trying to replicate
Haiku in functionality ... we're trying to improve the tool"): Core's big model (the chat slot, Qwen3.6-35B-A3B with its own vision
projector) is handed the task from the run's transcript with the picture, describes it (text copied exactly) and then reviews it
against the task, faults worst first. Without a task, or when the big model can't answer, the vision slot's plain description.
Measured on MineBench's rematch builds: the 8B vision slot found none of the faults the blind judge saw; the 35B with thinking off
found the big ones (a train across its rails, a train lost on its plate) and passed the control build. Thinking stays off: with it
on, one review thought for 8,000 tokens and said nothing.

Exit 2 = refused with the description on stderr; 0 = carry on (any other file, or a Claude-brain run, which sees pictures itself).
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
MAX_BYTES = 12 * 1024 * 1024        # as big as a picture the agent may show (workshop_engine.MAX_SHOT_BYTES)
PROMPT = ("Describe this picture precisely for someone who can't see it and needs every detail: what it is, the layout from top "
          "to bottom, and ALL text in it copied exactly as printed (every word, number, code, symbol and logo; say which you can't "
          "read rather than guess), then colours, counts and anything unusual. No opinions.")
REVIEW_MODEL = "chat"               # the slot that reviews: Core's 35B (its mmproj is loaded: core/models.json)
REVIEW_PROMPT = ("Someone made or opened this picture while doing this task:\n\n<task>\n{task}\n</task>\n\n"
                 "Look at it the way the person who asked would, and be honest: they want the best possible result, not reassurance.\n"
                 "1. Say precisely what the picture shows: the main things, the layout, how big they are in the frame, and ALL text in it "
                 "copied exactly as printed (say which you can't read rather than guess).\n"
                 "2. Review only what can be seen (steps about files, code, commands or commits are checked elsewhere). List the faults "
                 "that matter most, worst first: anything that looks wrong, broken, misplaced, floating, sideways, out of proportion, "
                 "cut off, overlapping, lost in empty space or hard to recognise, and anything someone would expect that is missing. "
                 "Only faults you can actually see.\n"
                 "3. Say plainly whether someone who did not know the task would recognise at a glance what it is meant to be.\n"
                 "4. The three changes that would improve it most.")
TASK_CHARS = 4000


def describe(url: str, key: str, path: str, post=None, timeout: float = 180) -> str:
    """What Skippy Core's vision slot sees in the picture at `path`. Raises when Core can't be asked or says nothing."""
    data = Path(path).read_bytes()
    if len(data) > MAX_BYTES:
        raise RuntimeError(f"the picture is {len(data) // 1048576} MB, too big to send")
    return ask(url, key, data, IMAGE_TYPES.get(Path(path).suffix.lower(), "image/jpeg"), PROMPT, post=post, timeout=timeout)


def task_of(transcript: str) -> str:
    """The latest thing the person asked in this run's transcript (their words, not tool results), or ''."""
    said = ""
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "user":
                    continue
                content = (d.get("message") or {}).get("content")
                if isinstance(content, list):
                    content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
                if isinstance(content, str) and content.strip():
                    said = content.strip()
    except (OSError, TypeError):
        return ""
    return said[:TASK_CHARS]


def review(url: str, key: str, path: str, task: str, post=None, timeout: float = 120) -> str:
    """Core's big model's look at the picture at `path` against `task`: what it shows, then its faults. Raises like ask()."""
    data = Path(path).read_bytes()
    if len(data) > MAX_BYTES:
        raise RuntimeError(f"the picture is {len(data) // 1048576} MB, too big to send")
    return ask(url, key, data, IMAGE_TYPES.get(Path(path).suffix.lower(), "image/jpeg"), REVIEW_PROMPT.format(task=task),
               max_tokens=3000, post=post, timeout=timeout, model=REVIEW_MODEL, system="You review pictures honestly and precisely.")


def ask(url: str, key: str, data: bytes, mime: str, prompt: str, max_tokens: int = 1500, post=None, timeout: float = 180,
        model: str = "vision", system: str = "You describe pictures precisely.") -> str:
    """One question about one picture (its bytes) to one of Skippy Core's slots (the vision slot unless told), thinking off; its
    words. Raises when Core can't be asked or says nothing."""
    import urllib.request
    if not (url and key):
        raise RuntimeError("Skippy Core isn't configured")
    body = json.dumps({"model": model, "max_tokens": max_tokens, "system": system, "chat_template_kwargs": {"enable_thinking": False},
                       "messages": [{"role": "user", "content": [
                           {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(data).decode()}},
                           {"type": "text", "text": prompt}]}]}).encode()
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
    url, key = env.get("ANTHROPIC_BASE_URL", ""), env.get("ANTHROPIC_API_KEY", "")
    task = task_of(str(event.get("transcript_path") or ""))
    if task:
        try:
            seen = review(url, key, path, task, post=post)
            return 2, (f"You can't see pictures, so this Read was not done (it would fail the whole run). Skippy Core's big model "
                       f"looked at {path} for you instead, with your task in mind. What it sees, and what it found wrong:\n\n{seen}")
        except Exception:                                        # noqa: BLE001 — the vision slot's description is still worth having
            pass
    try:
        seen = describe(url, key, path, post=post, timeout=60)
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
