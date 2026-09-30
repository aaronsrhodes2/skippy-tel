"""People who asked the Captain for access, waiting on the Admin page beside the friends list.

Stored as JSON in the games VM's instance metadata (`portal-requests`), like the
friends (friends.py): no database, and infra/gcp-games/main.tf ignores the key so
Terraform never wipes it.

    {"stranger@example.com": {"name": "Jo", "games": ["valheim"], "note": "from the Discord",
                              "at": "2026-09-30T12:00:00+00:00"}}

The email is the one Google verified at sign-in, never something typed: nobody
can ask on behalf of an address they don't own. One request per email (asking again
adds the new games and updates the note). Everything a person typed is trimmed,
stripped of invisible/control characters and length-capped before it is stored.

Bounded, and fair to the people already waiting: at most MAX_ENTRIES requests, and
when the queue is full a NEW person is refused (Full) rather than an older request
being evicted, so a flood can't push genuine requests out; entries expire after
EXPIRE_DAYS so the queue can't clog forever.

Writes are safe: they run under a lock, re-read the VM's metadata fresh (never a
cached copy), and REFUSE to write if that read failed, so a hiccup in the VM API can
never turn into "the queue is empty, save just this one" and wipe every request. A
failed write is reported as Unavailable too, so callers never see a raw API error.
"""
from __future__ import annotations

import json
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone

from catalog import GAMES

KEY = "portal-requests"
CACHE_SECONDS = 10
MAX_ENTRIES = 50
EXPIRE_DAYS = 60
NAME_MAX = 80
NOTE_MAX = 140
MAX_MARKS = 2                       # combining marks kept after one base character (more is "zalgo" text)
# Blank-looking characters that aren't format characters: fillers, the braille blank, the combining grapheme joiner.
_BLANKS = set("ㅤᅟᅠﾠ⠀͏")


class Unavailable(RuntimeError):
    """The VM's metadata couldn't be read or written, so nothing was changed."""


class Full(RuntimeError):
    """The queue is full: a new person can't be added until the Captain answers some."""


def tidy(text, limit: int) -> str:
    """One short plain line. Control characters become spaces. Dropped: invisible format characters (zero-width,
    bidi overrides; this also removes joiners in emoji sequences, a deliberate trade), private-use and unassigned
    code points, blank-looking fillers, variation selectors, and combining marks beyond MAX_MARKS per letter."""
    kept, marks = [], 0
    for ch in str(text or ""):
        category = unicodedata.category(ch)
        if category in ("Cc", "Zl", "Zp"):
            kept.append(" ")
            marks = 0
        elif category in ("Cf", "Cs", "Co", "Cn") or ch in _BLANKS or 0xFE00 <= ord(ch) <= 0xFE0F or 0xE0100 <= ord(ch) <= 0xE01EF:
            continue
        elif category in ("Mn", "Me", "Mc"):
            marks += 1
            if marks <= MAX_MARKS:
                kept.append(ch)
        else:
            kept.append(ch)
            marks = 0
    return " ".join("".join(kept).split())[:limit].strip()


class Requests:
    def __init__(self, vm, clock=lambda: datetime.now(timezone.utc)):
        self.vm, self.clock = vm, clock
        self._cache: tuple[float, dict] | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _shape(data) -> dict:
        return {e: v for e, v in data.items() if isinstance(e, str) and isinstance(v, dict)} if isinstance(data, dict) else {}

    def all(self) -> dict[str, dict]:
        """For display: a few seconds old is fine, and a VM API hiccup shows what we last saw."""
        if self._cache and time.monotonic() - self._cache[0] < CACHE_SECONDS:
            return self._cache[1]
        try:
            data = self._shape(json.loads(self.vm.get_metadata(KEY) or "{}"))
        except (ValueError, TypeError):
            data = {}
        except Exception:   # noqa: BLE001 — VM API down
            return self._cache[1] if self._cache else {}
        self._cache = (time.monotonic(), data)
        return data

    def count(self) -> int:
        return len(self.all())

    def get(self, email: str) -> dict | None:
        return self.all().get((email or "").strip().lower())

    def _fresh(self) -> dict:
        """What is stored right now, for a read-modify-write. A failed read raises: it must not look like 'empty'."""
        try:
            raw = self.vm.get_metadata(KEY)
        except Exception as exc:   # noqa: BLE001
            raise Unavailable("couldn't read the request queue") from exc
        try:
            return self._shape(json.loads(raw or "{}"))
        except (ValueError, TypeError):
            return {}                                    # stored text is corrupt; starting over beats being stuck

    def _save(self, data: dict) -> None:
        try:
            self.vm.set_metadata(KEY, json.dumps(data, sort_keys=True))
        except Exception as exc:   # noqa: BLE001 — a compute API error, a metadata fingerprint conflict…
            raise Unavailable("couldn't save the request queue") from exc
        self._cache = None

    def _expire(self, data: dict) -> None:
        cutoff = (self.clock() - timedelta(days=EXPIRE_DAYS)).isoformat(timespec="seconds")
        for email in [e for e, v in data.items() if str(v.get("at", "")) < cutoff]:
            del data[email]

    def submit(self, email: str, name: str, games: list[str], note: str) -> str:
        """Queue a request, or add to the one already waiting. Returns "new" or "updated"."""
        email = (email or "").strip().lower()
        with self._lock:
            data = self._fresh()
            self._expire(data)
            before = data.get(email)
            if before is None and len(data) >= MAX_ENTRIES:
                raise Full("the request queue is full")
            wanted = [g for g in GAMES if g in (games or []) or (before and g in before.get("games", []))]
            data[email] = {"name": tidy(name, NAME_MAX) or (before or {}).get("name", ""),
                           "note": tidy(note, NOTE_MAX) or (before or {}).get("note", ""),
                           "games": wanted, "at": self.clock().isoformat(timespec="seconds")}
            self._save(data)
        return "updated" if before is not None else "new"

    def answer(self, email: str, granted: list[str]) -> list[str]:
        """The Captain granted `granted` (game ids and other portals). Whatever the person asked for that is covered
        comes off their request; the request goes when nothing is left. Returns the games still being asked for."""
        email = (email or "").strip().lower()
        with self._lock:
            data = self._fresh()
            entry = data.get(email)
            if entry is None:
                return []
            left = [g for g in entry.get("games", []) if g not in granted]
            if left:
                entry["games"] = left
                data[email] = entry
            else:
                del data[email]
            self._save(data)
            return left

    def dismiss(self, email: str) -> bool:
        with self._lock:
            data = self._fresh()
            if data.pop((email or "").strip().lower(), None) is None:
                return False
            self._save(data)
        return True
