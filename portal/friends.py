"""Friends an admin has let into the portal, and which games each may see.

Stored as JSON in the games VM's instance metadata (`portal-friends`), next
to the server settings — no database, no deploy to add someone, and the
portal's service account can already write it. infra/gcp-games/main.tf
ignores the key so Terraform never wipes it.

    {"pal@example.com": {"portals": ["dragonwilds", "skippy"], "added_by": "…", "added": "2026-09-29"}}

(Entries from before portals existed have "games" instead; they read as those
games plus Skippy, which friends had implicitly then.)

People in ALLOWED_EMAILS (the deploy config) are "core" users: they always
get in and see every game. Friends see only the games they were granted.
Reads are cached for a few seconds because every request checks access;
writes clear the cache so a removal bites on the next page load.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date

from catalog import GAMES, PORTALS

KEY = "portal-friends"
CACHE_SECONDS = 10
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Unavailable(RuntimeError):
    """The VM's metadata couldn't be read or written, so the friends list was left alone."""


def normalize(email: str) -> str | None:
    email = (email or "").strip().lower()
    return email if _EMAIL.match(email) else None


class Friends:
    def __init__(self, vm):
        self.vm = vm
        self._cache: tuple[float, dict] | None = None

    def all(self) -> dict[str, dict]:
        if self._cache and time.monotonic() - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        try:
            data = json.loads(self.vm.get_metadata(KEY) or "{}")
        except (ValueError, TypeError):
            data = {}
        except Exception:   # noqa: BLE001 — VM API down: fail closed for friends (core users still get in)
            return self._cache[1] if self._cache else {}
        data = {e: v for e, v in data.items() if isinstance(v, dict)}
        self._cache = (time.monotonic(), data)
        return data

    def get(self, email: str) -> dict | None:
        return self.all().get((email or "").strip().lower())

    def _fresh(self) -> dict:
        """What is stored right now, for a read-modify-write. A failed read raises Unavailable: it must never look
        like "no friends", or the save that follows would write back only the one person changed."""
        try:
            raw = self.vm.get_metadata(KEY)
        except Exception as exc:   # noqa: BLE001
            raise Unavailable("couldn't read the friends list") from exc
        try:
            data = json.loads(raw or "{}")
        except (ValueError, TypeError):
            raise Unavailable("the stored friends list is unreadable") from None      # never overwrite what we can't read
        return {e: v for e, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        try:
            self.vm.set_metadata(KEY, json.dumps(data, sort_keys=True))
        except Exception as exc:   # noqa: BLE001
            raise Unavailable("couldn't save the friends list") from exc
        self._cache = None

    def upsert(self, email: str, portals: list[str], added_by: str) -> None:
        data = self._fresh()
        entry = dict(data.get(email, {"added_by": added_by, "added": date.today().isoformat()}))
        entry.pop("games", None)
        entry["portals"] = [p for p in PORTALS if p in portals]
        data[email] = entry
        self._save(data)

    def remove(self, email: str) -> None:
        data = self._fresh()
        if data.pop(email, None) is not None:
            self._save(data)


def portals_of(entry: dict | None) -> list[str]:
    """A friend's granted portals, reading pre-portal entries as games + Skippy."""
    if not entry:
        return []
    if "portals" in entry:
        held = set(entry["portals"]) | ({"audio"} if "bilby" in entry["portals"] else set())   # the old Bilby grant
        return [p for p in PORTALS if p in held]
    return [p for p in PORTALS if p in entry.get("games", [])] + ["skippy"]


def invite_text(email: str, games: list[str], sender: str, portal_url: str,
                discord_links: dict[str, str]) -> tuple[str, str]:
    """(subject, body) of the invite note: for the email link below, and for a Copy button that
    puts the same words on the clipboard to paste into Discord or a text message."""
    names = [PORTALS[p] for p in games if p in PORTALS]
    lines = [
        "Hey!",
        "",
        "I've set you up on my portal"
        + (f" for {', '.join(names)}." if names else "."),
        "",
        f"1. Go to {portal_url} and sign in with Google using this address ({email}).",
        "   (Not a Gmail user? You can make a Google account for any email address.)",
        "2. Open a game and press Play. The server wakes up in about 6 minutes and goes back",
        "   to sleep by itself when nobody's playing, so start it whenever you like.",
        "3. The game's page has everything to join: invite code, password, and address.",
    ]
    chats = [f"   {GAMES[g].name}: {discord_links[g]}" for g in games if discord_links.get(g)]
    if chats:
        lines += ["", "Chat and voice on Discord:"] + chats
    lines += ["", "See you in there,", sender]
    subject = "You're invited: " + (", ".join(names) if names else "my game servers")
    return subject, "\n".join(lines)


def invite_mailto(email: str, games: list[str], sender: str, portal_url: str,
                  discord_links: dict[str, str]) -> str:
    """A mailto: link that opens the admin's own email app with the invite
    written: sent from their address, so it arrives like any note from them."""
    from urllib.parse import quote
    subject, body = invite_text(email, games, sender, portal_url, discord_links)
    return f"mailto:{quote(email)}?subject={quote(subject)}&body={quote(body)}"
