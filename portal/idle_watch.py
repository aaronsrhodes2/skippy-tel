"""The idle watchdog as the personal dashboard's Watcher sees it: warnings when the games VM has been on too long.

The VM's watchdog (infra/gcp-games/idle_watchdog.py) stops it after `idle-minutes` with nobody playing, and after
`max-hours` whatever happens. If it has been on long enough that one of those should have fired, the watchdog is
not doing its job, and every hour costs money. This reads only what the portal already has: the VM's status (its
start time and metadata), and the latest skippy-diag snapshot, whose `WATCHDOG` and `PLAYERS` lines come from
`idle_watchdog.py report`.
Stdlib only.
"""
from __future__ import annotations

import re
from datetime import datetime

import diagnostics

DEFAULT_MAX_HOURS = 8.0
NOBODY_AFTER_MIN = 120           # up this long with nobody playing is worth a line
UNKNOWN_AFTER_MIN = 60           # the watchdog unable to tell for this long is too
GRACE_MIN = 60                   # past the backstop by this much means the backstop itself failed
CHECK_INTERVAL_MIN = 5           # the watchdog's timer period (idle_watchdog.INTERVAL_MIN): its clock reaching the limit
                                 # starts the sleep, which takes a while; one more check past it means it did not happen
STALE_CHECK_MIN = 15             # no check for this long (three periods) means the timer is not running it

_LINE = re.compile(r"^WATCHDOG (.+)$", re.M)
_PAIR = re.compile(r'(\w+)=("[^"]*"|\S+)')


def parse(snapshot: str) -> dict[str, str] | None:
    """The `WATCHDOG verdict=idle idle_min=25 limit=30 ...` line of a snapshot as a dict (None: there isn't one,
    e.g. a VM booted before the new watchdog, or a snapshot from before it was deployed)."""
    lines = _LINE.findall(snapshot or "")
    if not lines:
        return None
    return {k: v.strip('"') for k, v in _PAIR.findall(lines[-1])}


def max_hours(raw: str | None) -> float:
    try:
        value = float((raw or "").strip())
    except ValueError:
        return DEFAULT_MAX_HOURS
    return value if 0 <= value < 1e6 else DEFAULT_MAX_HOURS


def _hours(minutes: float) -> str:
    return f"{minutes / 60:.1f} h"


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(value or ""))
    except ValueError:
        return default


def _sleep_overdue(wd: dict[str, str], idle: int) -> str | None:
    """Should the VM have gone to sleep by now, given that nobody is playing? None: no, the watchdog is still counting
    down (the normal state for up to `limit` minutes after the last player leaves, however long the session was).
    Otherwise "" or a few words saying why it is overdue, for the end of the sentence.

    Two ways to be overdue, both read from the report's WATCHDOG line: its idle clock ran past the limit by more than
    a check (a sleep that began has failed), or the checks themselves are not running (`last_check_s`; the clock only
    moves when a check does, so a dead timer leaves it at 0 for ever: the very bug this Watcher exists to catch).
    No WATCHDOG line at all (a VM still on an older watchdog) has nothing to compare, so it stays as it was."""
    if not wd:
        return ""
    last = wd.get("last_check_s")
    if last == "never":
        return " and the watchdog has not run a check since boot"
    stale = _int(last, -1)
    if stale > STALE_CHECK_MIN * 60:
        return f" and the watchdog's last check was {stale // 60} min ago"
    limit = _int(wd.get("limit"), idle)
    if limit <= 0:
        limit = idle
    if _int(wd.get("idle_min")) >= limit + CHECK_INTERVAL_MIN:
        return ""
    return None


def nobody_playing(snapshot: str, games: list[str] | None = None) -> bool | None:
    """True: the snapshot says nobody is playing; False: someone is; None: it cannot say. The watchdog's own verdict
    counts every game; without it, only a PLAYERS line for every game that is running can say nobody is there."""
    counts = diagnostics.players_online(snapshot)
    if any(n > 0 for n in counts.values()):
        return False
    verdict = (parse(snapshot) or {}).get("verdict")
    if verdict == "active":
        return False
    if verdict == "idle":
        return True
    if games and all(g in counts for g in games):
        return True
    return None


def warnings(vm, snapshot: str, now: datetime | None = None) -> list[str]:
    """Watcher lines about a VM that is up longer than its watchdog should have allowed. Never raises."""
    try:
        status = vm.status()
        if status.status != "RUNNING":
            return []
        up = status.minutes_up(now)
        if up is None:
            return []
        idle = _int(status.idle_minutes, 30)
        ceiling = max_hours(getattr(status, "max_hours", None))
        out: list[str] = []
        if idle > 0 and ceiling > 0 and up > ceiling * 60 + GRACE_MIN:
            out.append(f"The server has been on for {_hours(up)}, over its {ceiling:g}-hour limit by "
                       f"{_hours(up - ceiling * 60)}: the idle watchdog should have stopped it and hasn't. "
                       "Check the watchdog block in the logs (Admin → Logs, or `ops logs`).")
        wd = parse(snapshot) or {}
        if up > NOBODY_AFTER_MIN and nobody_playing(snapshot, list(status.games)):
            overdue = _sleep_overdue(wd, idle)
            if idle <= 0:
                out.append(f"On for {_hours(up)} with nobody playing: auto-sleep is off (never sleep), "
                           "so it will run until someone stops it.")
            elif overdue is not None:
                out.append(f"On for {_hours(up)} with nobody playing: auto-sleep is set to {idle} min, "
                           f"so it should have gone to sleep{overdue}.")
        unknown = _int(wd.get("unknown_min"))
        if idle > 0 and unknown >= UNKNOWN_AFTER_MIN:
            out.append(f"The idle watchdog has not been able to tell whether anyone is playing for {unknown} min "
                       f"({wd.get('reason', 'no reason given')}); only the {ceiling:g}-hour limit will stop the server.")
        return out
    except Exception:  # noqa: BLE001 — the Watcher must never break the page
        return []
