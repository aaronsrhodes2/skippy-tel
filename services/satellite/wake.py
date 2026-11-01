"""What was said: is it for Skippy, is it "never mind", is it "goodbye"? (Ported from portal/static/skippy-voice.js: WAKE_RE, STOP_RE, END_RE.)"""
from __future__ import annotations

import re

WAKE_RE = re.compile(r"\b(?:(?:hey|hi|ok|okay|yo)[ ,]+)?(?:skipp(?:y|ie|ey|i)|skibb(?:y|ie)|gibby)\b[ ,.!?:;-]*", re.I)
STOP_RE = re.compile(r"^\s*(?:ok(?:ay)?[ ,]*)?(?:stop|cancel|wait|hold on|hold it|never ?mind|abort|that'?s all|that is all|thanks?|thank you)\s*[.!?]*\s*$", re.I)
_DECL = (r"(?:no(?:pe)?|nah|no thanks?|no thank you|not now|not right now|not today|not tonight|not really|not yet|maybe later|later|another time|"
         r"some other time|i'?m good|i'?m fine|i'?m ok(?:ay)?|i'?m all set|i'?ll pass|pass|no need|don'?t bother|skip it|no way)")
_DECL_MORE = (_DECL + r"|thanks?|thank you|(?:that'?s|it'?s|that is|it is) (?:ok(?:ay)?|fine|alright|all right)|"
              r"skippy|captain|buddy|man|though|really|for now|i'?m (?:good|fine|ok(?:ay)?)")
DECLINE_RE = re.compile(rf"^\s*(?:(?:oh|um+|uh+|well|ah|hmm+|ok(?:ay)?)[ ,]+)?{_DECL}(?:[ ,.!]+(?:{_DECL_MORE}))*\s*[.!?]*\s*$", re.I)
END_RE = re.compile(r"^\s*(?:ok(?:ay)?[ ,]*)?(?:good ?bye|bye(?: bye)?|stop listening|we'?re done|i'?m done|talk to you later)\s*[.!?]*\s*$", re.I)


def wake_split(text) -> tuple[bool, str]:
    """(woke, rest): "Hey Skippy, turn that down" -> (True, "turn that down"); "Skippy." -> (True, ""); room talk -> (False, "").
    The word counts only in the first 60 characters: it is an address, not a mention."""
    t = "" if text is None else str(text)
    m = WAKE_RE.search(t[:60])
    return (True, t[m.end():].strip()) if m else (False, "")


def is_stop(text) -> bool:
    return bool(STOP_RE.match(str(text or "")))


def is_decline(text) -> bool:
    """A plain no to something Skippy asked ("no", "nope", "not now, thanks", "I'm good", "maybe later"): the whole of what was said, nothing more.
    "No, play jazz instead" is not one: that goes to the brain."""
    return bool(DECLINE_RE.match(str(text or "")))


def is_goodbye(text) -> bool:
    return bool(END_RE.match(str(text or "")))
