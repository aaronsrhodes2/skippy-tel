"""Discord setup and links, shared by the portal's admin page and
scripts/discord_setup.py. Stdlib only.

setup() makes sure the Discord server has, for each game:
  • a text channel (#valheim …) and a voice channel (🔊 Valheim …) under "Game Servers"
  • a webhook in the text channel (the VM posts status through it; no bot on the VM)
  • a pinned status post (description + how to connect), edited in place by the VM
and returns the keys to merge into the VM's settings (games-env):
  DISCORD_GUILD_ID, DISCORD_{CHANNEL,VOICE,WEBHOOK,BOARD}_<GAME>

Needs a bot token (only here, never on the VM) invited with Manage Channels,
Manage Webhooks, Manage Messages, View Channels, Send Messages.
"""
from __future__ import annotations

import importlib.util
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = os.environ.get("DISCORD_API", "https://discord.com/api/v10")
CATEGORY = "Game Servers"
TEXT, VOICE, CATEGORY_TYPE = 0, 2, 4


def _load_notify():
    """The VM's status-post layout lives in infra/gcp-games/discord-notify.py.
    The portal image carries a copy as discord_notify.py; from the repo, load it by path."""
    try:
        import discord_notify  # type: ignore[import-not-found]
        return discord_notify
    except ImportError:
        path = Path(__file__).resolve().parents[1] / "infra" / "gcp-games" / "discord-notify.py"
        spec = importlib.util.spec_from_file_location("discord_notify", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


notify = _load_notify()


# ── links (pure) ──────────────────────────────────────────────────────────────

def links(env: dict[str, str], game: str) -> dict[str, str | None]:
    """URLs for a game's Discord channels, or None where not set up yet."""
    guild = env.get("DISCORD_GUILD_ID")
    def url(key):
        channel = env.get(f"{key}_{game.upper()}")
        return f"https://discord.com/channels/{guild}/{channel}" if guild and channel else None
    return {"text": url("DISCORD_CHANNEL"), "voice": url("DISCORD_VOICE")}


# ── REST ──────────────────────────────────────────────────────────────────────

class Discord:
    def __init__(self, token: str):
        self.token = token

    def __call__(self, method: str, path: str, payload: dict | None = None, auth: bool = True):
        url = path if path.startswith("http") else f"{API}{path}"
        headers = {"Content-Type": "application/json",
                   "User-Agent": "skippy-portal (https://github.com/aaronsrhodes2/skippy-tel, 1.0)"}
        if auth:
            headers["Authorization"] = f"Bot {self.token}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            return json.loads(body) if body else {}


def guild_from_invite(api: Discord, invite: str) -> str:
    code = invite.rstrip("/").rsplit("/", 1)[-1]
    return api("GET", f"/invites/{code}", auth=False)["guild"]["id"]


def _ensure_channel(api, guild, channels, name, kind, parent=None, topic=None) -> dict:
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
    return ch


def _ensure_webhook(api, channel_id) -> str:
    for hook in api("GET", f"/channels/{channel_id}/webhooks"):
        if hook.get("name") == notify.USERNAME and hook.get("token"):
            return f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}"
    hook = api("POST", f"/channels/{channel_id}/webhooks", {"name": notify.USERNAME})
    return f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}"


def _ensure_board(api, hook, channel_id, game, board_id, env) -> str:
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
    api("PUT", f"/channels/{channel_id}/pins/{board_id}")
    return board_id


def setup(token: str, invite: str, games: list[str], env: dict[str, str]) -> dict[str, str]:
    """Create/repair channels, webhooks and pinned posts. Safe to re-run.
    Returns the settings keys to merge into games-env."""
    api = Discord(token)
    guild = env.get("DISCORD_GUILD_ID") or guild_from_invite(api, invite)
    channels = api("GET", f"/guilds/{guild}/channels")
    category = _ensure_channel(api, guild, channels, CATEGORY, CATEGORY_TYPE)
    updates = {"DISCORD_GUILD_ID": guild}
    for game in games:
        name, description, _ = notify.GAMES[game]
        text = _ensure_channel(api, guild, channels, game, TEXT, category["id"],
                               topic=f"{description} Status + connection details are pinned.")
        voice = _ensure_channel(api, guild, channels, name.split(":")[-1].strip(), VOICE, category["id"])
        hook = _ensure_webhook(api, text["id"])
        key = game.upper()
        updates |= {
            f"DISCORD_CHANNEL_{key}": text["id"],
            f"DISCORD_VOICE_{key}": voice["id"],
            f"DISCORD_WEBHOOK_{key}": hook,
            f"DISCORD_BOARD_{key}": _ensure_board(api, hook, text["id"], game,
                                                  env.get(f"DISCORD_BOARD_{key}", ""), env),
        }
    return updates
