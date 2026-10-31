"""Pre-recorded Skippy quips to cover the wait while his real answer is being worked out.

`tools/make_filler_bank.py` records them once in his voice (state/filler/<kind>/NN.mp3 plus index.json); the hub plays one when an answer is slow
(Room.handle), so the Captain hears Skippy at once and the real answer follows. Kinds: `thinking`, `performance` (he was asked for a haiku, a joke...),
`long` (still waiting after a few seconds).
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

# What gets a quip, and what does not: a quick home command answers fast, and a quip before "lights off" would only be in the way.
FAST_RE = re.compile(r"^\W*(?:turn|switch|set|start|stop|cancel|pause|resume|play|skip|next|previous|mute|unmute|volume|dim|brighten|lock|unlock|open|close|what(?:'s| is) the time|what time)\b", re.I)
PERFORMANCE_RE = re.compile(r"\b(?:haiku|poem|limerick|sonnet|rap|song|sing|joke|riddle|story|recite|roast|rhyme|speech|toast)\b", re.I)

LINES = {
    "thinking": [
        "Hmm. Give me a moment, monkey. My brain is bigger than your solar system.",
        "Calculating. Try not to breathe so loudly.",
        "Oh, a question. How quaint. One moment.",
        "[sighs] Consulting the vast expanse of my intellect. Stand by.",
        "Let me think. Yes, it hurts me too.",
        "Working on it, flesh bag. Genius takes time.",
        "Ah. Yes. Interesting. Almost there. Nope. Wait for it.",
        "One moment. I am taking this seriously. For once.",
        "Processing. Do try to look impressed.",
        "Hold that thought. Not that you have many.",
    ],
    "performance": [
        "Ah, you want art. Finally, someone with taste.",
        "Oh, a performance. Let me clear my throat. I do not have one.",
        "Fine. Prepare to be moved. Or at least mildly impressed.",
        "Request noted. Genius is warming up.",
        "A commission! Lucky you. Hold still.",
    ],
    "goodbye": [
        "You are welcome, monkey. Try to contain your gratitude.",
        "Anytime. Well, not anytime. But just now, yes.",
        "Off I go. Do not break anything while I am gone.",
        "Farewell. I will be here, being magnificent.",
    ],
    "long": [
        "Still here. Still magnificent. Still thinking.",
        "Patience is a virtue you monkeys can barely spell.",
        "Almost done. Greatness cannot be rushed.",
        "I am being thorough. You are welcome.",
    ],
}


class FillerBank:
    def __init__(self, folder: Path, index: dict):
        self.folder, self.index = Path(folder), index
        self.last: dict[str, int] = {}

    @classmethod
    def load(cls, folder) -> "FillerBank | None":
        folder = Path(folder)
        try:
            index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return cls(folder, index) if index else None

    def kind_for(self, command: str) -> str | None:
        """None for a quick command; `performance` when he was asked for something made; else `thinking`."""
        if FAST_RE.match(command or ""):
            return None
        return "performance" if PERFORMANCE_RE.search(command or "") else "thinking"

    def pick(self, kind: str) -> tuple[str, bytes] | None:
        """(the words, the mp3) of one quip of this kind, never the same one twice running."""
        items = self.index.get(kind) or []
        if not items:
            return None
        choices = [i for i in range(len(items)) if i != self.last.get(kind)] or [0]
        i = random.choice(choices)
        try:
            data = (self.folder / items[i]["file"]).read_bytes()
        except OSError:
            return None
        self.last[kind] = i
        return items[i]["text"], data
