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


def _headers() -> dict:
    """The bridge runs Claude Code, so it only answers a caller holding SKIPPY_HOME_TOKEN (the vault `.env` gives the
    same value to this container and to the bridge on the host)."""
    token = os.getenv("SKIPPY_HOME_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _refused(e: "httpx.HTTPStatusError") -> BridgeUnavailable:
    code = e.response.status_code
    if code in (401, 503):
        return BridgeUnavailable(
            f"claude-bridge refused the request (HTTP {code}): SKIPPY_HOME_TOKEN must be set, and the same, "
            "in flask-api's environment and on the PC running the bridge"
        )
    return BridgeUnavailable(f"claude-bridge HTTP {code}")


def chat(text: str, mode: str = "plan") -> str:
    """One-shot. mode = 'plan' (solid gold) or 'bypass' (awesomeness)."""
    try:
        # Generous timeout: planning sessions and Opus on bypass mode
        # can take minutes if the prompt is non-trivial.
        r = httpx.post(
            f"{BRIDGE_URL}/solid-gold",
            json={"text": text, "mode": mode, "stream": False},
            headers=_headers(),
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
        raise _refused(e) from e


def stream_chat(text: str, mode: str = "plan") -> Iterator[str]:
    try:
        with httpx.stream(
            "POST",
            f"{BRIDGE_URL}/solid-gold",
            json={"text": text, "mode": mode, "stream": True},
            headers=_headers(),
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
        raise _refused(e) from e
