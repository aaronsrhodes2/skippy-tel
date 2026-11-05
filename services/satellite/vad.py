"""Voice activity detection for the home listener: when does someone start talking, and when are they done?

A port of createVad() in portal/static/skippy-voice.js (same numbers, same tests' shape), fed one loudness reading (the RMS of a block of
microphone samples, 0..1) with a clock in milliseconds. It answers "speech" once (they began), "end" once (a pause after speaking) or None.
The bar for "speaking" follows the room: a multiple of the quiet level it learned in its first 400 ms (the quietest 100 ms window), never
below a floor. A USB microphone is quieter than a phone's after its automatic gain, so the floor is a setting (see ear.py: SATELLITE_FLOOR).

Two things the hub's version does that the browser's does not (the Captain, 2026-11-05: "Skippy hears his wake word well, but the following commands
I have to shout"). The wake word's spotter listens for a shape and fires on a soft voice, but the bar above is an absolute loudness, so a command spoken
the way the name was could stay under it. arm() is called when the name has just been said: until he begins, the bar for his first words drops to
what the name says about how loud he is from where he stands (never above the normal bar). And a dip between two syllables no longer throws away
the 160 ms already counted: a soft voice sits near the bar and flickers across it.
"""
from __future__ import annotations

import math

ARM_VOICE = 0.35                # an expected command starts at this share of how loud the name was
ARM_QUIET = 1.6                 # ... but never below this multiple of the room's quiet
ARM_FLOOR = 1.2                 # ... or of the floor
ARM_MS = 12000                  # arming lapses by itself after this long (a listening window is 8 s): a detector left armed in a noisy room could never learn that the room got louder


def rms(samples) -> float:
    """Loudness of a block: the root mean square of float samples in -1..1."""
    n = len(samples)
    if not n:
        return 0.0
    return math.sqrt(sum(float(x) * float(x) for x in samples) / n)


def loudest(levels, width: int = 3) -> float:
    """The loudest stretch of `width` blocks (150 ms at 50 ms a block) of loudness readings: how loud a voice was, not how loud its loudest click was."""
    levels = list(levels)
    if not levels:
        return 0.0
    if len(levels) <= width:
        return sum(levels) / len(levels)
    return max(sum(levels[i:i + width]) / width for i in range(len(levels) - width + 1))


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
        self._armed: float | None = None
        self._arm_t0: float | None = None

    def restart(self) -> None:
        """Forget a half-heard utterance (after Skippy spoke, or a new turn began) but keep what it learned about the room's quiet."""
        self.speaking, self._begun, self._above, self._quiet_since, self._last_t = False, False, 0.0, None, None
        self._armed = self._arm_t0 = None

    def arm(self, voice: float = 0.0) -> None:
        """A command is expected: lower the bar for its first words to ARM_VOICE of `voice` (how loud the name just was; 0 = not known), never below
        ARM_QUIET times the room's quiet or ARM_FLOOR times the floor, and never above the normal bar. It holds until restart() or ARM_MS has gone by."""
        low = max(self.floor_min * ARM_FLOOR, self.quiet * ARM_QUIET)
        self._armed = max(low, ARM_VOICE * voice) if voice > 0 else low
        self._arm_t0 = None                                              # the clock starts with the next reading

    def bar(self) -> float:
        return max(self.floor_min * 2.2, self.quiet * self.ratio)

    def start_bar(self) -> float:
        """The bar a voice has to clear to begin: the normal one, or the lower one while a command is expected."""
        bar = self.bar()
        return bar if self._armed is None else min(bar, self._armed)

    def push(self, level: float, t: float) -> str | None:
        dt = 0.0 if self._last_t is None else t - self._last_t
        self._last_t = t
        if self._armed is not None:                                      # armed for a window only: a stale arming would keep the quiet from ever rising again
            if self._arm_t0 is None:
                self._arm_t0 = t
            elif t - self._arm_t0 > ARM_MS:
                self._armed = self._arm_t0 = None
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
        bar = self.start_bar()
        if not self.speaking:
            if level > bar:
                self._above += dt
                if self._above >= self.min_speech_ms:
                    self.speaking, self._begun, self._quiet_since = True, True, None
                    return "speech"
            else:
                self._above = max(0.0, self._above - dt * 0.5)          # a dip between two syllables keeps half of what was counted
                learned = self.quiet * 0.95 + level * 0.05              # learn how quiet this room is ...
                if self._armed is not None:                             # ... but not upward while a command is expected: a soft voice below the bar would raise it
                    learned = min(self.quiet, learned)                  # (the Enclave ducking under his name still lowers it)
                self.quiet = max(self.floor_min, learned)
            return None
        if level > max(bar * 0.7, self.quiet * 1.2):                     # still talking (a little hysteresis; a steady noise just above the bar must not hold it open)
            self._quiet_since = None
            return None
        if self._quiet_since is None:
            self._quiet_since = t
        if t - self._quiet_since >= self.end_silence_ms:
            self.speaking = False
            return "end" if self._begun else None
        return None
