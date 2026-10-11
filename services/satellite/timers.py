"""Kitchen timers, the first thing a house voice is asked for. In memory: a restart forgets them (a timer is minutes long)."""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Timer:
    label: str
    due: float            # seconds on the injected clock
    seconds: int


class Timers:
    MAX = 20

    def __init__(self, clock=time.time):
        self.clock = clock
        self._timers: list[Timer] = []

    def add(self, seconds: int, label: str = "") -> Timer:
        seconds = int(seconds)
        if not 1 <= seconds <= 24 * 3600:
            raise ValueError("a timer is between one second and a day")
        if len(self._timers) >= self.MAX:
            raise ValueError("that is a lot of timers already")
        t = Timer(label=(label or "").strip()[:40] or self._name(seconds), due=self.clock() + seconds, seconds=seconds)
        self._timers.append(t)
        return t

    @staticmethod
    def _name(seconds: int) -> str:
        if seconds % 3600 == 0:
            n = seconds // 3600
            return f"{n} hour" + ("s" if n != 1 else "")
        if seconds >= 60 and seconds % 60 == 0:
            n = seconds // 60
            return f"{n} minute" + ("s" if n != 1 else "")
        return f"{seconds} second" + ("s" if seconds != 1 else "")

    def cancel(self, label: str = "") -> int:
        """Cancel the timers whose label contains `label` (all of them when it is blank); how many were cancelled."""
        keep = [t for t in self._timers if label and label.lower() not in t.label.lower()]
        n = len(self._timers) - len(keep)
        self._timers = keep
        return n

    def due(self) -> list[Timer]:
        now = self.clock()
        done = [t for t in self._timers if t.due <= now]
        self._timers = [t for t in self._timers if t.due > now]
        return done

    def remaining(self) -> list[tuple[str, int]]:
        now = self.clock()
        return [(t.label, max(0, int(t.due - now))) for t in sorted(self._timers, key=lambda t: t.due)]
