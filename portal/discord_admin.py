"""Discord setup and links, shared by the portal's admin page and
scripts/discord_setup.py. Stdlib only.

setup() makes sure the Discord server has, for each game:
  • a text channel and a voice channel with the same name — the game id
    (#dragonwilds + 🔊dragonwilds …), or the name in NAMES when the Captain
    made the channels himself (EverQuest's are #foreverquest) — under "Game
    Servers", so scripts can address both by one name. Channels that already
    exist under that name are adopted, never duplicated.
  • a permanent invite that lands people straight in the text channel
  • a webhook in the text channel (the VM posts status through it; no bot on the VM)
  • a pinned status post (description + how to connect), edited in place by the VM
and returns the keys to merge into the VM's settings (games-env):
  DISCORD_GUILD_ID, DISCORD_{CHANNEL,VOICE,INVITE,WEBHOOK,BOARD}_<GAME>

recent_messages() gives the portal a read-only copy of a game's text channel
(a Discord account isn't a Google account, so the portal shows the channel
and links people into Discord to talk, rather than posting for them).

Needs a bot token (only here, never on the VM) invited with Manage Channels,
Manage Webhooks, Manage Messages, View Channels, Send Messages, Read Message
History, Create Invite, and the bot's "Message Content Intent" switched on
(Developer Portal → Bot) so it can read what people wrote.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = os.environ.get("DISCORD_API", "https://discord.com/api/v10")
CATEGORY = "Game Servers"
# Games whose Discord channels aren't named after the game id: the Captain made these by hand.
NAMES = {"everquest": "foreverquest"}


def channel_name(game: str) -> str:
    return NAMES.get(game, game)
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
    code = env.get(f"DISCORD_INVITE_{game.upper()}")
    return {"text": url("DISCORD_CHANNEL"), "voice": url("DISCORD_VOICE"),
            "invite": f"https://discord.gg/{code}" if code else None}


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
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as err:
            raise DiscordError.from_http(err, method, path) from err


# Discord's JSON error codes worth translating for whoever clicked the button.
_HINTS = {
    50001: "the bot isn't in this Discord server (invite it) or can't see that channel",
    50013: "the bot is missing a permission (give it Administrator, or Manage Channels/Webhooks/Messages)",
    10004: "the Discord server wasn't found — check DISCORD_INVITE",
    40001: "the bot token was rejected",
}


class DiscordError(urllib.error.HTTPError):
    """An HTTPError (callers keep checking .code) whose message says what
    Discord actually objected to."""

    @classmethod
    def from_http(cls, err, method, path):
        try:
            info = json.loads(err.read() or b"{}")
        except ValueError:
            info = {}
        detail = info.get("message") or err.reason
        hint = _HINTS.get(info.get("code"))
        where = "webhook" if path.startswith("http") else path
        text = f"{err.code} {detail} ({method} {where})" + (f": {hint}" if hint else "")
        return cls(err.url, err.code, text, err.headers, None)

    def __str__(self):
        return self.msg


def guild_from_invite(api: Discord, invite: str) -> str:
    code = invite.rstrip("/").rsplit("/", 1)[-1]
    return api("GET", f"/invites/{code}", auth=False)["guild"]["id"]


def _ensure_channel(api, guild, channels, name, kind, parent=None, topic=None, known_id=None) -> dict:
    """The channel we made before (by stored id, renamed if its name drifted),
    else one with this name, else a new one."""
    for ch in channels:
        if known_id and ch["id"] == known_id and ch["type"] == kind:
            if ch["name"] != name:
                ch.update(api("PATCH", f"/channels/{ch['id']}", {"name": name}))
            return ch
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


def _ensure_invite(api, channel_id, code) -> str:
    """A permanent invite into this channel (kept if it still exists)."""
    if code:
        try:
            api("GET", f"/invites/{code}", auth=False)
            return code
        except urllib.error.HTTPError as err:
            if err.code != 404:
                raise
    return api("POST", f"/channels/{channel_id}/invites",
               {"max_age": 0, "max_uses": 0, "unique": False})["code"]


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


def setup(token: str, invite: str, games: list[str], env: dict[str, str],
          portal_url: str | None = None) -> dict[str, str]:
    """Create/repair channels, webhooks and pinned posts. Safe to re-run.
    Returns the settings keys to merge into games-env. `portal_url` (the address the portal is served on)
    is what the pinned posts link to; it's kept in games-env as PORTAL_URL so the VM's own edits of the
    posts link to the same place."""
    api = Discord(token)
    guild = env.get("DISCORD_GUILD_ID") or guild_from_invite(api, invite)
    channels = api("GET", f"/guilds/{guild}/channels")
    category = _ensure_channel(api, guild, channels, CATEGORY, CATEGORY_TYPE)
    updates = {"DISCORD_GUILD_ID": guild}
    shown = (portal_url or "").strip().rstrip("/")
    if shown and notify.portal_url({"PORTAL_URL": shown}) == shown:        # a plain https origin, else ignored
        updates["PORTAL_URL"] = shown
        env = {**env, "PORTAL_URL": shown}
    for game in games:
        _, description, _ = notify.GAMES[game]
        key = game.upper()
        # Text and voice share one name (the game id unless NAMES says otherwise), e.g. #dragonwilds + 🔊dragonwilds.
        name = channel_name(game)
        text = _ensure_channel(api, guild, channels, name, TEXT, category["id"],
                               topic=f"{description} Status + connection details are pinned.",
                               known_id=env.get(f"DISCORD_CHANNEL_{key}"))
        voice = _ensure_channel(api, guild, channels, name, VOICE, category["id"],
                                known_id=env.get(f"DISCORD_VOICE_{key}"))
        hook = _ensure_webhook(api, text["id"])
        updates |= {
            f"DISCORD_CHANNEL_{key}": text["id"],
            f"DISCORD_VOICE_{key}": voice["id"],
            f"DISCORD_INVITE_{key}": _ensure_invite(api, text["id"], env.get(f"DISCORD_INVITE_{key}", "")),
            f"DISCORD_WEBHOOK_{key}": hook,
            f"DISCORD_BOARD_{key}": _ensure_board(api, hook, text["id"], game,
                                                  env.get(f"DISCORD_BOARD_{key}", ""), env),
        }
    return updates


# ── read-only channel copy for the portal ─────────────────────────────────────

_MENTION = re.compile(r"<@!?(\d+)>")
_CACHE: dict[str, tuple[float, list[dict]]] = {}
CACHE_SECONDS = 15          # every open game page polls; Discord rate-limits per channel


def _plain_message(m: dict) -> dict | None:
    """One Discord message → what the portal shows. Mentions become @names;
    embeds (the VM's status posts) show their title/description."""
    names = {u["id"]: u.get("global_name") or u["username"] for u in m.get("mentions", [])}
    text = _MENTION.sub(lambda g: "@" + names.get(g.group(1), "someone"), m.get("content") or "")
    extra = [e.get("title") or e.get("description") or "" for e in m.get("embeds", [])]
    extra += [a.get("filename", "attachment") for a in m.get("attachments", [])]
    extra = [x for x in extra if x]
    if not text and not extra:
        return None
    author = m.get("author", {})
    return {
        "id": m["id"],
        "author": author.get("global_name") or author.get("username") or "?",
        "bot": bool(author.get("bot")),
        "text": text,
        "extra": extra,
        "at": m.get("timestamp", ""),
    }


def recent_messages(token: str, channel_id: str, limit: int = 30) -> list[dict]:
    """The channel's latest messages, oldest first, cached briefly."""
    hit = _CACHE.get(channel_id)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    raw = Discord(token)("GET", f"/channels/{channel_id}/messages?limit={limit}")
    msgs = [p for p in (_plain_message(m) for m in reversed(raw)) if p]
    _CACHE[channel_id] = (time.monotonic(), msgs)
    return msgs
