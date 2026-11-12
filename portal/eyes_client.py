"""Skippy Core's eyes, for the portal: one question about one picture to Core's vision slot (Qwen3-VL, its mmproj always loaded).

A small copy of services/claude-bridge/eyes.py ask(): the portal image can't import the bridge. Used by vision.py to read the writing in what the
glasses see and to say what the Captain is looking at. Thinking is off (an answer in seconds, not a thought of thousands of tokens).
"""
from __future__ import annotations

import base64
import json
import urllib.request


def ask(url: str, key: str, data: bytes, mime: str, prompt: str, max_tokens: int = 600, post=None, timeout: float = 45.0,
        model: str = "vision", system: str = "You read and describe pictures precisely.") -> str:
    """The words of Core's answer to `prompt` about the picture `data`. Raises RuntimeError when Core can't be asked or says nothing."""
    if not (url and key):
        raise RuntimeError("Skippy Core isn't configured")
    body = json.dumps({"model": model, "max_tokens": max_tokens, "system": system, "chat_template_kwargs": {"enable_thinking": False},
                       "messages": [{"role": "user", "content": [
                           {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(data).decode()}},
                           {"type": "text", "text": prompt}]}]}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/v1/messages", data=body, method="POST",
                                 headers={"x-api-key": key, "Authorization": "Bearer " + key,
                                          "anthropic-version": "2023-06-01", "Content-Type": "application/json"})
    try:
        if post is not None:
            got = post(req)
        else:
            with urllib.request.urlopen(req, timeout=timeout) as r:                       # noqa: S310 — Skippy Core, over its Funnel
                got = json.loads(r.read().decode("utf-8", "replace"))
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 — a timeout, a refused connection, a bad reply: one kind of failure for the caller
        raise RuntimeError(f"Skippy Core couldn't be asked ({type(exc).__name__})") from exc
    text = " ".join(b.get("text", "") for b in (got or {}).get("content", []) if isinstance(b, dict) and b.get("type") == "text").strip()
    if not text:
        raise RuntimeError("the vision model said nothing")
    return text
