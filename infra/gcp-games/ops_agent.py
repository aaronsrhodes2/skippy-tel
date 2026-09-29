#!/usr/bin/env python3
"""On-VM half of the `ops` workflow: answers requests Claude sends through
instance metadata, with no SSH and no open port.

The skippy-ops service (startup.sh) runs this file fresh from metadata
(`ops-agent`) in a loop, so a deploy changes it without a reboot. Each run
long-polls metadata `ops-request` for up to a minute; a request it hasn't
answered yet is run and the reply written to serial port 3 (/dev/ttyS2),
which scripts/remote.py reads through the Compute API:

    ops-request  {"id": "ab12", "cmd": "search", "args": ["dragonwilds", "join"]}
    serial 3     === ops-reply ab12 ===\n…output…\n=== ops-end ab12 ===

Only the commands in COMMANDS exist; arguments are validated and passed to
programs directly (no shell). Output is capped. It never reads games-env, but
game logs can echo secrets, so remote.py masks settings values in replies.
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from pathlib import Path

META = "http://metadata.google.internal/computeMetadata/v1/instance/attributes/ops-request"
STATE = Path("/var/lib/skippy/ops-last-id")
PORT = "/dev/ttyS2"
MAX_OUT = 60_000
GAME = re.compile(r"^[a-z0-9_-]{1,32}$")


def run(*argv: str, timeout: int = 60) -> str:
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return (p.stdout + p.stderr).strip()


def container(name: str) -> str:
    if not GAME.match(name or ""):
        raise ValueError(f"bad game name {name!r}")
    known = run("docker", "ps", "-a", "--format", "{{.Names}}").split()
    if name not in known:
        raise ValueError(f"no container {name!r} (have: {', '.join(known) or 'none'}) — is it running this boot?")
    return name


def _int(value, default: int, cap: int) -> int:
    try:
        return max(1, min(int(value), cap))
    except (TypeError, ValueError):
        return default


def cmd_tail(args):
    return run("docker", "logs", "--tail", str(_int(args[1] if len(args) > 1 else None, 200, 2000)),
               container(args[0] if args else ""))


def cmd_search(args):
    if len(args) < 2:
        raise ValueError("usage: search <game> <regex> [max matches]")
    name, pattern = container(args[0]), re.compile(args[1], re.I)
    limit = _int(args[2] if len(args) > 2 else None, 200, 1000)
    hits = [ln for ln in run("docker", "logs", name, timeout=120).splitlines() if pattern.search(ln)]
    return f"{len(hits)} matching lines (showing last {min(limit, len(hits))})\n" + "\n".join(hits[-limit:])


def cmd_stats(_args):
    parts = [
        ("uptime / load", run("uptime")),
        ("cpu", run("sh", "-c", "top -bn1 | head -5")),   # fixed command line, no user input
        ("memory", run("free", "-m")),
        ("disk", run("df", "-h", "/", "/srv/games/data")),
        ("containers", run("docker", "stats", "--no-stream", "--format",
                           "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}")),
        ("docker ps", run("docker", "ps", "-a", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}")),
    ]
    return "\n\n".join(f"--- {title}\n{body}" for title, body in parts)


def cmd_restart_game(args):
    name = container(args[0] if args else "")
    return run("docker", "restart", "--time", "60", name, timeout=180) + f"\nrestarted {name}"


COMMANDS = {"tail": cmd_tail, "search": cmd_search, "stats": cmd_stats, "restart-game": cmd_restart_game}


def reply(req_id: str, text: str) -> None:
    if len(text) > MAX_OUT:
        text = f"(output truncated to the last {MAX_OUT} characters)\n" + text[-MAX_OUT:]
    with open(PORT, "w") as port:
        port.write(f"\n=== ops-reply {req_id} ===\n{text}\n=== ops-end {req_id} ===\n")


def main() -> None:
    req = urllib.request.Request(META + "?wait_for_change=true&timeout_sec=55",
                                 headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=65) as resp:
            body = json.loads(resp.read() or b"{}")
    except Exception:  # noqa: BLE001 — no request key yet, or a blip: the loop retries
        return
    req_id = str(body.get("id", ""))
    if not req_id or (STATE.exists() and STATE.read_text() == req_id):
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(req_id)
    handler = COMMANDS.get(body.get("cmd"))
    try:
        out = handler([str(a) for a in body.get("args", [])]) if handler else \
            f"unknown command {body.get('cmd')!r}; have: {', '.join(COMMANDS)}"
    except Exception as exc:  # noqa: BLE001 — report, don't die
        out = f"error: {exc}"
    reply(req_id, out)


if __name__ == "__main__":
    main()
