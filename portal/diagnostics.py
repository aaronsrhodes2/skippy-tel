"""Server logs for admins, read from the VM's serial ports (gcp.py
serial_output): the startup script's output on the console (port 1) and the
latest skippy-diag snapshot of the game containers (port 2, written every
2 minutes by infra/gcp-games/startup.sh). Secret setting values are masked
before anything is shown. Stdlib only.
"""
from __future__ import annotations

import re

from catalog import GAMES

MARK = "=== skippy-diag "
STARTUP_LINES = 80
_STARTUP = re.compile(r"startup-script|google_metadata_script_runner")


def latest_snapshot(port2: str) -> str:
    start = port2.rfind(MARK)
    if start == -1:
        return ""
    end = port2.find("=== end ===", start)
    return port2[start:] if end == -1 else port2[start:end + len("=== end ===")]


def startup_tail(port1: str, lines: int = STARTUP_LINES) -> str:
    """Startup-script lines from the console, last boot's worth."""
    picked = [ln for ln in port1.splitlines() if _STARTUP.search(ln)]
    boots = [i for i, ln in enumerate(picked) if "=== skippy-games boot" in ln]
    if boots:
        picked = picked[boots[-1]:]
    return "\n".join(picked[-lines:])


def redact(text: str, env: dict[str, str]) -> str:
    """Mask every secret setting's value (passwords) wherever it appears."""
    for game in GAMES.values():
        for s in game.settings:
            value = env.get(s.key, "")
            if s.secret and len(value) >= 3:
                text = text.replace(value, "••••")
    return text


def read(vm) -> dict[str, str]:
    """Both logs, masked. Port 2 is empty until the first snapshot lands
    (about a minute after boot) and on VMs booted before this existed."""
    env = vm.status().env
    try:
        snapshot = latest_snapshot(vm.serial_output(2))
    except Exception:   # noqa: BLE001 — port 2 not written yet reads as an error
        snapshot = ""
    return {
        "containers": redact(snapshot, env) or
        "No container snapshot yet: it appears about a minute after the server boots "
        "(servers started before this feature need one Restart & update).",
        "startup": redact(startup_tail(vm.serial_output(1)), env) or "(no startup output on the console)",
    }
