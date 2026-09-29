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
    port2 = port2.replace("\r\n", "\n").replace("\r", "")   # serial ports end lines with CRLF
    start = port2.rfind(MARK)
    if start == -1:
        return ""
    end = port2.find("=== end ===", start)
    return port2[start:] if end == -1 else port2[start:end + len("=== end ===")]


_JOIN_LINE = re.compile(r"^JOIN_CODE (\w+)=(\S+)$", re.M)
_JOIN_LOG = re.compile(r'Setting \["JoinCode"\].*?value\[([^\]]+)\]')


def join_codes(snapshot: str) -> dict[str, str]:
    """Invite codes by game from a snapshot: skippy-diag's JOIN_CODE lines,
    else (older VMs) a JoinCode line still in Dragonwilds' log tail."""
    codes = dict(_JOIN_LINE.findall(snapshot))
    if "dragonwilds" not in codes:
        found = _JOIN_LOG.findall(snapshot)
        if found:
            codes["dragonwilds"] = found[-1]
    return codes


def current_join_codes(vm) -> dict[str, str]:
    """Live invite codes (≤2 minutes old). Empty when the VM is off, the
    snapshot isn't there yet, or the serial port can't be read."""
    try:
        return join_codes(latest_snapshot(vm.serial_output(2)))
    except Exception:   # noqa: BLE001 — an invite code is a nicety; never break the page
        return {}


def for_game(snapshot: str, container: str) -> str:
    """Just one game's part of a snapshot: its `docker ps` row and its log
    section (container names match game ids — games/docker-compose.yml)."""
    if not snapshot:
        return ""
    lines = snapshot.splitlines()
    out = [ln for ln in lines[1:3] if ln.startswith("NAMES")]            # the ps header
    out += [ln for ln in lines if ln.split()[:1] == [container]]         # its ps row
    marker = f"--- {container}: "
    if marker in snapshot:
        section = snapshot.split(marker, 1)[1]
        out.append(marker + section.split("\n--- ", 1)[0].rstrip())
    else:
        out.append(f"(no {container} container in the latest snapshot: not started this boot)")
    return "\n".join(out)


def startup_tail(port1: str, lines: int = STARTUP_LINES) -> str:
    """Startup-script lines from the console, last boot's worth."""
    port1 = port1.replace("\r\n", "\n").replace("\r", "")
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


def read(vm, game: str | None = None) -> dict[str, str]:
    """Both logs, masked; with `game`, only that game's container. Port 2 is
    empty until the first snapshot lands (about a minute after boot) and on
    VMs booted before this existed."""
    env = vm.status().env
    try:
        snapshot = latest_snapshot(vm.serial_output(2))
    except Exception:   # noqa: BLE001 — port 2 not written yet reads as an error
        snapshot = ""
    if game:
        snapshot = for_game(snapshot, game)
    return {
        "containers": redact(snapshot, env) or
        "No container snapshot yet: it appears about a minute after the server boots "
        "(servers started before this feature need one Restart & update).",
        "startup": redact(startup_tail(vm.serial_output(1)), env) or "(no startup output on the console)",
    }
