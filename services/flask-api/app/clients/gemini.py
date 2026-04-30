"""Sync Gemini client over Gemini's REST API.

Two deliberate choices baked in:

1. **httpx, not google-generativeai SDK.** That SDK uses gRPC, which
   doesn't cooperate with gunicorn's gevent worker — gevent's
   monkey-patched socket can't share the event loop with grpc's, and
   SSE responses hang after the first yield. REST is plain HTTP, same
   pattern as ollama_client and anthropic_client.

2. **`X-goog-api-key` header, not `?key=...` query param.** If httpx's
   HTTPStatusError or any other upstream message ends up surfaced to
   the client, the URL it carries won't include the API key. Defense-
   in-depth on top of version.py's scrub() (which redacts URLs anyway).

Triggered by "ask gemini" in user text (see intent.py tier registry).
Per the explicit-only rule, never auto-escalates from another tier.
"""
import json
from typing import Iterator

import httpx
from app.config import settings


_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-goog-api-key": settings.gemini_api_key,
    }


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


def _payload(message: str, system: str | None) -> dict:
    return {
        "systemInstruction": {
            "parts": [{"text": _fill_system(system)}],
        },
        "contents": [
            {"role": "user", "parts": [{"text": message}]},
        ],
    }


def _extract_text(response_obj: dict) -> str:
    out = []
    for cand in response_obj.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            t = part.get("text")
            if t:
                out.append(t)
    return "".join(out)


def chat(message: str, system: str | None = None) -> str:
    from app import model_watchdog as mw  # noqa: PLC0415
    mw.record("nagatha", message[:2000], settings.gemini_model)
    try:
        url = f"{_BASE}/{settings.gemini_model}:generateContent"
        r = httpx.post(url, headers=_headers(),
                       json=_payload(message, system), timeout=60.0)
        r.raise_for_status()
        result = _extract_text(r.json())
        mw.clear("nagatha")
        return result
    except Exception:
        raise


def stream_chat(message: str, system: str | None = None) -> Iterator[str]:
    from app import model_watchdog as mw  # noqa: PLC0415
    mw.record("nagatha", message[:2000], settings.gemini_model)
    try:
        url = f"{_BASE}/{settings.gemini_model}:streamGenerateContent?alt=sse"
        with httpx.stream("POST", url, headers=_headers(),
                          json=_payload(message, system), timeout=60.0) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                text = _extract_text(obj)
                if text:
                    yield text
        mw.clear("nagatha")
    except Exception:
        raise
