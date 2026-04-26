"""Sync Anthropic client. Used as the escalation tier.

Triggered when the user said "put on your thinking cap" OR the request
included `model_hint=claude` OR Ollama was unreachable. Brief: "PC
must be online to Anthropic — design Ollama fallback for when it isn't."
"""
from typing import Iterator

from anthropic import Anthropic
from app.config import settings


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _fill_system(system: str | None) -> str:
    """Replace {KEYWORD_MEMORY} placeholder with live keyword digest."""
    raw = system or settings.skippy_system or ""
    if "{KEYWORD_MEMORY}" not in raw:
        return raw
    try:
        from app import keyword_memory as km  # noqa: PLC0415
        return raw.replace("{KEYWORD_MEMORY}", km.context_digest())
    except Exception:
        return raw.replace("{KEYWORD_MEMORY}", "(keyword memory unavailable)")


def chat(message: str, system: str | None = None) -> str:
    resp = _get_client().messages.create(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=_fill_system(system),
        messages=[{"role": "user", "content": message}],
    )
    return resp.content[0].text


def stream_chat(message: str, system: str | None = None) -> Iterator[str]:
    with _get_client().messages.stream(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=_fill_system(system),
        messages=[{"role": "user", "content": message}],
    ) as stream:
        for text in stream.text_stream:
            yield text
