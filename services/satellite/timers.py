"""Kitchen timers, the first thing a house voice is asked for. Kept on disk when given a path (state/timers/<room>.json): the hub is restarted
often, and a pasta timer must outlive that."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_UNIT_SECONDS = {"second": 1, "seconds": 1, "sec": 1, "secs": 1, "minute": 60, "minutes": 60, "min": 60, "mins": 60, "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600}
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def length_label(seconds: int) -> str:
    """The name a timer gets when nobody names it: "5 minutes", "90 seconds", "1 hour"."""
    if seconds % 3600 == 0:
        n = seconds // 3600
        return f"{n} hour" + ("s" if n != 1 else "")
    if seconds >= 60 and seconds % 60 == 0:
        n = seconds // 60
        return f"{n} minute" + ("s" if n != 1 else "")
    return f"{seconds} second" + ("s" if seconds != 1 else "")


def spoken_length(words: str) -> int | None:
    """The seconds `words` say when they are NOTHING BUT one count and one unit, in digits or in words, singular or plural: "five minute", "5 minutes", "Ninety-Second", "an hour",
    "twenty five minutes". None for anything else: a name ("pasta"), a unit alone ("second" is also an ordinal), a length with more words, "half an hour" (the brain's parser reads those)."""
    toks = re.findall(r"\d+|[a-z]+", str(words or "").lower())
    while toks and toks[0] in ("the", "my", "our", "that", "this"):
        toks = toks[1:]
    if len(toks) == 2 and toks[0] in ("a", "an") and toks[1] in _UNIT_SECONDS:
        return _UNIT_SECONDS[toks[1]]
    if toks and toks[0] in ("a", "an"):
        toks = toks[1:]                                              # ("a five minute")
    if len(toks) < 2 or toks[-1] not in _UNIT_SECONDS:
        return None
    count, rest = None, toks[:-1]
    if len(rest) == 1 and rest[0].isdigit():
        count = int(rest[0])
    elif len(rest) == 1 and rest[0] in _ONES:
        count = _ONES.index(rest[0])
    elif rest and rest[0] in _TENS and (len(rest) == 1 or (len(rest) == 2 and rest[1] in _ONES[1:10])):
        count = _TENS[rest[0]] + (_ONES.index(rest[1]) if len(rest) == 2 else 0)
    return count * _UNIT_SECONDS[toks[-1]] if count else None


@dataclass
class Timer:
    label: str
    due: float            # seconds on the injected clock
    seconds: int


class Timers:
    MAX = 20

    def __init__(self, clock=time.time, path: Path | str | None = None):
        self.clock = clock
        self.path = Path(path) if path else None
        self._timers: list[Timer] = []
        if self.path is not None and self.path.is_file():
            try:
                self._timers = [Timer(**t) for t in json.loads(self.path.read_text(encoding="utf-8"))]
            except (OSError, ValueError, TypeError):
                self._timers = []
            self._timers = [t for t in self._timers if t.due > self.clock() - 600]    # one that ran out while the hub was down is still announced, if recent

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(t) for t in self._timers]), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def add(self, seconds: int, label: str = "") -> Timer:
        seconds = int(seconds)
        if not 1 <= seconds <= 24 * 3600:
            raise ValueError("a timer is between one second and a day")
        if len(self._timers) >= self.MAX:
            raise ValueError("that is a lot of timers already")
        t = Timer(label=(label or "").strip()[:40] or self._name(seconds), due=self.clock() + seconds, seconds=seconds)
        self._timers.append(t)
        self._save()
        return t

    @staticmethod
    def _name(seconds: int) -> str:
        return length_label(seconds)

    def cancel(self, label: str = "") -> int:
        """Cancel the timers whose label contains `label` (all of them when it is blank); how many were cancelled.
        A label that is nothing but a length ("five minute", "5 minutes", "ninety seconds", "an hour") cancels the timers SET for that long, whatever digits or words their names use:
        "cancel the five minute timer" found nothing while the timer was named "5 minutes", and the substring match took "5 minutes" for a part of "25 minutes". An unnamed timer of that
        length goes first (the pasta timer that also runs five minutes stays); with none unnamed, the named ones of that length go."""
        seconds = spoken_length(label) if label else None
        if seconds is not None:
            of_length = [t for t in self._timers if t.seconds == seconds]
            gone = {id(t) for t in ([t for t in of_length if t.label == length_label(t.seconds)] or of_length)}
            keep = [t for t in self._timers if id(t) not in gone]
        else:
            keep = [t for t in self._timers if label and label.lower() not in t.label.lower()]
        n = len(self._timers) - len(keep)
        self._timers = keep
        self._save()
        return n

    def due(self) -> list[Timer]:
        now = self.clock()
        done = [t for t in self._timers if t.due <= now]
        if done:
            self._timers = [t for t in self._timers if t.due > now]
            self._save()
        return done

    def remaining(self) -> list[tuple[str, int]]:
        now = self.clock()
        return [(t.label, max(0, int(t.due - now))) for t in sorted(self._timers, key=lambda t: t.due)]
