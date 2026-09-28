"""What the portal displays and controls. No external dependencies —
importable and testable with nothing but the stdlib.

Keep GAMES in sync with infra/gcp-games/discord-notify.py's GAMES dict;
they describe the same four servers to two different audiences (a
Discord channel vs. this portal) and are kept separate on purpose so each
deployable (portal, GCE VM) has no build-time dependency on the other.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Game:
    id: str
    name: str
    description: str
    ram_gb: int                          # sizing input, mirrors scripts/games.sh
    connect: Callable[[str, dict], str]  # (external_ip, instance_metadata) -> HTML-safe instructions


GAMES: dict[str, Game] = {
    "valheim": Game(
        "valheim", "Valheim",
        "Viking survival co-op. Dedicated world, persists between sessions.",
        ram_gb=4,
        connect=lambda ip, meta: f"Join Game &rarr; Add server &rarr; <code>{ip}:2456</code>",
    ),
    "factorio": Game(
        "factorio", "Factorio",
        "Factory-building co-op. The factory must grow.",
        ram_gb=2,
        connect=lambda ip, meta: f"Multiplayer &rarr; Connect to address &rarr; <code>{ip}</code>",
    ),
    "satisfactory": Game(
        "satisfactory", "Satisfactory",
        "3D factory-building co-op on an alien planet.",
        ram_gb=12,
        connect=lambda ip, meta: f"Server Manager &rarr; Add Server &rarr; <code>{ip}</code> port <code>7777</code>",
    ),
    "dragonwilds": Game(
        "dragonwilds", "RuneScape: Dragonwilds",
        "Survival crafting in Ashenfall. Crossplay: PC, Xbox, PS5, Switch 2.",
        ram_gb=8,
        connect=lambda ip, meta: (
            "Play &rarr; <b>Public</b> tab &rarr; search world "
            f"<b>{meta.get('DRAGONWILDS_WORLD_NAME', 'Ashenfall')}</b>"
        ),
    ),
}


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    description: str
    url: str


# Static for v1 — no live health check (the portal, on Cloud Run, has no
# route into the home Tailscale mesh these mostly live on). Edit this list
# directly as services come and go; a real service registry (SkippyTel v1's
# GET /services, or its v2 successor) can replace this once it's reachable
# from here.
SERVICES: list[Service] = [
    Service("jellyfin", "Jellyfin", "Movies and TV", "http://skippy-pc:8096"),
    Service("skippytel", "SkippyTel", "Phone recovery + AI inference", "http://skippy-pc:3003/phone"),
]


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
