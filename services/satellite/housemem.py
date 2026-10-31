"""The house log: what the room hub heard and did, one JSON line per event, appended to skippy-home/log/YYYY-MM-DD.jsonl.

Local to skippy-pc, append-only, small. It records the words heard after the name was said, the tools called and what came of it; never audio,
and never room talk the hub was not addressed in. A failure to write is swallowed: the house must keep answering with a full disk.
The learner (routines.py) reads these files; the workspace is the folder D:\\Aaron\\development\\skippy-home (SKIPPY_HOME_DIR overrides).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

ACTIONS = {"home_control": "home", "set_timer": "timer", "cancel_timers": "timer", "music": "music", "play_music": "music"}   # tool -> what it is
MAX_TEXT = 300


def home_dir(env: dict | None = None) -> Path:
    env = env if env is not None else os.environ
    return Path(env.get("SKIPPY_HOME_DIR") or Path(__file__).resolve().parents[3] / "skippy-home")


class HouseLog:
    def __init__(self, root: Path | str | None = None, clock=time.time):
        self.root = Path(root) if root else home_dir()
        self.clock = clock
        self._lock = threading.Lock()

    def event(self, kind: str, **fields) -> dict | None:
        t = time.localtime(self.clock())
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", t), "date": time.strftime("%Y-%m-%d", t), "weekday": time.strftime("%A", t),
               "time": time.strftime("%H:%M", t), "kind": kind}
        rec.update({k: (v[:MAX_TEXT] if isinstance(v, str) else v) for k, v in fields.items() if v is not None})
        try:
            path = self.root / "log" / f"{rec['date']}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return rec
        except OSError:
            return None

    def actions(self, room: str, calls: list[dict]) -> None:
        """One 'action' event per lights / timer / music tool call of a reply (a read-only question is only part of the command's tools)."""
        for c in calls:
            what = ACTIONS.get(c.get("tool", ""))
            if what is None:
                continue
            args = c.get("args") or {}
            self.event("action", room=room, what=what, tool=c["tool"], target=str(args.get("name") or args.get("what") or args.get("label") or "")[:80] or None,
                       act=str(args.get("action") or "") or None, value=args.get("value") if isinstance(args.get("value"), (int, float)) else args.get("seconds"),
                       outcome=c.get("outcome"))
