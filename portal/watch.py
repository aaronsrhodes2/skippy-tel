"""The watcher: early signs that "one game server at a time" is no longer enough,
or that a server is in trouble. Read by the personal dashboard.

  • blocked starts — someone asked for a game while a different one was running
    (recorded in VM metadata `portal-watch`, last 100; Terraform ignores it)
  • crashes — a game container restarting or exited, from the latest snapshot
  • pressure — memory and world disk, from the latest snapshot
  • the idle watchdog — the VM on longer than its session limit, or 2+ hours with nobody playing (idle_watch.py)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import idle_watch

KEY = "portal-watch"
KEEP = 100


def _load(vm) -> dict:
    try:
        data = json.loads(vm.get_metadata(KEY) or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — the watcher must never break a start
        return {}


def record_blocked(vm, who: str, wanted: list[str], running: list[str]) -> None:
    try:
        data = _load(vm)
        events = data.get("blocked_starts", [])
        events.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "who": who, "wanted": wanted, "running": running})
        data["blocked_starts"] = events[-KEEP:]
        vm.set_metadata(KEY, json.dumps(data))
    except Exception:  # noqa: BLE001
        pass


def blocked_since(vm, days: int = 7) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for e in _load(vm).get("blocked_starts", []):
        try:
            if datetime.fromisoformat(e["at"]) >= cutoff:
                out.append(e)
        except (KeyError, ValueError):
            continue
    return out


_PS_ROW = re.compile(r"^(\S+)\s+(Restarting|Exited)[^\n]*", re.M)


def container_problems(snapshot: str) -> list[str]:
    """Game containers that are crash-looping or stopped, from `docker ps -a` rows."""
    return [f"{m.group(1)} is {m.group(2).lower()} ({m.group(0)[len(m.group(1)):].strip()[:60]})"
            for m in _PS_ROW.finditer(snapshot)]


def warnings(vm, snapshot: str, resources: dict) -> list[str]:
    out = container_problems(snapshot)
    if resources.get("memory_pct", 0) > 85:
        out.append(f"Memory at {resources['memory_pct']}% — the next game may not fit alongside.")
    if resources.get("disk_pct", 0) > 80:
        out.append(f"World disk at {resources['disk_pct']}% full.")
    blocked = blocked_since(vm)
    if blocked:
        who = sorted({e.get("who", "?") for e in blocked})
        out.append(f"{len(blocked)} blocked start{'s' if len(blocked) != 1 else ''} this week "
                   f"(someone wanted a different game while one was running: {', '.join(who)}). "
                   "If this keeps happening, it's time for concurrent servers.")
    out += idle_watch.warnings(vm, snapshot)          # up longer than the idle watchdog should allow (idle_watch.py)
    return out
