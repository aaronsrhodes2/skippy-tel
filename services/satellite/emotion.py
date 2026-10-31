"""What Skippy feels while he says something, for the box's drawn face. Rules over the words (and the question that was asked), no model call,
so it costs nothing before he speaks: the hub sends the box a track, one letter per 100 ms of his voice, and the box reads it by elapsed time.

Emotions (the letter is the code; UPPERCASE is strong, lowercase mild):
  n neutral  h happy  e excited  l laughing  s surprised  q curious  a annoyed  m smug  d sad  w worried  b bored  r embarrassed  v loving  k sick
"""
from __future__ import annotations

import re

STEP_S = 0.1

_RULES = [                                   # (emotion, words that suggest it): the first that matches wins, in this order
    ("l", ("haha", "ha ha", "lol", "[laughs]", "[chuckles]", "hilarious", "funny")),
    ("v", ("love", "adore", "darling", "sweetheart", "[affection")),
    ("r", ("blush", "embarrass", "awkward", "oops", "my bad", "ahem")),
    ("k", ("sick", "nauseous", "queasy", "fever", "feel awful", "feeling ill", "i'm ill", "i am ill", "disgusting", "[coughs", "[sneezes", "cough")),
    ("w", ("careful", "danger", "warning", "worried", "afraid", "uh oh", "problem", "broken", "error", "failed", "[nervous")),
    ("d", ("sorry", "unfortunately", "can't", "cannot", "unable", "alas", "sadly", "miss you", "[sad", "[sobs")),
    ("a", ("ugh", "seriously", "idiot", "stupid", "moron", "annoying", "unbelievable", "enough", "[groans", "[annoyed", "[angry")),
    ("b", ("boring", "yawn", "whatever", "meh", "[sighs]", "[yawns", "tedious", "again?")),
    ("m", ("obviously", "genius", "of course", "naturally", "magnificent", "monkey", "you're welcome", "as i said", "clearly")),
    ("s", ("wow", "whoa", "no way", "incredible", "amazing", "what?!", "really?!", "[gasps")),
    ("e", ("awesome", "fantastic", "excellent", "yes!", "finally", "let's go", "[excited")),
    ("h", ("great", "glad", "happy", "good news", "done", "ready", "thank", "welcome", "nice")),
]


def split(text: str) -> list[str]:
    """Sentences, with a stage direction like [sighs] kept with the sentence it opens."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p for p in parts if p]


def emotion_of(sentence: str, asked: str = "") -> str:
    """One sentence's emotion letter (uppercase when it is strong)."""
    t = sentence.lower()
    strong = "!" in sentence or (len(re.findall(r"\b[A-Z]{3,}\b", sentence)) > 0) or sentence.count("?") > 1
    for code, words in _RULES:
        if any(w in t for w in words):
            return code.upper() if strong else code
    if sentence.rstrip().endswith("?"):
        return "Q" if strong else "q"
    if "!" in sentence:
        return "H"
    return "n"


def reaction(asked: str) -> str:
    """How he looks while he listens and thinks about what he was just told (a mild expression: the box scales it down)."""
    t = (asked or "").lower()
    if not t:
        return "n"
    if any(w in t for w in ("haha", "lol", "joke", "funny")):
        return "l"
    if any(w in t for w in ("love you", "thank", "thanks", "you're the best", "good job", "well done", "nice work")):
        return "r"
    if any(w in t for w in ("stupid", "idiot", "shut up", "you suck", "useless", "dumb")):
        return "d"
    if any(w in t for w in ("i'm sick", "i am sick", "feel sick", "feeling sick", "help", "broken", "doesn't work", "not working", "error", "problem", "emergency", "hurry")):
        return "w"
    if any(w in t for w in ("tell me about", "explain", "why", "how does", "what is")):
        return "q"
    if t.rstrip().endswith("?"):
        return "q"
    return "n"


def track(said: str, asked: str, seconds: float, cap: int = 248) -> str:
    """One letter per STEP_S of his voice: each sentence gets the share of the clip its characters are of the whole reply."""
    sentences = split(said)
    if not sentences or seconds <= 0:
        return ""
    weights = [max(1, len(re.sub(r"\[[^\]]*\]", "", s))) for s in sentences]
    total, out, t = float(sum(weights)), [], 0.0
    for s, w in zip(sentences, weights):
        code = emotion_of(s, asked)
        n = max(1, round(seconds * w / total / STEP_S))
        out.append(code * n)
        t += n
    return "".join(out)[:cap]
