"""The quiet gate: skippy-core's builders (deployer, CI, CD) keep off the box while the private Skippy is working on it.

The coder shares the GPU and CPU with everything else on skippy-core, and a build or a llama-swap reload in the middle of an agent
run makes the run slow (2026-10-25: a loop of failed Core builds and rollbacks ran through the first half hour of a trial).
`busy()` asks the bridge on skippy-pc whether a run is going; the callers defer, up to MAX_DEFER_S, then go anyway so a stuck
flag can't hold deploys forever. An unreachable bridge is "not busy" (skippy-pc off means no run)."""
from __future__ import annotations

import json
import os
import subprocess
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
    w = got.get("workshop")
    return bool((w if isinstance(w, dict) else got).get("busy"))      # the bridge nests it: {"workshop": {"busy": ...}}


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


# ── Mentat-only changes are exempt (the Captain, 2026-10-27: "approved changes pushed to mentat live ... full one-step CD ...
#    I don't care if it interrupts a prompt mentat is chewing on"). ──
# A change that can only touch the Mentat container does not disturb a private Skippy run (the coder's slot, llama-swap and the
# shim are not rebuilt or reloaded), so CI and the deployer let it through while the gate is closed. "Only Mentat" means every file
# that differs between the last commit this box handled and the new one is one of:
#   - under mentat/ (the service, its tests and its Dockerfile);
#   - core/cd/deploy.json with nothing changed but pins.SIGMA_GROUND_REF (the sigma-ground version Mentat serves);
#   - core/compose.core.yml with nothing changed outside the `mentat:` service block (comments and blank lines ignored).
# Anything else, an unreadable diff, or no base commit to compare with: not exempt, the gate applies as before.
MENTAT_DIR = "mentat/"
DEPLOY_JSON = "core/cd/deploy.json"
COMPOSE_CORE = "core/compose.core.yml"
MENTAT_PIN = "SIGMA_GROUND_REF"


def _compose_without(text: str, service: str = "mentat") -> list[str]:
    """The compose file's meaningful lines with one service's block (`  <service>:` to the next 2-space key) left out."""
    out, skipping = [], False
    for line in text.splitlines():
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= 2:
            skipping = indent == 2 and body == f"{service}:"
        if not skipping:
            out.append(line.rstrip())
    return out


def _json_without_pin(text: str):
    doc = json.loads(text)
    doc.get("pins", {}).pop(MENTAT_PIN, None)
    return doc


def mentat_only(changed: list[str], old, new) -> bool:
    """`changed`: the paths that differ. `old(path)` / `new(path)`: that file's text before and after (None = absent)."""
    for path in changed:
        if path.startswith(MENTAT_DIR):
            continue
        a, b = old(path), new(path)
        if a is None or b is None:
            return False
        try:
            if path == DEPLOY_JSON and _json_without_pin(a) == _json_without_pin(b):
                continue
            if path == COMPOSE_CORE and _compose_without(a) == _compose_without(b):
                continue
        except (ValueError, AttributeError):
            pass
        return False
    return True


def git_mentat_only(repo, base: str | None, commit: str) -> bool:
    """mentat_only() for two commits of a git repo (a working copy or a bare mirror). Any trouble reading it: False."""
    if not base or not commit:
        return False

    def git(*args: str) -> str | None:
        r = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(repo), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        return r.stdout if r.returncode == 0 else None

    names = git("diff", "--name-only", "-z", base, commit)
    if names is None:
        return False
    return mentat_only([n for n in names.split("\0") if n], lambda p: git("show", f"{base}:{p}"), lambda p: git("show", f"{commit}:{p}"))
