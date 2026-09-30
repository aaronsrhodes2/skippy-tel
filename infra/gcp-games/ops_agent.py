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


def run_rc(*argv: str, timeout: int = 60) -> tuple[int, str]:
    """Like run(), but keeps the exit status: a failed command must not read as a success."""
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


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


BACKUP = "/usr/local/bin/skippy-game-backup"      # game_backup.py, fetched at boot (startup.sh)
ARCHIVE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
EVERQUEST = "/usr/local/bin/skippy-everquest"


def cmd_backups(args):
    """What each game's rollback points are, and which files are flagged as backup targets."""
    game = args[0] if args else ""
    if game and not GAME.match(game):
        raise ValueError(f"bad game name {game!r}")
    return run("python3", BACKUP, "list", *([game] if game else []))


def cmd_restore_game(args):
    """Roll one game back to an archive (`backups <game>` lists them; `latest` = the newest). The archive is checked
    first, before anything stops. Then the game is stopped, the flagged files are set aside (kept until someone
    deletes them) and replaced, and the game is started again. A failure is an error, never a success message."""
    if len(args) < 2:
        raise ValueError("usage: restore-game <game> <archive name | latest>")
    game, name = args[0], args[1]
    if not GAME.match(game) or not ARCHIVE.match(name):
        raise ValueError("bad game or archive name")
    if not Path(BACKUP).exists():
        raise RuntimeError("the backup tool isn't on this VM yet: it is installed at boot, so restart the server once")
    rc, out = run_rc("python3", BACKUP, "check", game, name)
    if rc:
        raise ValueError(f"nothing was stopped or changed: {out}")
    everquest = game == "everquest"
    was_up = game in run("docker", "ps", "--format", "{{.Names}}").split()
    if everquest:
        run(EVERQUEST, "stop", timeout=170)
    elif was_up:
        run("docker", "stop", "--time", "90", game, timeout=180)
    try:
        rc, out = run_rc("python3", BACKUP, "restore", game, name, timeout=600)
    finally:
        if was_up and not everquest:
            run("docker", "start", game, timeout=60)
    if rc:
        raise RuntimeError(f"the restore failed and the files are as they were: {out}"
                           + (f" ({game} was started again)" if was_up and not everquest else ""))
    if everquest:
        return out + "\nEverQuest is stopped. `restart` (the whole server) brings it back up on the restored world."
    return out + (f"\n{game} was started again." if was_up else f"\n{game} wasn't running; it loads this world on its next start.")


COMMANDS = {"tail": cmd_tail, "search": cmd_search, "stats": cmd_stats, "restart-game": cmd_restart_game,
            "backups": cmd_backups, "restore-game": cmd_restore_game}


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
