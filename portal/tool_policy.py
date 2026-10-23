"""Who may use which of skippy-tel-core's MCP servers through the portal chat.

The portal chat is open to every friend on the portal, but skippy-tel-core
also holds the Captain's private life (mail, calendar, Drive, memory, …).
Every MCP server gets an audience here:

    "everyone"  friends and admins — features of the chat (e.g. physics)
    "admin"     only portal admins (the Captain)

Anything not listed is admin-only: a server added to skippy-tel-core later
can never reach friends by accident; opening it up is a line here.

Not wired to skippy-tel-core yet (the portal has no route into the home
mesh; docs/portal/README.md → Chat). When the bridge lands, it must build the
tool list with `allowed_servers(is_admin)` and refuse calls with `allowed`.
"""
from __future__ import annotations

EVERYONE, ADMIN = "everyone", "admin"

MCP_SERVERS: dict[str, str] = {
    # Features of the chat, fine for friends
    "mentat": EVERYONE,     # sigma-ground's physics tools (the Wolfram rival), hosted as skippy-mentat
    "physics": EVERYONE,
    # The Captain's hardware: skippy-pc's GPU makes the music, so it's his alone
    "music_gen": ADMIN,
    "image_gen": EVERYONE,      # make_image: this PC's GPU; guests 10 a day (app.py IMAGES_PER_DAY), the Captain no limit
    # The Captain's private data
    "gmail": ADMIN,
    "calendar": ADMIN,
    "drive": ADMIN,
    "memory": ADMIN,        # Skippy Memory: every past conversation
    "contacts": ADMIN,
    "sms": ADMIN,
    "location": ADMIN,
    "bilby": ADMIN,         # home music player
    "jellyfin": ADMIN,
}


def audience(server: str) -> str:
    return MCP_SERVERS.get(server.strip().lower(), ADMIN)


def allowed(server: str, is_admin: bool) -> bool:
    return is_admin or audience(server) == EVERYONE


def allowed_servers(servers: list[str], is_admin: bool) -> list[str]:
    return [s for s in servers if allowed(s, is_admin)]
