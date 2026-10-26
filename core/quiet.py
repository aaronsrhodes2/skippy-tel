"""The quiet gate: skippy-core's builders (deployer, CI, CD) keep off the box while the private Skippy is working on it.

The coder shares the GPU and CPU with everything else on skippy-core, and a build or a llama-swap reload in the middle of an agent
run makes the run slow (2026-10-25: a loop of failed Core builds and rollbacks ran through the first half hour of a trial).
`busy()` asks the bridge on skippy-pc whether a run is going; the callers defer, up to MAX_DEFER_S, then go anyway so a stuck
flag can't hold deploys forever. An unreachable bridge is "not busy" (skippy-pc off means no run)."""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

BRIDGE_HEALTH = os.environ.get("QUIET_BRIDGE_URL", "http://skippy-pc:9337/health")
MAX_DEFER_S = 4 * 3600


def busy(url: str | None = None, timeout: float = 5) -> bool:
    try:
        with urllib.request.urlopen(url or BRIDGE_HEALTH, timeout=timeout) as r:
            got = json.loads(r.read() or b"{}")
    except (OSError, ValueError):
        return False
    return bool(got.get("busy"))


def should_defer(state_file: Path, now: float | None = None, is_busy=None) -> bool:
    """True = hold off this tick. The first busy tick is stamped in state_file; after MAX_DEFER_S of continuous busy we go anyway."""
    now = time.time() if now is None else now
    if not (is_busy or busy)():
        state_file.unlink(missing_ok=True)
        return False
    try:
        since = float(state_file.read_text())
    except (OSError, ValueError):
        since = now
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(str(since))
    return now - since < MAX_DEFER_S
