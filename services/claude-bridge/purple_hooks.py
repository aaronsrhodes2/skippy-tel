"""The hook entries that make purple obey its three limits and stop looping, in one place, for every runner that launches Claude Code the way purple runs
(the engine's lane-settings.json, and tools/codebench/run.py's hooks.json: the benchmark measures purple as it really runs).

    import purple_hooks
    hooks = purple_hooks.entries()          # {"PreToolUse": [...], "PostToolUse": [...]}: merge each list into your settings' hooks

  * PreToolUse  matcher "*"                                   purple_guard.py   leak / spend / lie before every tool call (exit 2 refuses)
  * PostToolUse matcher "Bash|Edit|Write|MultiEdit"           loop_breaker.py   the same failure again and again: a step up the ladder, as context

The SDK engine runs the same two as callbacks (sdk_engine.guard_hook / loop_hook), not through these entries.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def entries(python: str | None = None) -> dict[str, list[dict]]:
    py = python or sys.executable
    return {
        "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": f'"{py}" "{HERE / "purple_guard.py"}"', "timeout": 60}]}],
        "PostToolUse": [{"matcher": "Bash|Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": f'"{py}" "{HERE / "loop_breaker.py"}"', "timeout": 10}]}],
    }
