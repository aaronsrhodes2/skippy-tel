"""The plain spoken audiobook commands, caught before the model.

Skippy Core's chat model answers "stop" with a joke and "where was I" with an invented chapter instead of calling the audiobook tool, so the
words people actually say to a book ("stop", "next chapter", "back thirty seconds", "stop in twenty minutes", "where was I", "read my book") are matched
here. The tool then runs first and the model only words the answer (brain.Brain.pre_route). Anything not matched here still goes to the model, with the tool.

A command that could just as well mean Bilby's music ("stop", "resume") counts only while a book is playing or was the last thing played.
"""
from __future__ import annotations

import re

from audiobooks import STOP_WORDS

_NUMS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
         "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90}
_NUM = r"(?P<n>\d+(?:\.\d+)?|half an?|(?:forty|fifty|twenty|thirty|sixty|ninety)(?: five)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten|fifteen)"
_UNIT = r"(?P<u>seconds?|secs?|minutes?|mins?|hours?)"
_BOOKISH = r"(?: (?:the|my|our))?(?: (?:audio ?)?book|reading|playing)?"


def _amount(m) -> float | None:
    """Seconds from a matched number and unit ("twenty minutes", "half an hour", "45 seconds")."""
    n, u = m.group("n"), m.group("u")
    if n.startswith("half"):
        k = 0.5
    else:
        k = float(n) if n.replace(".", "").isdigit() else _NUMS.get(n)
        if k is None:
            return None
    return k * (3600 if u.startswith("h") else 60 if u.startswith("m") else 1)


def normalize(heard: str) -> str:
    t = re.sub(r"[^a-z0-9. ']+", " ", (heard or "").lower())
    t = re.sub(r"\b(?:hey |ok |okay )?skippy\b|\bplease\b|\bcan you\b|\bcould you\b|\bwould you\b|\bnow\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def pre_route(heard: str, room) -> dict | None:
    """The audiobook tool's arguments for a plain spoken command, or None (the model decides)."""
    player = getattr(room, "book", None)
    t = normalize(heard)
    if player is None or not t:
        return None
    book_now = player.playing or (player.book is not None and getattr(room, "last_media", "") == "book")
    wordy = "book" in t or "reading" in t

    m = (re.search(rf"(?:stop|pause|sleep|turn (?:it|the book) off|shut off){_BOOKISH} (?:in|after|for) {_NUM} {_UNIT}\b", t)
         or re.search(rf"sleep timer(?: (?:for|of))? {_NUM} {_UNIT}\b", t))
    if m and (book_now or wordy or "sleep" in t):
        secs = _amount(m)
        return {"action": "sleep", "minutes": round(secs / 60, 2)} if secs else None
    if book_now and re.search(r"(?:stop|pause|sleep)\b.*\bend of (?:this|the|that) chapter|(?:stop|pause) after (?:this|the|that) chapter", t):
        return {"action": "sleep", "end_of_chapter": True}
    if re.search(r"(?:cancel|turn off|clear) (?:the )?sleep timer", t):
        return {"action": "sleep", "minutes": 0} if book_now or player.sleep_at else None
    if re.fullmatch(rf"(?:stop|pause|hold on|wait|be quiet|quiet|silence|shut up|enough){_BOOKISH}(?: it)?", t) or re.fullmatch(r"stop reading", t):
        return {"action": "pause"} if player.playing or (wordy and player.book is not None) else None
    if (player.book is not None and not player.playing and (getattr(room, "last_media", "") == "book" or wordy)
            and re.fullmatch(rf"(?:resume|continue|keep reading|carry on|go on|unpause|keep going|play){_BOOKISH}(?: it)?(?: again)?", t)):
        return {"action": "resume"}
    if book_now and re.search(r"(?:next|following) chapter|skip (?:to the )?(?:next )?chapter|skip ahead a chapter|forward a chapter", t):
        return {"action": "next_chapter"}
    if book_now and re.search(r"(?:previous|prior|last) chapter|back a chapter|chapter back|(?:start|beginning) of (?:this|the) chapter|restart (?:this |the )?chapter", t):
        return {"action": "previous_chapter"}
    m = re.fullmatch(rf"(?:go |skip |jump |rewind |wind )?(?P<d>back|backwards?|rewind|forwards?|ahead|skip)(?: {_NUM} {_UNIT})?(?: a bit)?", t)
    if m and book_now:
        secs = _amount(m) if m.group("n") else 30
        if secs:
            return {"action": "forward" if m.group("d") in ("forward", "forwards", "ahead", "skip") else "back", "seconds": int(secs)}
    m = re.fullmatch(rf"(?:go |skip |jump |rewind )?(?P<d>back|forward|ahead) {_NUM} {_UNIT}", t)
    if m and book_now:
        secs = _amount(m)
        return {"action": "back" if m.group("d") == "back" else "forward", "seconds": int(secs)} if secs else None
    if re.search(r"where (?:was|am|were|are|did)\b.*\b(?:i|we)\b|what(?:'s| is) (?:this|the) (?:book|chapter)|what chapter|how much (?:is )?(?:left|remaining)\b|what am i (?:listening|reading)", t) \
            and (book_now or (player.book is not None and (wordy or "chapter" in t))):
        return {"action": "where"}
    if book_now and re.fullmatch(r"(?:speed up|faster|read faster|play faster|a bit faster|slow down|slower|read slower|play slower|normal speed|regular speed)", t):
        if "normal" in t or "regular" in t:
            return {"action": "speed", "speed": 1.0}
        return {"action": "speed", "speed": player.speed + (0.25 if "faster" in t or "speed up" in t else -0.25)}
    m = re.fullmatch(r"(?:set )?(?:the )?(?:book )?speed(?: to)? (?P<x>\d(?:\.\d+)?)(?: times| x)?", t)
    if m and book_now:
        return {"action": "speed", "speed": float(m.group("x"))}
    if re.fullmatch(r"(?:read|play|continue|resume|start|open)(?: reading)?(?: to me| me)?(?: (?:a|my|the|our))?(?: (?:audio ?)?books?)(?: again)?|read to me|read me a book|keep reading|continue reading", t):
        return {"action": "play"}
    m = re.fullmatch(r"(?:read|play|start|put on|listen to)(?: to me| me)?(?: (?:the |my |our )?(?:audio ?)?book)?(?: called| named| about| by)? (?P<what>.+?)(?: audio ?book| book)?", t)
    if m and not re.search(r"\b(?:music|song|songs|album|playlist|radio|some)\b", t):
        try:
            player.library.refresh()
        except Exception:                                      # noqa: BLE001 — the folder is away: the model path will say so
            return None
        best, many = player.library.find(m.group("what"))
        words = [w for w in m.group("what").split() if w not in STOP_WORDS]
        if many and ("read" in t or "book" in t or (best is not None and len(words) >= 2)):
            return {"action": "play", "title": m.group("what")}
    return None
