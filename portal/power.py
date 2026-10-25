"""The house's power, as its UPSes see it: skippy-pc's (read by the starter, tools/skippy_starter.py) and skippy-core's (NUT on the box,
core/install/ups.sh, which posts its events to /home/ups). One Discord DM to the Captain per change, never a stream of them:
the power going out, a battery running low, the power coming back (the Captain, 2026-10-25).

skippy-pc shuts itself down after 2 minutes on battery and skippy-core after 3, so an outage reads: "out" (from whichever box saw it
first), then nothing while the boxes go down, then "back" from skippy-core, which also wakes skippy-pc.
"""
from __future__ import annotations

import threading
import time

NAMES = {"pc": "skippy-pc", "core": "skippy-core"}


class Power:
    def __init__(self, notify=None, clock=time.time):
        self.notify = notify or (lambda text: False)
        self.clock = clock
        self._lock = threading.Lock()
        self.state: dict[str, dict] = {}         # box -> {"on_battery", "low", "charge", "runtime_min", "at"}

    def observe(self, box: str, on_battery: bool | None, charge: int | None = None, runtime_min: int | None = None,
                low: bool = False) -> str | None:
        """A reading from one box's UPS. Returns the message it sent, if this reading was news."""
        if box not in NAMES or on_battery is None:
            return None
        with self._lock:
            was = self.state.get(box)
            self.state[box] = {"on_battery": on_battery, "low": low, "charge": charge, "runtime_min": runtime_min, "at": self.clock()}
            if was is None and not on_battery:
                return None                                  # the first reading after a portal start, all is well: not news
            text = None
            left = "" if charge is None else f" (battery {charge}%" + (f", ~{runtime_min} min left)" if runtime_min else ")")
            if on_battery and (was is None or not was["on_battery"]):
                text = f"Power is out at home: {NAMES[box]} is on its UPS{left}."
            elif on_battery and low and not (was or {}).get("low"):
                text = f"{NAMES[box]}'s UPS battery is low{left}: it is shutting down."
            elif not on_battery and was is not None and was["on_battery"]:
                text = f"Power is back at home ({NAMES[box]}'s UPS is on mains again)."
        if text:
            try:
                self.notify(text)
            except Exception:  # noqa: BLE001 — a DM must never break a report
                pass
        return text

    def view(self) -> dict:
        with self._lock:
            return {b: dict(s, age_s=int(self.clock() - s["at"])) for b, s in self.state.items()}
