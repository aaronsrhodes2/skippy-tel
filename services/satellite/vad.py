"""Voice activity detection for the home listener: when does someone start talking, and when are they done?

A port of createVad() in portal/static/skippy-voice.js (same numbers, same tests' shape), fed one loudness reading (the RMS of a block of
microphone samples, 0..1) with a clock in milliseconds. It answers "speech" once (they began), "end" once (a pause after speaking) or None.
The bar for "speaking" follows the room: a multiple of the quiet level it learned in its first 400 ms (the quietest 100 ms window), never
below a floor. A USB microphone is quieter than a phone's after its automatic gain, so the floor is a setting (see ear.py: SATELLITE_FLOOR).
"""
from __future__ import annotations

import math


def rms(samples) -> float:
    """Loudness of a block: the root mean square of float samples in -1..1."""
    n = len(samples)
    if not n:
        return 0.0
    return math.sqrt(sum(float(x) * float(x) for x in samples) / n)


class Vad:
    def __init__(self, min_speech_ms: int = 160, end_silence_ms: int = 1100, floor_min: float = 0.012, ratio: float = 3.2,
                 cal_ms: int = 400, quiet_cap: float = 0.06):
        self.min_speech_ms, self.end_silence_ms, self.floor_min, self.ratio = min_speech_ms, end_silence_ms, floor_min, ratio
        self.cal_ms, self.quiet_cap = cal_ms, quiet_cap
        self.quiet = floor_min
        self.speaking = False
        self._above = 0.0
        self._quiet_since: float | None = None
        self._last_t: float | None = None
        self._begun = False
        self._t0: float | None = None
        self._win_sum = 0.0
        self._win_n = 0
        self._win_t = 0.0
        self._calib = math.inf

    def restart(self) -> None:
        """Forget a half-heard utterance (after Skippy spoke, or a new turn began) but keep what it learned about the room's quiet."""
        self.speaking, self._begun, self._above, self._quiet_since, self._last_t = False, False, 0.0, None, None

    def bar(self) -> float:
        return max(self.floor_min * 2.2, self.quiet * self.ratio)

    def push(self, level: float, t: float) -> str | None:
        dt = 0.0 if self._last_t is None else t - self._last_t
        self._last_t = t
        if self._t0 is None:
            self._t0, self._win_t = t, t
        if t - self._t0 < self.cal_ms:                                   # calibrating: the quietest ~100 ms window is the room
            self._win_sum += level
            self._win_n += 1
            if t - self._win_t >= 100:
                self._calib = min(self._calib, self._win_sum / self._win_n)
                self._win_sum, self._win_n, self._win_t = 0.0, 0, t
            return None
        if self._calib != math.inf:
            self.quiet = max(self.floor_min, min(self.quiet_cap, self._calib))
            self._calib = math.inf
        bar = self.bar()
        if not self.speaking:
            if level > bar:
                self._above += dt
                if self._above >= self.min_speech_ms:
                    self.speaking, self._begun, self._quiet_since = True, True, None
                    return "speech"
            else:
                self._above = 0.0
                self.quiet = max(self.floor_min, self.quiet * 0.95 + level * 0.05)      # learn how quiet this room is
            return None
        if level > bar * 0.7:                                            # still talking (a little hysteresis)
            self._quiet_since = None
            return None
        if self._quiet_since is None:
            self._quiet_since = t
        if t - self._quiet_since >= self.end_silence_ms:
            self.speaking = False
            return "end" if self._begun else None
        return None
