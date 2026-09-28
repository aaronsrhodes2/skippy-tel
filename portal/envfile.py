"""The VM's settings live in one .env-format blob (instance metadata key
`games-env`, read by the VM's startup script). These helpers parse it and
merge changes into it without disturbing lines they don't touch. Stdlib only.
"""
from __future__ import annotations

import re


def parse(text: str) -> dict[str, str]:
    """KEY=value lines → dict. Blank lines, comments and a trailing
    ` # comment` are ignored — the same rules docker compose and
    infra/gcp-games/discord-notify.py use."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        if sep:
            out[key.strip()] = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    return out


def merge(text: str, updates: dict[str, str]) -> str:
    """Set each key in `updates`, replacing its existing line in place
    (keeping order and every other line) or appending it at the end."""
    lines = text.splitlines()
    pending = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in pending:
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    return "\n".join(lines).strip("\n") + "\n"
