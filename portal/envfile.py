"""The VM's settings live in one .env-format blob (instance metadata key
`games-env`, read by the VM's startup script). These helpers parse it and
merge changes into it without disturbing lines they don't touch. Stdlib only.

Values are written single-quoted: docker compose takes a single-quoted .env
value literally, so a password containing `$`, `#` or spaces reaches the game
server exactly as typed (unquoted, compose would interpolate `$` and cut at
` #`). A value can't contain a single quote or a newline; `check` says so.
"""
from __future__ import annotations

import re


def _value(raw: str) -> str:
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        if end != -1:
            return raw[1:end]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def parse(text: str) -> dict[str, str]:
    """KEY=value lines → dict. Blank lines, comments and a trailing
    ` # comment` are ignored and quotes removed — the same rules docker
    compose and infra/gcp-games/discord-notify.py use."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        if sep:
            out[key.strip()] = _value(value)
    return out


def check(value: str) -> str | None:
    """Why `value` can't be stored, or None if it can."""
    if "'" in value:
        return "can't contain a single quote (')"
    if "\n" in value or "\r" in value:
        return "can't span several lines"
    return None


def merge(text: str, updates: dict[str, str]) -> str:
    """Set each key in `updates`, replacing its existing line in place
    (keeping order and every other line) or appending it at the end."""
    for key, value in updates.items():
        problem = check(value)
        if problem:
            raise ValueError(f"{key} {problem}")
    lines = text.splitlines()
    pending = {k: f"{k}='{v}'" for k, v in updates.items()}
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in pending:
            lines[i] = pending.pop(key)
    lines += list(pending.values())
    return "\n".join(lines).strip("\n") + "\n"
