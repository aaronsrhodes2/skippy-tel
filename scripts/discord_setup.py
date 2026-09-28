#!/usr/bin/env python3
"""One-time Discord setup for the game servers. Safe to re-run.

For each game it makes sure the Discord server has:
  • a text channel   (#valheim, #factorio, #satisfactory, #dragonwilds) under "Game Servers"
  • a voice channel  with the same name
  • a webhook in the text channel (the VM posts through it; no bot on the VM)
  • a pinned status post: description + connection details, edited in place later

…and writes DISCORD_WEBHOOK_<GAME> / DISCORD_BOARD_<GAME> into games/.env.
Then run `scripts/games.sh env` to hand them to the VM.

Needs a bot token only here, on your PC (see docs/games/README.md → Discord):

  DISCORD_BOT_TOKEN=... python scripts/discord_setup.py --invite gPTVS7Hmj
  DISCORD_BOT_TOKEN=... python scripts/discord_setup.py --guild 1234567890 --games valheim dragonwilds
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "games" / ".env"
API = os.environ.get("DISCORD_API", "https://discord.com/api/v10")
CATEGORY = "Game Servers"
TEXT, VOICE, CATEGORY_TYPE = 0, 2, 4

# Reuse the exact post layout the VM uses.
_spec = importlib.util.spec_from_file_location(
    "discord_notify", ROOT / "infra" / "gcp-games" / "discord-notify.py")
notify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notify)


class Discord:
    def __init__(self, token: str):
        self.token = token

    def __call__(self, method: str, path: str, payload: dict | None = None, auth: bool = True):
        url = path if path.startswith("http") else f"{API}{path}"
        headers = {"Content-Type": "application/json",
                   "User-Agent": "skippy-games-setup (https://github.com/aaronsrhodes2/skippy-tel, 1.0)"}
        if auth:
            headers["Authorization"] = f"Bot {self.token}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            return json.loads(body) if body else {}


def set_env(lines: list[str], key: str, value: str) -> list[str]:
    pattern = re.compile(rf"^{re.escape(key)}=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            return lines
    return lines + [f"{key}={value}"]


def ensure_channel(api, guild: str, channels: list, name: str, kind: int, parent: str | None,
                   topic: str | None = None) -> dict:
    for ch in channels:
        if ch["name"].lower() == name.lower() and ch["type"] == kind:
            return ch
    body = {"name": name, "type": kind}
    if parent:
        body["parent_id"] = parent
    if topic:
        body["topic"] = topic
    ch = api("POST", f"/guilds/{guild}/channels", body)
    channels.append(ch)
    print(f"  created {'#' if kind == TEXT else '🔊 ' if kind == VOICE else ''}{name}")
    return ch


def ensure_webhook(api, channel_id: str) -> str:
    for hook in api("GET", f"/channels/{channel_id}/webhooks"):
        if hook.get("name") == notify.USERNAME and hook.get("token"):
            return f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}"
    hook = api("POST", f"/channels/{channel_id}/webhooks", {"name": notify.USERNAME})
    return f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}"


def ensure_board(api, hook: str, channel_id: str, game: str, board_id: str, env: dict) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    payload = notify.board(game, False, "", "30", "", env, now)
    if board_id:
        try:
            api("PATCH", f"{hook}/messages/{board_id}", payload, auth=False)
        except urllib.error.HTTPError as err:
            if err.code != 404:
                raise
            board_id = ""
    if not board_id:
        board_id = api("POST", f"{hook}?wait=true", payload, auth=False)["id"]
        print("  posted status post")
    api("PUT", f"/channels/{channel_id}/pins/{board_id}")
    return board_id


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--guild", help="Discord server id")
    where.add_argument("--invite", help="invite code or link, e.g. gPTVS7Hmj")
    ap.add_argument("--games", nargs="+", default=list(notify.GAMES), choices=list(notify.GAMES))
    args = ap.parse_args(argv)

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        sys.exit("set DISCORD_BOT_TOKEN (see docs/games/README.md → Discord)")
    if not ENV_FILE.exists():
        sys.exit("copy games/.env.example to games/.env first")
    api = Discord(token)

    guild = args.guild
    if args.invite:
        code = args.invite.rstrip("/").rsplit("/", 1)[-1]
        guild = api("GET", f"/invites/{code}", auth=False)["guild"]["id"]
    print(f"Discord server {guild}")

    channels = api("GET", f"/guilds/{guild}/channels")
    category = ensure_channel(api, guild, channels, CATEGORY, CATEGORY_TYPE, None)
    lines = ENV_FILE.read_text().splitlines()
    env = notify.load_env(ENV_FILE)

    for game in args.games:
        name, description, _ = notify.GAMES[game]
        print(f"{name}:")
        text = ensure_channel(api, guild, channels, game, TEXT, category["id"],
                              topic=f"{description} Status + connection details are pinned.")
        ensure_channel(api, guild, channels, name.split(":")[-1].strip(), VOICE, category["id"])
        hook = ensure_webhook(api, text["id"])
        board = ensure_board(api, hook, text["id"], game,
                             env.get(f"DISCORD_BOARD_{game.upper()}", ""), env)
        lines = set_env(lines, f"DISCORD_WEBHOOK_{game.upper()}", hook)
        lines = set_env(lines, f"DISCORD_BOARD_{game.upper()}", board)

    ENV_FILE.write_text("\n".join(lines) + "\n")
    print(f"\nwrote webhooks + pinned post ids to {ENV_FILE.relative_to(ROOT)}")
    print("next: scripts/games.sh env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
