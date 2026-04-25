"""Sync Ollama client. httpx for both single-shot + streaming.

Raises OllamaUnavailable when the daemon is unreachable so callers can
fall back to Anthropic. Brief: "Claude vs Ollama routing — Ollama
default, Anthropic fallback when unreachable."
"""
import json
from typing import Iterator

import httpx
from app.config import settings


class OllamaUnavailable(Exception):
    pass


def _post(path: str, payload: dict, *, stream: bool = False, timeout: float = 120.0):
    url = f"{settings.ollama_base_url}{path}"
    try:
        if stream:
            return httpx.stream("POST", url, json=payload, timeout=timeout)
        r = httpx.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        return r
    except httpx.ConnectError as e:
        raise OllamaUnavailable("ollama unreachable") from e
    except httpx.HTTPStatusError as e:
        raise OllamaUnavailable(f"ollama HTTP {e.response.status_code}") from e


def _with_system(messages: list[dict], system: str | None) -> list[dict]:
    """Prepend a system message if provided and not already present."""
    prompt = system or settings.skippy_system
    if not prompt:
        return messages
    # Don't double-inject if the caller already included a system turn.
    if messages and messages[0].get("role") == "system":
        return messages
    return [{"role": "system", "content": prompt}] + messages


def chat(messages: list[dict], model: str | None = None,
         system: str | None = None) -> str:
    payload = {
        "model": model or settings.ollama_model,
        "messages": _with_system(messages, system),
        "stream": False,
    }
    r = _post("/api/chat", payload)
    return r.json()["message"]["content"]


def stream_chat(messages: list[dict], model: str | None = None,
                system: str | None = None) -> Iterator[str]:
    payload = {
        "model": model or settings.ollama_model,
        "messages": _with_system(messages, system),
        "stream": True,
    }
    with _post("/api/chat", payload, stream=True) as r:
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise OllamaUnavailable(f"ollama HTTP {e.response.status_code}") from e
        for line in r.iter_lines():
            if not line:
                continue
            data = json.loads(line)
            chunk = data.get("message", {}).get("content")
            if chunk:
                yield chunk
            if data.get("done"):
                return
