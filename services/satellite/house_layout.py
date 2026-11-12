"""The house's layout (house_layout.json beside this file): floors, rooms, the names he says for them, and what is in each room.

ONE source of truth for "upstairs", "the theater", "everywhere" or "the living room lights", whatever reaches the thing (HomeKit, Matter, Leviton's
cloud, a router output). The backends' own room names stay as they are; a room given here wins.

    layout = house_layout.default()
    layout.place("upstairs")              -> ["living room", "kitchen", "bedroom"]   (a room, an alias, a floor, or everywhere; None when none)
    layout.split("upstairs lights")       -> (["living room", ...], "lights")       (the place phrase taken out of a longer phrase)
    layout.room_of("light.bedroom", "Bedroom") -> "bedroom"
    layout.outputs("bilby upstairs")      -> ["enclave"]; "everywhere" -> ["hdmi", "enclave"]   (router kinds, speaker_out.py)
    layout.primer()                       -> a few sentences for Skippy's prompt

SKIPPY_HOUSE_LAYOUT names another file (tests, a second house).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

FILE = Path(__file__).resolve().with_name("house_layout.json")


def words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()).split()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "thing"


def _has(hay: list[str], needle: list[str]) -> int:
    """Where the words `needle` start in `hay` as a run of whole words, or -1."""
    n = len(needle)
    for i in range(len(hay) - n + 1):
        if hay[i:i + n] == needle:
            return i
    return -1


class Layout:
    def __init__(self, data: dict):
        self.data = data or {}
        self.floors: dict[str, dict] = self.data.get("floors") or {}
        self.rooms: dict[str, dict] = self.data.get("rooms") or {}
        self._phrases: list[tuple[list[str], list[str]]] = []         # (the words he says, the rooms they mean), longest first
        for w in self.data.get("everywhere") or ["everywhere"]:
            self._phrases.append((words(w), list(self.rooms)))
        for name, floor in self.floors.items():
            for w in [name] + list(floor.get("aliases") or []):
                self._phrases.append((words(w), [r for r in floor.get("rooms") or [] if r in self.rooms]))
        for name, room in self.rooms.items():
            for w in [name] + list(room.get("aliases") or []):
                self._phrases.append((words(w), [name]))
        self._phrases = [p for p in self._phrases if p[0]]
        self._phrases.sort(key=lambda p: len(p[0]), reverse=True)
        self._device_room: dict[str, str] = {}
        for name, room in self.rooms.items():
            for d in room.get("devices") or []:
                for key in (d.get("id"), d.get("name")):
                    if key:
                        self._device_room.setdefault(slug(str(key).split(".", 1)[-1]), name)
                        self._device_room.setdefault(str(key).lower(), name)

    # ── places ──────────────────────────────────────────────────────────────────
    def find(self, text: str) -> tuple[list[str] | None, int, int, list[str]]:
        """(the rooms the longest place phrase in `text` names, where it starts, how many words it has, all the words); (None, 0, 0, words) when it names no place."""
        have = words(text)
        for phrase, rooms in self._phrases:
            i = _has(have, phrase)
            if i >= 0:
                return list(rooms), i, len(phrase), have
        return None, 0, 0, have

    def take(self, text: str) -> tuple[list[str] | None, str, str]:
        """(the rooms the longest place phrase in `text` names, that phrase, the rest of the words); (None, "", text) when it names no place."""
        rooms, i, n, have = self.find(text)
        if rooms is None:
            return None, "", " ".join(have)
        return rooms, " ".join(have[i:i + n]), " ".join(have[:i] + have[i + n:])

    def split(self, text: str) -> tuple[list[str] | None, str]:
        """(the rooms the longest place phrase in `text` names, the rest of the words); (None, text) when it names no place."""
        rooms, _, rest = self.take(text)
        return rooms, rest

    def place(self, text: str) -> list[str] | None:
        """The rooms `text` names when it is ONLY a place ("upstairs", "the theater", "everywhere"); None otherwise."""
        rooms, rest = self.split(text)
        return rooms if rooms is not None and not [w for w in words(rest) if w not in ("the", "in", "on", "my", "whole")] else None

    def floor_of(self, room: str) -> str:
        return next((f for f, d in self.floors.items() if room in (d.get("rooms") or [])), "")

    def room_of(self, entity_id: str, label: str = "", area: str = "") -> str:
        """The room a device is in: the layout's word first, then the backend's own room, then a room named in its name ("kitchen under 1")."""
        eid = str(entity_id or "")
        for key in (eid.lower(), slug(eid.split(".", 1)[-1]) if eid else "", str(label or "").lower(), slug(label) if label else ""):
            if key and key in self._device_room:
                return self._device_room[key]
        for text in (area, label):
            if text:
                rooms, _ = self.split(text)
                if rooms is not None and len(rooms) == 1:
                    return rooms[0]
        return ""

    def device(self, entity_id: str, label: str = "") -> dict:
        """The layout's entry for a device ({"kind": "light", ...}), or {}."""
        keys = {str(entity_id or "").lower(), slug(str(entity_id or "").split(".", 1)[-1]), str(label or "").lower(), slug(label) if label else ""} - {""}
        for room in self.rooms.values():
            for d in room.get("devices") or []:
                if {str(d.get("id") or "").lower(), slug(str(d.get("id") or "").split(".", 1)[-1]) if d.get("id") else "",
                        str(d.get("name") or "").lower(), slug(d["name"]) if d.get("name") else ""} & keys:
                    return d
        return {}

    # ── outputs (the router's kinds) ────────────────────────────────────────────
    def outputs(self, text: str) -> list[str] | None:
        """The router outputs a place phrase means ("upstairs" -> ["enclave"], "everywhere" -> both); None when it names no place,
        [] when the place has no speaker of its own (the office)."""
        rooms, _ = self.split(text)
        if rooms is None:
            return None
        out: list[str] = []
        for r in rooms:
            for o in self.rooms.get(r, {}).get("outputs") or []:
                if o not in out:
                    out.append(o)
        return out

    def place_words(self, kinds) -> str:
        """Where a set of router outputs is, in his words: {"hdmi", "enclave"} -> "everywhere", {"enclave"} -> "upstairs",
        {"hdmi"} -> "downstairs" (a floor when only its rooms have them, else the room); '' for none."""
        kinds = set(kinds)
        if not kinds:
            return ""
        if kinds >= set(self.all_outputs()):
            return "everywhere"
        names = []
        for floor, d in self.floors.items():
            rooms = [r for r in d.get("rooms") or [] if kinds & set(self.rooms.get(r, {}).get("outputs") or [])]
            outs = {o for r in d.get("rooms") or [] for o in self.rooms.get(r, {}).get("outputs") or []}
            if rooms and outs <= kinds:
                names.append(floor)
            else:
                names += [r for r in rooms if r not in names]
        return " and ".join(names)

    def all_outputs(self) -> list[str]:
        return self.outputs("everywhere") if self.data.get("everywhere") else \
            [o for r in self.rooms.values() for o in r.get("outputs") or []]

    def room_of_pod(self, hub_room: str) -> str:
        """The layout room a hub room (SATELLITE_ROOMS' name: "office", "theater") is in."""
        hub_room = str(hub_room or "").lower()
        for name, room in self.rooms.items():
            if hub_room in [p.lower() for p in room.get("pods") or []]:
                return name
        return hub_room if hub_room in self.rooms else ""

    # ── for Skippy ──────────────────────────────────────────────────────────────
    def primer(self, here: str = "") -> str:
        """A few plain sentences: the floors, their rooms, which speaker each has, and where he is now."""
        lines = []
        for floor, d in self.floors.items():
            parts = []
            for r in d.get("rooms") or []:
                room = self.rooms.get(r) or {}
                speaker = [x["name"] for x in room.get("devices") or [] if x.get("kind") == "speaker" and not x.get("planned")]
                lights = sum(1 for x in room.get("devices") or [] if x.get("kind") == "light")
                bits = ([f"speakers: {', '.join(speaker)}"] if speaker else []) + ([f"{lights} light{'s' if lights != 1 else ''}"] if lights else [])
                parts.append(r + (f" ({'; '.join(bits)})" if bits else ""))
            lines.append(f"{floor}: " + ", ".join(parts))
        room = self.room_of_pod(here)
        where = f" He is talking to you from the {room} ({self.floor_of(room)})." if room else ""
        return ("\n\n## The house\n" + ". ".join(lines) + "." + where +
                " A floor means every room on it and 'everywhere' means the whole house: 'the upstairs lights' is every light upstairs, "
                "'play it upstairs' is the living room's Enclave speakers, 'downstairs' or 'the theater' is the Sony.")


_default: Layout | None = None
_default_key: tuple | None = None


def load(path: str | os.PathLike | None = None) -> Layout:
    p = Path(path or os.environ.get("SKIPPY_HOUSE_LAYOUT") or FILE)
    try:
        return Layout(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Layout({})


def default() -> Layout:
    """The layout file, read again when it changes (so a new room or a placed light needs no hub restart)."""
    global _default, _default_key
    p = Path(os.environ.get("SKIPPY_HOUSE_LAYOUT") or FILE)
    try:
        key = (str(p), p.stat().st_mtime_ns)
    except OSError:
        key = (str(p), 0)
    if _default is None or key != _default_key:
        _default, _default_key = load(p), key
    return _default
