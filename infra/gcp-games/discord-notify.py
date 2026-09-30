#!/usr/bin/env python3
"""Per-game Discord status for the pop-up game servers.

  skippy-discord online                 every game's pinned post → current state; ping started games
  skippy-discord offline "<reason>"     every game's pinned post → asleep; ping games that were up

Each game has its own text channel (made by scripts/discord_setup.py) with a
webhook and a pinned status post that this script edits in place: connection
details, description, online/offline. Configuration comes from /srv/games/.env
(uploaded from games/.env by `games.sh env`):

  DISCORD_WEBHOOK_<GAME>   webhook of that game's text channel
  DISCORD_BOARD_<GAME>     id of the pinned status post in that channel

Games without a webhook are skipped. If a pinned post was deleted, a new one
is posted (unpinned; re-run discord_setup.py to pin it) and its id kept on
the data disk. Stdlib only: runs on a stock Debian VM.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENV = Path("/srv/games/.env")
STATE = Path("/srv/games/data/.discord")
META = "http://metadata.google.internal/computeMetadata/v1/instance"
USERNAME = "Skippy Game Servers"
# Where the pinned post sends people. PORTAL_URL in the VM's .env overrides it (the portal's Discord setup
# writes it from the address it is served on, so a custom domain later needs no code change).
PORTAL_URL = "https://skippy-portal-1015050363686.us-west1.run.app"

# id → (display name, description, how-to-connect(ip, env))
GAMES = {
    "valheim": (
        "Valheim",
        "Viking survival co-op. Dedicated world: it keeps its state between sessions.",
        lambda ip, e: (("Join Game → **Add server** → the 6-digit join code on the Skippy Portal (a new one every boot)"
                        if (e.get("VALHEIM_CROSSPLAY") or "false").strip().lower() in ("true", "1", "yes", "on")
                        else f"Join Game → Add server → `{ip}:2456`") + "\nPassword: on the Skippy Portal's Valheim page"),
    ),
    "factorio": (
        "Factorio",
        "Factory-building co-op. The factory must grow.",
        lambda ip, e: f"Multiplayer → Connect to address → `{ip}:34197`",
    ),
    "satisfactory": (
        "Satisfactory",
        "3D factory-building co-op on an alien planet.",
        lambda ip, e: (f"Main menu → **Server Manager** → Add Server → address `{ip}`, port `7777` (two boxes)\n"
                       "Not Multiplayer → Join Game: that errors with “Encryption Token Missing”\n"
                       "The first player to connect claims the server: do it right away"),
    ),
    "dragonwilds": (
        "RuneScape: Dragonwilds",
        "Survival crafting in Ashenfall. Crossplay: PC, Xbox, PS5 and Switch 2.",
        lambda ip, e: ("Worlds → **Invite Code** → the code on the Skippy Portal (a new one every boot)"
                       + ("\nWorld password: next to the code on the Skippy Portal"
                          if e.get("DRAGONWILDS_WORLD_PASSWORD") else "")
                       + (f"\nOr: Direct tab → `{ip}:{e.get('DRAGONWILDS_PORT') or '7778'}`" if ip else "")
                       + f"\nOr: Public tab → search **{e.get('DRAGONWILDS_SERVER_NAME') or 'Skippy'}** "
                         "(the whole name, exact spelling and case)"),
    ),
    "everquest": (
        "EverQuest",
        "Golden Era Norrath (EQEmu): bots, mercs, XP x3, no death penalty.",
        lambda ip, e: (f"RoF2 client: eqhost.txt = `[LoginServer]` then `Host={ip}:5999` (not the old braces format), "
                       "run `eqgame.exe patchme`; the exact block is on the Skippy Portal"),
    ),
}


def load_env(path: Path | None = None) -> dict:
    path = path or ENV
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, val = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                val = val.strip()
                if val[:1] in ("'", '"') and val.find(val[0], 1) != -1:
                    val = val[1:val.find(val[0], 1)]     # quoted: literal (portal writes 'value')
                else:
                    val = val.split(" #")[0].strip()
                out[key.strip()] = val
    return out


def meta(path: str, default: str = "") -> str:
    req = urllib.request.Request(f"{META}/{path}", headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.read().decode().strip()
    except OSError:
        return default


def call(method: str, url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json",
        "User-Agent": "skippy-games (https://github.com/aaronsrhodes2/skippy-tel, 1.0)",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def portal_url(e: dict) -> str:
    """The portal's address: PORTAL_URL from the env if it's a plain https origin, else the default."""
    url = (e.get("PORTAL_URL") or "").strip().rstrip("/")
    found = re.fullmatch(r"https://[A-Za-z0-9.-]+(?::([0-9]{1,5}))?", url)
    return url if found and (not found.group(1) or 1 <= int(found.group(1)) <= 65535) else PORTAL_URL


def board(game: str, online: bool, ip: str, idle: str, reason: str, e: dict, now: int) -> dict:
    """The pinned status post for one game."""
    name, description, connect = GAMES[game]
    # Everyone reading the channel can open the game's page; a signed-in stranger is offered a way to ask for access.
    portal = {"name": "Skippy Portal",
              "value": f"[Open {name} on the Skippy Portal]({portal_url(e)}/games/{game})\n"
                       "No access yet? Open it, sign in with Google and tap **Request access**.",
              "inline": False}
    if online:
        auto = "never" if idle == "0" else f"after {idle} min with nobody playing"
        fields = [
            {"name": "Status", "value": f"🟢 **Online** since <t:{now}:R>\n"
                                        "First boot after an update can take 5–10 min.", "inline": False},
            {"name": "How to connect", "value": connect(ip, e), "inline": False},
            {"name": "Auto-sleep", "value": auto, "inline": False},
            portal,
        ]
        color = 0x00FF88                                   # ChatPalette.Green
    else:
        why = f": {reason}" if reason else ""
        fields = [
            {"name": "Status", "value": f"⚫ **Asleep** since <t:{now}:R>{why}", "inline": False},
            {"name": "How to connect", "value": "Ask Aaron / Skippy to start the server. "
                                                "Connection details appear here when it's up.",
             "inline": False},
            portal,
        ]
        color = 0x555555
    return {"username": USERNAME, "embeds": [{
        "title": name, "description": description, "color": color, "fields": fields,
        "footer": {"text": "Pinned status. Updates itself when the server starts or sleeps."},
    }]}


def update_game(hook: str, payload: dict, candidates: list[str]) -> str:
    """Edit the first status post that still exists (the pinned one from
    discord_setup.py, then any replacement), else post a new one. Returns its id."""
    for board_id in filter(None, candidates):
        try:
            call("PATCH", f"{hook}/messages/{board_id}", payload)
            return board_id
        except urllib.error.HTTPError as err:
            if err.code != 404:
                raise
    return call("POST", f"{hook}?wait=true", payload)["id"]


def main(argv: list[str]) -> int:
    online = (argv[1] if len(argv) > 1 else "online") == "online"
    reason = argv[2] if len(argv) > 2 else ""
    e = load_env()
    running = meta("attributes/games").split()
    ip = meta("network-interfaces/0/access-configs/0/external-ip", "?")
    idle = meta("attributes/idle-minutes", "30")
    now = int(datetime.now(timezone.utc).timestamp())
    STATE.mkdir(parents=True, exist_ok=True)

    failed = False
    for game in GAMES:
        hook = e.get(f"DISCORD_WEBHOOK_{game.upper()}", "")
        if not hook:
            continue
        is_up = online and game in running
        pinned = e.get(f"DISCORD_BOARD_{game.upper()}", "")
        saved = STATE / f"{game}.id"           # replacement post, if the pinned one was deleted
        replacement = saved.read_text().strip() if saved.exists() else ""
        try:
            used = update_game(hook, board(game, is_up, ip, idle, reason, e, now), [pinned, replacement])
            if used == pinned:
                saved.unlink(missing_ok=True)
            else:
                saved.write_text(used)
            if game in running:                # ping only the channels of games in this session
                name = GAMES[game][0]
                text = (f"🟢 **{name}** is starting up. Connection details are in the pinned post."
                        if online else f"💤 **{name}** went to sleep{': ' + reason if reason else '.'}")
                call("POST", hook, {"username": USERNAME, "content": text})
        except (OSError, KeyError, ValueError) as err:
            print(f"discord {game}: {err}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
