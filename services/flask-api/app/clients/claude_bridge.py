"""Sync client for the host-side Claude Code bridge.

Runs INSIDE the docker container; reaches the bridge running on the
Windows host via host.docker.internal. The bridge in turn shells out
to `claude -p` using Aaron's OAuth subscription credentials.

See services/claude-bridge/bridge.py for the other half.
"""
import json
import os
from typing import Iterator

import httpx


BRIDGE_URL = os.getenv("CLAUDE_BRIDGE_URL", "http://host.docker.internal:9337")


class BridgeUnavailable(Exception):
    pass


def chat(text: str, mode: str = "plan") -> str:
    """One-shot. mode = 'plan' (solid gold) or 'bypass' (awesomeness)."""
    try:
        # Generous timeout: planning sessions and Opus on bypass mode
        # can take minutes if the prompt is non-trivial.
        r = httpx.post(
            f"{BRIDGE_URL}/solid-gold",
            json={"text": text, "mode": mode, "stream": False},
            timeout=600.0,
        )
        r.raise_for_status()
        body = r.json()
        return (body.get("output") or "").strip()
    except httpx.ConnectError as e:
        raise BridgeUnavailable(
            "claude-bridge unreachable — start it with `make bridge` on the host"
        ) from e
    except httpx.HTTPStatusError as e:
        raise BridgeUnavailable(
            f"claude-bridge HTTP {e.response.status_code}"
        ) from e


def stream_chat(text: str, mode: str = "plan") -> Iterator[str]:
    try:
        with httpx.stream(
            "POST",
            f"{BRIDGE_URL}/solid-gold",
            json={"text": text, "mode": mode, "stream": True},
            timeout=600.0,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("done"):
                    return
                t = obj.get("text")
                if t:
                    yield t
    except httpx.ConnectError as e:
        raise BridgeUnavailable(
            "claude-bridge unreachable — start it with `make bridge` on the host"
        ) from e
    except httpx.HTTPStatusError as e:
        raise BridgeUnavailable(
            f"claude-bridge HTTP {e.response.status_code}"
        ) from e
