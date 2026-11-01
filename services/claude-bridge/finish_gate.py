"""The finish line for a CLI-engine Workshop run (the Captain, 2026-10-31: "build it, deploy it, it asks me to refresh browser and
see it deployed, and it should have checked").

Claude Code runs this as the Stop hook of every Workshop run (lane-settings.json). The bridge writes the run's starting point to
the file named by SKIPPY_FINISH_STATE before it starts claude: the folder, the paths already dirty then (earlier work is not this
run's), HEAD then, and how many times it has been sent back. When the agent tries to end its turn, run_state.loose_ends says what
is unfinished (uncommitted, unpushed, a portal change not live yet); if anything is, the turn is blocked with those facts, up to
run_state.GATE_LIMIT times, so a run can never end with "everything's done" and the work still in the folder. The SDK engine
has the same gate as a callback (sdk_engine.StopGate).

No state file, or an unreadable one: no opinion (exit 0, nothing printed).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_state  # noqa: E402


def main() -> int:
    path = os.environ.get("SKIPPY_FINISH_STATE", "")
    try:
        sys.stdin.read()
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return 0
    sent = int(state.get("sent_back") or 0)
    if sent >= run_state.GATE_LIMIT:
        return 0
    reason = run_state.loose_ends(str(state.get("clone") or ""), set(state.get("baseline") or []), str(state.get("head") or ""))
    if not reason:
        return 0
    state["sent_back"] = sent + 1
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        return 0                                   # can't count: never risk blocking forever
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
