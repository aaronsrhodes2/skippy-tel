"""Talking to Skippy the Foreman in a running Factorio game, by voice (the Captain, 2026-11-13: speech to text and text to speech in the
game, in Skippy's own voice, so the game can be played with no keyboard).

A Factorio mod has no microphone, no speaker and no network. The game's bridge (D:\\Aaron\\development\\AI-factorio, copilot/voice_door.py)
keeps a small door open on this PC while a game session is on, http://127.0.0.1:47862, with no key:

    GET  /health                    answers while a Factorio session is on
    POST /hear   {"text", "who"?}   a spoken line for the foreman -> {"ok", "id", "after"}
    GET  /said?after=N&wait=S       what the foreman has said since N, waiting up to S seconds -> {"lines": [{"id", "text", "voice"}], "next"}

This module is the house's side. It wraps a room's brain: while the door answers, what was heard goes to the game and the foreman's answer
is what Skippy says; the ears, the wake word and the voice stay exactly as they are. While no game is on (the door is shut), nothing
changes at all: every word goes to the house brain as before, and the check costs one refused connection on loopback every few seconds.

Which words are the house's while a game is on:
    "house, lights off"      begins with house / home: always the house
    "foreman, build more"    begins with foreman / factorio / game: always the game
    anything else            the house when it names a house thing (HOUSE_WORDS: lights, music, a timer, the garage, ...), else the game

    SATELLITE_FACTORIO_DOOR      the door (default http://127.0.0.1:47862); `off` never asks it
    SATELLITE_FACTORIO_WAIT      seconds to wait for the foreman's answer before saying it is still working (default 60)

Not here yet: speaking what the foreman says on his own (news of the game, a finished job) when nobody asked. Only answers are spoken.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

DOOR = "http://127.0.0.1:47862"
CHECK_EVERY_S = 5.0                 # how long one look at the door is trusted
POLL_S = 20.0                       # one wait for news (the door allows 30)
TO_HOUSE = re.compile(r"^\s*(?:the\s+)?(?:house|home)\b[\s,.:;!-]*", re.I)
TO_GAME = re.compile(r"^\s*(?:the\s+)?(?:foreman|factorio|game)\b[\s,.:;!-]*", re.I)
HOUSE_WORDS = re.compile(
    r"\b(lights?|lamps?|dim|brightness|thermostat|temperature|heat(?:er|ing)?|air conditioning|fan|garage|front door|back door|lock|unlock|"
    r"tv|television|projector|stereo|speakers?|volume|louder|quieter|mute|music|songs?|playlist|spotify|radio|bilby|audiobook|chapter|"
    r"timers?|alarms?|remind(?:er)?|shopping list|grocery|weather|forecast|news|tesla|car|generator|camera|doorbell|announce|"
    r"good ?night|good morning|what time|what day)\b", re.I)
STILL_WORKING = "The foreman is still working on that. Watch the game's chat."
NO_ANSWER = "The foreman did not answer. Typical."


def _http(method: str, url: str, body: dict | None = None, timeout: float = 5.0):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def whose(heard: str) -> tuple[str, str]:
    """-> ("house" | "game", the words without a leading "house," or "foreman,")."""
    m = TO_HOUSE.match(heard)
    if m:
        return "house", heard[m.end():].strip() or heard.strip()
    m = TO_GAME.match(heard)
    if m:
        return "game", heard[m.end():].strip() or heard.strip()
    return ("house" if HOUSE_WORDS.search(heard) else "game"), heard.strip()


class Door:
    """The game's voice door. Every method answers; none raises."""

    def __init__(self, url: str = DOOR, http=_http, clock=time.monotonic, wait_s: float = 60.0):
        self.url, self.http, self.clock, self.wait_s = url.rstrip("/"), http, clock, wait_s
        self._open, self._looked = False, None

    def is_open(self) -> bool:
        """Is a Factorio session on? One look is trusted for a few seconds, so a house command never waits on this."""
        now = self.clock()
        if self._looked is None or now - self._looked >= CHECK_EVERY_S:
            self._looked = now
            try:
                got = self.http("GET", self.url + "/health", None, 0.4)
                self._open = bool(got.get("ok")) and got.get("service") == "skippy-factorio"
            except (urllib.error.URLError, OSError, ValueError):
                self._open = False
        return self._open

    def ask(self, text: str, who: str = "") -> str | None:
        """Say `text` to the foreman and wait for his answer. -> what he said; STILL_WORKING when he has not answered in time; None when
        the door shut on us (the caller then lets the house answer)."""
        try:
            got = self.http("POST", self.url + "/hear", {"text": text, "who": who} if who else {"text": text}, 5.0)
        except (urllib.error.URLError, OSError, ValueError):
            self._open, self._looked = False, self.clock()
            return None
        if not got.get("ok"):
            return None
        after, began = int(got.get("after") or 0), self.clock()
        while True:
            left = self.wait_s - (self.clock() - began)
            if left <= 0:
                return STILL_WORKING
            try:
                news = self.http("GET", f"{self.url}/said?after={after}&wait={min(POLL_S, max(left, 0.1)):.1f}", None, POLL_S + 10)
            except (urllib.error.URLError, OSError, ValueError):
                return NO_ANSWER
            lines = [l.get("text", "") for l in news.get("lines") or [] if l.get("text")]
            if lines:
                return " ".join(lines)
            after = int(news.get("next") or after)


class GameBrain:
    """A room's brain with the game in front of it. Everything but `reply` is the brain's own: reading or setting any other attribute
    reads or sets it on the brain, so the hub's `brain.room = ...`, `brain.timing`, `brain.warm()` work as if this were not here."""

    _OWN = ("_brain", "_door", "_log", "_who")

    def __init__(self, brain, door: Door, log=print, who: str = ""):
        object.__setattr__(self, "_brain", brain)
        object.__setattr__(self, "_door", door)
        object.__setattr__(self, "_log", log)
        object.__setattr__(self, "_who", who)

    def __getattr__(self, name):
        return getattr(self._brain, name)

    def __setattr__(self, name, value):
        if name in self._OWN:
            object.__setattr__(self, name, value)
        else:
            setattr(self._brain, name, value)

    def reply(self, heard: str) -> str:
        to, words = whose(heard)
        if to == "game" and self._door.is_open():
            said = self._door.ask(words, self._who)
            if said is not None:
                self._log(f"(to the Factorio foreman: {words[:80]})")
                return said
            return self._brain.reply(heard)                         # the door shut between the look and the asking
        return self._brain.reply(words if to == "house" and words != heard.strip() else heard)


def wrap(brain, env: dict, log=print, who: str = ""):
    """The brain with the game in front of it, or the brain itself when SATELLITE_FACTORIO_DOOR is `off`."""
    url = (env.get("SATELLITE_FACTORIO_DOOR") or DOOR).strip()
    if url.lower() in ("off", "0", "no"):
        return brain
    try:
        wait_s = float(env.get("SATELLITE_FACTORIO_WAIT", "60"))
    except ValueError:
        wait_s = 60.0
    return GameBrain(brain, Door(url, wait_s=wait_s), log=log, who=who)
