"""What the portal displays and controls. No external dependencies —
importable and testable with nothing but the stdlib.

Keep GAMES in sync with infra/gcp-games/discord-notify.py's GAMES dict;
they describe the same servers to two audiences (a Discord channel vs.
this portal).
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field, replace
from typing import Callable


@dataclass(frozen=True)
class Setting:
    key: str                  # the games-env key the VM reads
    label: str
    secret: bool = False      # rendered as a password field, never echoed back
    help: str = ""


@dataclass(frozen=True)
class Theme:
    """Look of a game's own page (templates/game.html). Colors are CSS values;
    `font` is a Google Fonts family used for headings."""
    accent: str               # buttons, links, highlights
    accent2: str              # secondary glow / borders
    bg: str                   # page background
    card: str                 # panel background
    text: str                 # body text on panels
    font: str
    pattern: str              # CSS background-image layered over the hero
    tagline: str


@dataclass(frozen=True)
class Game:
    id: str
    name: str
    description: str
    ram_gb: int                          # sizing input, mirrors scripts/games.sh
    connect: Callable[[str, dict], str]  # (external_ip, settings) -> HTML-safe instructions
    settings: tuple[Setting, ...] = field(default=())
    join_password_key: str = ""          # shown to signed-in players on the dashboard
    ready_minutes: int = 5               # typical boot → joinable, for the "starting" hint
    max_players: int = 0                 # shown as "Players online N / max" on the game page
    invite_code: bool = False            # hands out a per-session invite code (diagnostics.join_codes)
    direct: Callable[[str, dict], str] | None = None   # (ip, settings) -> "ip:port" for a Direct-connect box
    theme: Theme | None = None
    icon: str = "🎮"                    # the game's card on the Games page (an emoji, like the launcher's tiles)
    facts: tuple[tuple[str, str], ...] = field(default=())   # (label, value) on the game page
    links: tuple[tuple[str, str], ...] = field(default=())   # (label, url) official site, wiki

    @property
    def banner_key(self) -> str:
        """games-env key for an optional banner image URL (set on the game's page)."""
        return f"{self.id.upper()}_BANNER_URL"


GAMES: dict[str, Game] = {
    "dragonwilds": Game(
        "dragonwilds", "RuneScape: Dragonwilds",
        "Survival crafting in Ashenfall. Crossplay: PC, Xbox, PS5, Switch 2.",
        ram_gb=8,
        connect=lambda ip, env: (
            "Worlds &rarr; <b>Public</b> tab &rarr; search "
            f"<b>{html.escape(env.get('DRAGONWILDS_SERVER_NAME') or 'Skippy')}</b> "
            "(the whole name, exact spelling and case)"
        ),
        settings=(
            Setting("DRAGONWILDS_OWNER_ID", "Owner Player ID",
                    help="In-game Settings menu, bottom, copy button. Not your Xbox/Steam ID."),
            Setting("DRAGONWILDS_ADMIN_PASSWORD", "Admin password", secret=True),
            Setting("DRAGONWILDS_SERVER_NAME", "Server name",
                    help="How the world shows in the Public list. Search only matches the whole name, "
                         "exact case, so keep it easy to type."),
            Setting("DRAGONWILDS_WORLD_NAME", "World name",
                    help="Only used when the world is first created; renaming it later has no effect."),
            Setting("DRAGONWILDS_WORLD_PASSWORD", "World password", secret=True,
                    help="Strongly recommended: the world is listed publicly, so without one "
                         "anyone can join. Signed-in portal users see it next to the invite code."),
        ),
        join_password_key="DRAGONWILDS_WORLD_PASSWORD",
        ready_minutes=6,
        invite_code=True,
        direct=lambda ip, env: f"{ip}:{env.get('DRAGONWILDS_PORT') or '7778'}",
        icon="🐉",
        theme=Theme(accent="#ff7a1a", accent2="#c9a24a", bg="#120806", card="#24110b", text="#f3dcc2",
                    font="Cinzel",
                    pattern="radial-gradient(ellipse at 20% 110%, rgba(255,90,20,.55), transparent 60%), "
                            "radial-gradient(ellipse at 85% -10%, rgba(201,162,74,.35), transparent 55%)",
                    tagline="Dragons are loose in Ashenfall. Bring friends."),
        max_players=6,
        facts=(("Crossplay", "PC · Xbox · PS5 · Switch 2"), ("Ports", "7778/udp + beacon 8888/udp")),
        links=(("Official site", "https://dragonwilds.runescape.com"),
               ("Wiki", "https://dragonwilds.runescape.wiki")),
    ),
    "valheim": Game(
        "valheim", "Valheim",
        "Viking survival co-op. Dedicated world, persists between sessions.",
        ram_gb=4,
        connect=lambda ip, env: f"Join Game &rarr; Add server &rarr; <code>{ip}:2456</code>",
        settings=(
            Setting("VALHEIM_SERVER_NAME", "Server name"),
            Setting("VALHEIM_WORLD_NAME", "World name", help="Changing this starts a new world."),
            Setting("VALHEIM_PASSWORD", "Join password", secret=True,
                    help="At least 5 characters; must not contain the server name."),
            Setting("VALHEIM_PUBLIC", "List in the public server browser", help="true or false"),
        ),
        join_password_key="VALHEIM_PASSWORD",
        ready_minutes=5,
        icon="🪓",
        theme=Theme(accent="#8fd3ff", accent2="#d8c9a3", bg="#0b1016", card="#141d27", text="#dfe8ef",
                    font="Cinzel",
                    pattern="linear-gradient(180deg, rgba(143,211,255,.18), transparent 70%), "
                            "repeating-linear-gradient(115deg, rgba(216,201,163,.06) 0 2px, transparent 2px 22px)",
                    tagline="Odin's tenth world awaits. Mind the trolls."),
        max_players=10,
        facts=(("Crossplay", "Steam · Xbox · Game Pass"),
               ("Joined by", "IP address, port 2456")),
        links=(("Official site", "https://www.valheimgame.com"), ("Wiki", "https://valheim.fandom.com")),
    ),
    "factorio": Game(
        "factorio", "Factorio",
        "Factory-building co-op. The factory must grow.",
        ram_gb=2,
        connect=lambda ip, env: f"Multiplayer &rarr; Connect to address &rarr; <code>{ip}</code>",
        settings=(Setting("FACTORIO_SAVE_NAME", "Save name"),),
        icon="⚙️",
        theme=Theme(accent="#ff9a1f", accent2="#7fb03a", bg="#141414", card="#232323", text="#e6e0d6",
                    font="Titillium Web",
                    pattern="repeating-linear-gradient(45deg, rgba(255,154,31,.10) 0 14px, rgba(0,0,0,0) 14px 28px)",
                    tagline="The factory must grow."),
        facts=(("Players", "as many as the factory can take"), ("Joined by", "IP address"),
               ("Port", "34197/udp")),
        links=(("Official site", "https://factorio.com"), ("Wiki", "https://wiki.factorio.com")),
    ),
    "satisfactory": Game(
        "satisfactory", "Satisfactory",
        "3D factory-building co-op on an alien planet.",
        ram_gb=12,
        connect=lambda ip, env: f"Server Manager &rarr; Add Server &rarr; <code>{ip}</code> port <code>7777</code>",
        settings=(Setting("SATISFACTORY_MAX_PLAYERS", "Max players"),),
        icon="🏭",
        theme=Theme(accent="#fa9549", accent2="#5fb6d9", bg="#0d1216", card="#18222a", text="#e2ecf2",
                    font="Rajdhani",
                    pattern="radial-gradient(circle at 80% 20%, rgba(95,182,217,.35), transparent 50%), "
                            "linear-gradient(135deg, rgba(250,149,73,.25), transparent 60%)",
                    tagline="FICSIT reminds you: efficiency is mandatory."),
        facts=(("Players", "4 recommended, more configurable"),
               ("Joined by", "Server Manager, IP + port 7777")),
        links=(("Official site", "https://www.satisfactorygame.com"),
               ("Wiki", "https://satisfactory.wiki.gg")),
    ),
    "everquest": Game(
        "everquest", "EverQuest",
        "The Captain's Golden Era EverQuest (EQEmu): soloable with bots and mercs, XP x3, no death penalty.",
        ram_gb=6,
        connect=lambda ip, env: (
            "RoF2 client: in <code>eqhost.txt</code> change the address in both the "
            f"<code>[Registration Servers]</code> and <code>[Login Servers]</code> blocks to "
            f"<code>\"{ip}:5999\"</code>, start <code>eqgame.exe patchme</code>, and log in: "
            "a new name + password creates your account"
        ),
        ready_minutes=8,
        direct=lambda ip, env: f"{ip}:5999",
        icon="🧙",
        theme=Theme(accent="#d4af37", accent2="#6a8cc7", bg="#0b0a12", card="#171425", text="#e8e2d0",
                    font="Cinzel",
                    pattern="radial-gradient(ellipse at 15% 0%, rgba(106,140,199,.35), transparent 55%), "
                            "radial-gradient(ellipse at 90% 100%, rgba(212,175,55,.30), transparent 55%)",
                    tagline="Norrath, the way you remember it. Minus the corpse runs."),
        facts=(("Client", "RoF2 (Rain of Fear 2)"), ("Login server", "this server, port 5999"),
               ("Rules", "XP x3 · AA x2 · level 65 · bots + mercs · no death penalty"),
               ("First boot", "~15-20 min to install; later boots a few minutes")),
        links=(("EQEmu", "https://www.eqemulator.org"), ("Bots guide", "https://docs.eqemu.io")),
    ),
}

# Every game can have a banner image on its page; the URL is just another setting.
GAMES = {gid: replace(g, settings=g.settings + (Setting(
    g.banner_key, "Banner image URL",
    help="Optional: an https:// image shown at the top of this game's page."),))
    for gid, g in GAMES.items()}


# Sub-portals a friend can be granted, in front-page order: every game, the
# Skippy chat, and Experiments (public anyway; the grant puts it on their
# front page). The personal dashboard is admin-only and never grantable.
PORTALS: dict[str, str] = {**{gid: g.name for gid, g in GAMES.items()},
                           "skippy": "Skippy (chat)", "bilby": "Bilby (music)",
                           "experiments": "Experiments"}


def missing_settings(game_id: str, env: dict[str, str]) -> list[str]:
    """What stops a game from starting, in words an admin can act on.
    Mirrors check_env() in scripts/games.sh."""
    problems = []
    if game_id == "valheim":
        pw = env.get("VALHEIM_PASSWORD", "")
        if len(pw) < 5 or pw == "change-me":
            problems.append("Valheim needs a join password of at least 5 characters.")
        elif env.get("VALHEIM_SERVER_NAME") and env["VALHEIM_SERVER_NAME"] in pw:
            problems.append("Valheim's password must not contain the server name.")
    if game_id == "dragonwilds":
        if not env.get("DRAGONWILDS_OWNER_ID"):
            problems.append("Dragonwilds needs the owner's Player ID.")
        if env.get("DRAGONWILDS_ADMIN_PASSWORD", "change-me") in ("", "change-me"):
            problems.append("Dragonwilds needs an admin password.")
    return problems


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    description: str
    url: str


# Static for now: the portal runs on Cloud Run, with no route into the home
# Tailscale mesh most of these live on.
SERVICES: list[Service] = [
    Service("jellyfin", "Jellyfin", "Movies and TV", "http://skippy-pc:8096"),
    Service("skippytel", "SkippyTel", "Phone recovery + AI inference", "http://skippy-pc:3003/phone"),
]


# ── Who may choose what (guardrails for non-admin players) ──────────────────

PLAYER_IDLE_MIN, PLAYER_IDLE_MAX, DEFAULT_IDLE = 15, 120, 30


def clamp_idle(value: str | None, is_admin: bool) -> int:
    """Auto-sleep minutes. Admins may pick anything, including 0 (never
    sleep); players get 15–120 so nobody leaves the VM billing all week."""
    minutes = int(value) if value and value.strip().isdigit() else DEFAULT_IDLE
    if is_admin:
        return minutes
    return min(max(minutes, PLAYER_IDLE_MIN), PLAYER_IDLE_MAX)


# ── VM sizing (mirrors scripts/games.sh's ram_gb/machine_for) ────────────────

def machine_for(game_ids: list[str]) -> str:
    """Smallest E2 machine type that fits the requested games + ~2GB for the OS."""
    total = 2 + sum(GAMES[g].ram_gb for g in game_ids if g in GAMES)
    if total <= 6:
        return "e2-standard-2"    # 8 GB: one light server
    if total <= 14:
        return "e2-standard-4"    # 16 GB
    if total <= 30:
        return "e2-highmem-4"     # 32 GB
    return "e2-highmem-8"         # 64 GB
