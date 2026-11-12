"""Kitchen timers, the first thing a house voice is asked for. Kept on disk when given a path (state/timers/<room>.json): the hub is restarted
often, and a pasta timer must outlive that."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


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
