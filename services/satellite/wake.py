"""What was said: is it for Skippy, is it "never mind", is it "goodbye"? (Ported from portal/static/skippy-voice.js: WAKE_RE, STOP_RE, END_RE.)"""
from __future__ import annotations

import re

WAKE_RE = re.compile(r"\b(?:(?:hey|hi|ok|okay|yo)[ ,]+)?(?:skipp(?:y|ie|ey|i)|skibb(?:y|ie)|gibby)\b[ ,.!?:;-]*", re.I)
STOP_RE = re.compile(r"^\s*(?:ok(?:ay)?[ ,]*)?(?:stop|cancel|wait|hold on|hold it|never ?mind|abort|that'?s all|that is all|thanks?|thank you)\s*[.!?]*\s*$", re.I)
END_RE = re.compile(r"^\s*(?:ok(?:ay)?[ ,]*)?(?:good ?bye|bye(?: bye)?|stop listening|we'?re done|i'?m done|talk to you later)\s*[.!?]*\s*$", re.I)


def wake_split(text) -> tuple[bool, str]:
    """(woke, rest): "Hey Skippy, turn that down" -> (True, "turn that down"); "Skippy." -> (True, ""); room talk -> (False, "").
    The word counts only in the first 60 characters: it is an address, not a mention."""
    t = "" if text is None else str(text)
    m = WAKE_RE.search(t[:60])
    return (True, t[m.end():].strip()) if m else (False, "")


def is_stop(text) -> bool:
    return bool(STOP_RE.match(str(text or "")))


def is_goodbye(text) -> bool:
    return bool(END_RE.match(str(text or "")))
