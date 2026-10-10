"""What the portal displays and controls. No external dependencies —
importable and testable with nothing but the stdlib.

Keep GAMES in sync with infra/gcp-games/discord-notify.py's GAMES dict;
they describe the same servers to two audiences (a Discord channel vs.
this portal).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, replace
from typing import Callable


@dataclass(frozen=True)
class Setting:
    key: str                  # the games-env key the VM reads
    label: str
    secret: bool = False      # rendered as a password field, never echoed back
    help: str = ""
    check: Callable[[str], tuple[str, str | None]] | None = None   # (typed value) -> (value to save, what's wrong or None)
    external: bool = False    # issued by someone else (a token): "Set up automatically" must never make one up


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
    # Official art shipped with the portal (portal/static/games, provenance in docs/portal/game-art.md): `bg_image` is
    # the page's dimmed full-page backdrop, `hero_image` the banner behind the title (an admin's <GAME>_BANNER_URL
    # still wins), `hero_focus` is the banner's CSS object-position so the interesting part survives the crop.
    bg_image: str = ""
    hero_image: str = ""
    hero_focus: str = "center"


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
    invite_when: Callable[[dict], bool] | None = None   # (settings) -> is the invite code in play? (None: always, when invite_code)
    invite_hint: str = ""                # HTML-safe: where to type the invite code in the game
    direct: Callable[[str, dict], str] | None = None   # (ip, settings) -> "ip:port" for a Direct-connect box
    direct_hint: str = ""                # HTML-safe: where to type that address in the game
    direct_port: str = ""                # only when the game's dialog has a SEPARATE Port box: the port, as its own chip beside the address
    via_list: bool = False               # the game's server list finds it by name: `connect` says how (the fallback)
    snippet: Callable[[str, dict], tuple[str, str]] | None = None   # (ip, settings) -> (label, text): a block to copy as-is
    theme: Theme | None = None
    icon: str = "🎮"                    # (the old emoji card; the bridge uses `glyph`)
    glyph: str = "console"              # the game's control on the Games page (templates/_glyphs.html)
    facts: tuple[tuple[str, str], ...] = field(default=())   # (label, value) on the game page
    links: tuple[tuple[str, str], ...] = field(default=())   # (label, url) official site, wiki

    @property
    def banner_key(self) -> str:
        """games-env key for an optional banner image URL (set on the game's page)."""
        return f"{self.id.upper()}_BANNER_URL"


# What the VM uses when a Valheim setting is blank; autoconfig.DEFAULTS makes them visible, and tests keep them equal to
# games/docker-compose.yml (crossplay) and infra/gcp-games/valheim_seed.py (the seed).
VALHEIM_DEFAULTS = {"VALHEIM_WORLD_NAME": "Skippy", "VALHEIM_WORLD_SEED": "skippyhasyourmoney", "VALHEIM_CROSSPLAY": "false"}


def valheim_crossplay(env: dict) -> bool:
    """Crossplay is a trade: a 6-digit join code that works from Steam, Xbox and Game Pass, but the server then stops
    listening on 2456, so `ip:2456` no longer works, and the idle watchdog can't see the players (it counts packets on
    2456). Off by default until the watchdog counts them."""
    return (env.get("VALHEIM_CROSSPLAY") or VALHEIM_DEFAULTS["VALHEIM_CROSSPLAY"]).strip().lower() in ("true", "1", "yes", "on")


def valheim_password_problems(password: str, env: dict) -> list[str]:
    """The game refuses a password that appears inside the world's name or seed (the seed check is skipped for `keep`:
    then the world's own seed, which we don't know here, is what counts)."""
    checks = [("world name", env.get("VALHEIM_WORLD_NAME") or VALHEIM_DEFAULTS["VALHEIM_WORLD_NAME"])]
    seed = env.get("VALHEIM_WORLD_SEED") or VALHEIM_DEFAULTS["VALHEIM_WORLD_SEED"]
    if seed.strip().lower() != "keep":
        checks.append(("world seed", seed))
    return [f"Valheim's password must not appear inside the {what}." for what, text in checks
            if password and password.lower() in text.lower()]


def _gmod_password(value: str) -> tuple[str, str | None]:
    # it goes onto srcds's command line through a shell (games/docker-compose.yml builds ARGS from it): nothing the shell reads
    if re.fullmatch(r"[A-Za-z0-9_.-]{0,64}", value.strip()):
        return value.strip(), None
    return value, "letters, digits, _ . - only (no spaces or symbols), up to 64"


def _gmod_name(value: str) -> tuple[str, str | None]:
    v = value.strip()
    return (v, None) if re.fullmatch(r"[A-Za-z0-9_]{0,64}", v) else (value, "letters, digits and underscores only")


def _gmod_slots(value: str) -> tuple[str, str | None]:
    v = value.strip()
    return (v, None) if v == "" or (v.isascii() and v.isdigit() and 1 <= int(v) <= 128) else (value, "a number from 1 to 128")


def _gmod_collection(value: str) -> tuple[str, str | None]:
    v = value.strip()
    return (v, None) if re.fullmatch(r"[0-9]{0,20}", v) else (value, "the digits from the collection's address (no link, no spaces)")


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
        invite_hint="In game: Worlds &rarr; <b>Invite Code</b> (the I key). A new code every boot.",
        direct=lambda ip, env: f"{ip}:{env.get('DRAGONWILDS_PORT') or '7778'}",
        direct_hint="In game: Worlds &rarr; <b>Direct</b> tab. Same address every session.",
        via_list=True,
        icon="🐉", glyph="crest",
        theme=Theme(accent="#ff7a1a", accent2="#c9a24a", bg="#120806", card="#24110b", text="#f3dcc2",
                    font="Cinzel",
                    pattern="radial-gradient(ellipse at 20% 110%, rgba(255,90,20,.55), transparent 60%), "
                            "radial-gradient(ellipse at 85% -10%, rgba(201,162,74,.35), transparent 55%)",
                    tagline="Dragons are loose in Ashenfall. Bring friends.",
                    bg_image="games/dragonwilds-bg.jpg", hero_image="games/dragonwilds-hero.jpg", hero_focus="62% 50%"),
        max_players=6,
        facts=(("Crossplay", "PC · Xbox · PS5 · Switch 2"), ("Ports", "7778/udp + beacon 8888/udp")),
        links=(("Official site", "https://dragonwilds.runescape.com"),
               ("Wiki", "https://dragonwilds.runescape.wiki")),
    ),
    "valheim": Game(
        "valheim", "Valheim",
        "Viking survival co-op. Dedicated world, persists between sessions.",
        ram_gb=4,
        connect=lambda ip, env: ("Join Game &rarr; Add server &rarr; the join code (a new one every boot; the address doesn't work "
                                 "with crossplay on)" if valheim_crossplay(env)
                                 else f"Join Game &rarr; Add server &rarr; <code>{ip}:2456</code>"),
        settings=(
            Setting("VALHEIM_SERVER_NAME", "Server name"),
            Setting("VALHEIM_WORLD_NAME", "World name", help="Changing this starts a new world."),
            Setting("VALHEIM_CROSSPLAY", "Crossplay join code",
                    help="true: a 6-digit join code that works from Steam, Xbox and Game Pass, new every boot, INSTEAD of "
                         "the IP address (the server stops listening on 2456). false (the default): the IP address, no code. "
                         "Keep it false until the idle watchdog counts crossplay players."),
            Setting("VALHEIM_WORLD_SEED", "World seed",
                    help="The world is built from this text. At the next start, a world with a different seed is "
                         "set aside (kept on the server's disk, never deleted) and a new one is generated. "
                         "Blank means skippyhasyourmoney; type keep to leave the world alone."),
            Setting("VALHEIM_PASSWORD", "Join password", secret=True,
                    help="At least 5 characters; must not contain the server name."),
            Setting("VALHEIM_PUBLIC", "List in the public server browser", help="true or false"),
        ),
        join_password_key="VALHEIM_PASSWORD",
        ready_minutes=5,
        invite_code=True,                                # the crossplay join code, only while VALHEIM_CROSSPLAY is on
        invite_when=valheim_crossplay,
        invite_hint="In game: Join Game &rarr; <b>Add server</b>, then paste the code. A new code every boot. "
                    "(With crossplay on the server has no IP address to join.)",
        direct=lambda ip, env: "" if valheim_crossplay(env) else f"{ip}:2456",     # "" = nothing to show
        direct_hint="In game: Join Game &rarr; <b>Add server</b>. Same address every session.",
        icon="🪓", glyph="rune",
        theme=Theme(accent="#8fd3ff", accent2="#d8c9a3", bg="#0b1016", card="#141d27", text="#dfe8ef",
                    font="Cinzel",
                    pattern="linear-gradient(180deg, rgba(143,211,255,.18), transparent 70%), "
                            "repeating-linear-gradient(115deg, rgba(216,201,163,.06) 0 2px, transparent 2px 22px)",
                    tagline="Odin's tenth world awaits. Mind the trolls.",
                    bg_image="games/valheim-bg.jpg", hero_image="games/valheim-hero.jpg", hero_focus="50% 64%"),
        max_players=10,
        facts=(("Crossplay", "Steam · Xbox · Game Pass"),
               ("Joined by", "IP address port 2456, or a join code when crossplay is on")),
        links=(("Official site", "https://www.valheimgame.com"), ("Wiki", "https://valheim.fandom.com")),
    ),
    "factorio": Game(
        "factorio", "Factorio",
        "Factory-building co-op. The factory must grow.",
        ram_gb=2,
        connect=lambda ip, env: f"Multiplayer &rarr; Connect to address &rarr; <code>{ip}:34197</code>",
        settings=(Setting("FACTORIO_SAVE_NAME", "Save name"),),
        direct=lambda ip, env: f"{ip}:34197",            # no invite codes: the address is the whole way in, port included (one paste)
        direct_hint="In game: Multiplayer &rarr; <b>Connect to address</b>. Same address every session.",
        icon="⚙️", glyph="cog",
        theme=Theme(accent="#ff9a1f", accent2="#7fb03a", bg="#141414", card="#232323", text="#e6e0d6",
                    font="Titillium Web",
                    pattern="repeating-linear-gradient(45deg, rgba(255,154,31,.10) 0 14px, rgba(0,0,0,0) 14px 28px)",
                    tagline="The factory must grow.",
                    bg_image="games/factorio-bg.jpg", hero_image="games/factorio-hero.jpg", hero_focus="50% 40%"),
        facts=(("Players", "as many as the factory can take"), ("Joined by", "IP address"),
               ("Port", "34197/udp")),
        links=(("Official site", "https://factorio.com"), ("Wiki", "https://wiki.factorio.com")),
    ),
    "satisfactory": Game(
        "satisfactory", "Satisfactory",
        "3D factory-building co-op on an alien planet.",
        ram_gb=12,
        connect=lambda ip, env: (f"Main menu &rarr; Server Manager &rarr; Add Server &rarr; address <code>{ip}</code>, "
                                 "port <code>7777</code> "
                                 "(not Multiplayer &rarr; Join Game: that fails with Encryption Token Missing)"),
        settings=(Setting("SATISFACTORY_MAX_PLAYERS", "Max players"),),
        direct=lambda ip, env: ip,                       # no invite codes (its Session ID is for player-hosted games) and no server list;
        direct_port="7777",                              # the Server Manager's Add Server dialog has separate Address and Port boxes
        direct_hint="In game: the main menu's <b>Server Manager</b> &rarr; <b>Add Server</b>: the address in the "
                    "first box, the port in the second. <b>Not</b> Multiplayer &rarr; Join Game: that screen (the one asking for an IP or "
                    "Session ID) is for player-hosted games and fails on a dedicated server with &ldquo;Encryption Token "
                    "Missing&rdquo;. The first player to connect claims the server and sets its admin password: do that "
                    "right away. Same address every session.",
        icon="🏭", glyph="factory",
        theme=Theme(accent="#fa9549", accent2="#5fb6d9", bg="#0d1216", card="#18222a", text="#e2ecf2",
                    font="Rajdhani",
                    pattern="radial-gradient(circle at 80% 20%, rgba(95,182,217,.35), transparent 50%), "
                            "linear-gradient(135deg, rgba(250,149,73,.25), transparent 60%)",
                    tagline="FICSIT reminds you: efficiency is mandatory.",
                    bg_image="games/satisfactory-bg.jpg", hero_image="games/satisfactory-hero.jpg", hero_focus="50% 62%"),
        facts=(("Players", "4 recommended, more configurable"),
               ("Joined by", "Server Manager: address, and port 7777")),
        links=(("Official site", "https://www.satisfactorygame.com"),
               ("Wiki", "https://satisfactory.wiki.gg")),
    ),
    "garrysmod": Game(
        "garrysmod", "Garry's Mod",
        "Sandbox physics and bad ideas. A dedicated Source server: Sandbox, or any gamemode, and a Workshop collection.",
        ram_gb=4,
        connect=lambda ip, env: f"Press <b>~</b> for the console &rarr; <code>connect {ip}:27015</code>",
        settings=(
            Setting("GARRYSMOD_SERVER_NAME", "Server name", check=_gmod_password,
                    help="One word: letters, digits, _ . - (a space cuts the name short, the image puts it on the command line unquoted). Shown in the in-game server list."),
            Setting("GARRYSMOD_PASSWORD", "Join password", secret=True, check=_gmod_password,
                    help="No spaces. Blank: anyone who finds the server can join."),
            Setting("GARRYSMOD_GAMEMODE", "Gamemode", check=_gmod_name,
                    help="sandbox (blank means sandbox), or the folder name of any installed gamemode (darkrp, terrortown...). "
                         "A gamemode that isn't installed leaves the server unable to start."),
            Setting("GARRYSMOD_MAP", "Starting map", check=_gmod_name,
                    help="gm_construct (blank means gm_construct), gm_flatgrass... A map that isn't installed leaves the server unable to start."),
            Setting("GARRYSMOD_MAX_PLAYERS", "Max players", check=_gmod_slots, help="1 to 128; blank means 16."),
            Setting("GARRYSMOD_WORKSHOP_COLLECTION", "Workshop collection", check=_gmod_collection,
                    help="The number from a Steam Workshop COLLECTION's address (not a single addon). The server downloads the "
                         "whole collection at every start, and players download the same addons when they join."),
        ),
        join_password_key="GARRYSMOD_PASSWORD",
        ready_minutes=6,
        max_players=16,
        direct=lambda ip, env: f"{ip}:27015",
        direct_hint="In game: the console (<b>~</b>) &rarr; <code>connect</code> and this address; or Find Multiplayer Game "
                    "&rarr; Favorites &rarr; Add Server. Same address every session. (Turn the console on in Options &rarr; "
                    "Keyboard &rarr; Advanced if <b>~</b> does nothing.)",
        icon="🧱", glyph="cube",
        theme=Theme(accent="#4aa8ff", accent2="#ffb347", bg="#0a1118", card="#121d29", text="#dde8f2",
                    font="Rajdhani",
                    pattern="repeating-linear-gradient(0deg, rgba(74,168,255,.08) 0 1px, transparent 1px 28px), "
                            "repeating-linear-gradient(90deg, rgba(74,168,255,.08) 0 1px, transparent 1px 28px)",
                    tagline="Sandbox physics. Bad ideas, together.",
                    bg_image="games/garrysmod-bg.jpg", hero_image="games/garrysmod-hero.jpg", hero_focus="50% 50%"),
        facts=(("Players", "up to 16 (configurable)"), ("Joined by", "the console: connect ip:27015"),
               ("Port", "27015/udp")),
        links=(("Official site", "https://gmod.facepunch.com"), ("Wiki", "https://wiki.facepunch.com/gmod")),
    ),
    "everquest": Game(
        "everquest", "EverQuest",
        "The Captain's Golden Era EverQuest (EQEmu): soloable with bots and mercs, XP x3, no death penalty.",
        ram_gb=6,
        connect=lambda ip, env: (
            "RoF2 client: in <code>eqhost.txt</code> put <code>[LoginServer]</code> with "
            f"<code>Host={ip}:5999</code> (RoF2's format; the older <code>[Registration Servers]</code> braces "
            "format is Titanium's and RoF2 throws it away), start <code>eqgame.exe patchme</code>, and log in: "
            "a new name + password creates your account"
        ),
        ready_minutes=8,
        direct=lambda ip, env: f"{ip}:5999",
        direct_hint="This is the login server. It goes in <code>eqhost.txt</code>, in the block below. If the game "
                    "puts its own <code>Host=xp-smitchell:7308</code> back, the file wasn't read (wrong format) or was "
                    "restored: close the game, paste the block again and mark the file read-only.",
        snippet=lambda ip, env: ("eqhost.txt (RoF2): replace the file's contents with this, then start "
                                 "eqgame.exe patchme; a new name + password creates your account",
                                 f"[LoginServer]\nHost={ip}:5999\nHost1="),
        icon="🧙", glyph="wand",
        theme=Theme(accent="#d4af37", accent2="#6a8cc7", bg="#0b0a12", card="#171425", text="#e8e2d0",
                    font="Cinzel",
                    pattern="radial-gradient(ellipse at 15% 0%, rgba(106,140,199,.35), transparent 55%), "
                            "radial-gradient(ellipse at 90% 100%, rgba(212,175,55,.30), transparent 55%)",
                    tagline="Norrath, the way you remember it. Minus the corpse runs.",
                    bg_image="games/everquest-bg.jpg", hero_image="games/everquest-hero.jpg", hero_focus="50% 38%"),
        facts=(("Client", "RoF2 (Rain of Fear 2)"), ("Login server", "this server, port 5999"),
               ("Rules", "XP x3 · AA x2 · level 65 · bots + mercs · no death penalty"),
               ("First boot", "~15-20 min to install; later boots a few minutes")),
        links=(("EQEmu", "https://www.eqemulator.org"), ("Bots guide", "https://docs.eqemu.io")),
    ),
}

# ── keeping servers and mods current (docs/games/MODS_AND_UPDATES.md; the card is portal/admin_panel.py) ────────
# Admin-only settings for the update controls the real images support. Each one is wired in games/docker-compose.yml with
# the default below, which is what a blank value means and is exactly what the servers did before these existed:
# nothing here changes behaviour until the Captain turns it on. tests/test_admin_panel.py keeps the two in step.

def _bool(value: str) -> tuple[str, str | None]:
    v = value.strip().lower()
    return (v, None) if v in ("true", "false") else (value, "must be true or false")


def _factorio_version(value: str) -> tuple[str, str | None]:
    # ASCII digits only (a Unicode `\d` also takes Arabic-Indic and full-width digits, which are no image tag) and a
    # sane length. Only the shape is checked: a well-formed version that has no Docker Hub tag still passes, and
    # infra/gcp-games/startup.sh is what keeps such a tag from stopping the other games at boot.
    v = value.strip().lower()
    if v in ("stable", "latest") or re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,4}", v):
        return v, None
    return value, "must be stable, latest, or an exact version such as 2.0.77"


def _mod_names(value: str) -> tuple[str, str | None]:
    names = [n.strip() for n in value.split(",") if n.strip()]
    if all(re.fullmatch(r"[A-Za-z0-9 _.-]{1,80}", n) for n in names):
        return ",".join(names), None
    return value, "must be mod names separated by commas"


UPDATE_DEFAULTS = {
    "VALHEIM_BEPINEX": "false",
    "FACTORIO_VERSION": "stable", "FACTORIO_UPDATE_MODS": "false", "FACTORIO_UPDATE_IGNORE": "",
    "FACTORIO_USERNAME": "", "FACTORIO_TOKEN": "",
    "SATISFACTORY_SKIP_UPDATE": "false",
    "DRAGONWILDS_UPDATE_ON_START": "true",
}

UPDATE_SETTINGS: dict[str, tuple[Setting, ...]] = {
    "valheim": (
        Setting("VALHEIM_BEPINEX", "Mod support (BepInEx)", check=_bool,
                help="true or false; blank means false. true: at every start the image installs BepInEx (from Thunderstore) "
                     "and loads the plugins in data/valheim/config/bepinex/plugins on the server disk. The server then "
                     "counts as modded, and a mod that changes gameplay must also be installed on every player's PC "
                     "(Xbox players can't install mods). The server itself updates from Steam at every start either way."),
    ),
    "factorio": (
        Setting("FACTORIO_VERSION", "Server version", check=_factorio_version,
                help="stable (blank means stable): the newest stable release, pulled at every boot. latest: the newest "
                     "experimental one. Or an exact version such as 2.0.77 to hold the server there: it then never "
                     "updates until you change this."),
        Setting("FACTORIO_UPDATE_MODS", "Update mods on start", check=_bool,
                help="true or false; blank means false. true: at every start, each enabled mod listed in "
                     "data/factorio/mods/mod-list.json is updated from mods.factorio.com (a listed mod that isn't "
                     "installed yet is downloaded). Needs the Factorio username and token below."),
        Setting("FACTORIO_USERNAME", "Factorio username",
                help="Your factorio.com username, for mod downloads. Blank: mod downloads fail with 403."),
        Setting("FACTORIO_TOKEN", "Factorio token", secret=True, external=True,
                help="Your factorio.com token (factorio.com → profile), for mod downloads. Never shown again once saved."),
        Setting("FACTORIO_UPDATE_IGNORE", "Never update these mods", check=_mod_names,
                help="Mod names separated by commas, left at their installed version when mods update on start."),
    ),
    "satisfactory": (
        Setting("SATISFACTORY_SKIP_UPDATE", "Skip the game update on start", check=_bool,
                help="true or false; blank means false. false: the server files are updated from Steam at every start. "
                     "true: the installed build is kept, and players on a newer game version can't join."),
    ),
    "dragonwilds": (
        Setting("DRAGONWILDS_UPDATE_ON_START", "Update the game files on start", check=_bool,
                help="true or false; blank means true. The server version has to match the game client, so leave this on."),
    ),
}

GAMES = {gid: replace(g, settings=g.settings + UPDATE_SETTINGS.get(gid, ())) for gid, g in GAMES.items()}

# Every game can have a banner image on its page; the URL is just another setting.
GAMES = {gid: replace(g, settings=g.settings + (Setting(
    g.banner_key, "Banner image URL",
    help="Optional: an https:// image shown at the top of this game's page."),))
    for gid, g in GAMES.items()}


# Sub-portals a friend can be granted, in front-page order: every game, the
# Skippy chat, and Experiments (public anyway; the grant puts it on their
# front page). The personal dashboard is admin-only and never grantable.
PORTALS: dict[str, str] = {**{gid: g.name for gid, g in GAMES.items()},
                           "skippy": "Skippy (chat)", "audio": "Bilby's Audio Service",
                           "video": "Nagatha's Video Service", "play": "Browser Games",
                           "experiments": "Experiments"}
# One grant per launcher tile a friend may be given (templates/home.html); Dashboard and Admin are the Captain's alone.
# "bilby" was the old /bilby page's grant: friends.portals_of() reads it as "audio".


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
        else:
            problems.extend(valheim_password_problems(pw, env))
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
    # (SkippyTel used to be listed here, linking to its /phone recovery page. Skippy core serves
    # no HTML any more, so there is nothing to open: its data shows in the Core widget instead.)
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
