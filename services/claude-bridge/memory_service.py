"""Skippy Memory on skippy-pc, run by the bridge: a background import every SYNC_EVERY_S (memory/sync.py) and the two
reads the portal's "Continue with Skippy" needs. The bridge is the right home for it: it already runs on Windows for good
(the supervisor restarts it), sees the Drive folder (G:) and ~/.claude, and every door of it wants the home token.

A pass that fails (Drive not mounted yet at logon, a broken export) is logged and tried again next time; it never stops
the bridge. Reads open the archive fresh each time, so they always see the last finished pass.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the SkippyTel checkout: the `memory` package lives at its top
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory import sync  # noqa: E402
from memory.store import Archive  # noqa: E402

KEY = re.compile(r"[a-z-]{2,20}:[A-Za-z0-9_.-]{1,80}")
DEVICE = os.environ.get("SKIPPY_MEMORY_DEVICE", "skippy-pc")
FIRST_DELAY_S = 60                  # let Drive for Desktop mount after a logon before the first pass


class MemoryService:
    def __init__(self, root: Path | None = None, claude_home: Path | None = None, every_s: float = sync.SYNC_EVERY_S):
        self.root = root or sync.default_root()
        self.claude_home = claude_home or Path.home() / ".claude" / "projects"
        self.every_s = every_s
        self.lock = threading.Lock()
        self.last_error = ""

    def available(self) -> bool:
        return self.root.parent.is_dir()            # "My Drive" is there: Drive for Desktop is running

    def run_once(self) -> dict | None:
        if not self.available():
            self.last_error = f"{self.root.parent} isn't there (is Google Drive for Desktop running?)"
            return None
        with self.lock:
            try:
                report = sync.sync(Archive(self.root), self.claude_home, DEVICE)
                self.last_error = ""
                return report
            except Exception as exc:  # noqa: BLE001 — logged, retried next pass
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                traceback.print_exc()
                return None

    def _loop(self) -> None:
        time.sleep(FIRST_DELAY_S)
        while True:
            report = self.run_once()
            if report and (report["sessions"] or report["exports"]):
                print(f"[memory] {report['sessions']} session(s); exports {report['exports']}", file=sys.stderr)
            elif self.last_error:
                print(f"[memory] pass failed: {self.last_error}", file=sys.stderr)
            time.sleep(self.every_s)

    def start(self) -> None:
        threading.Thread(target=self._loop, name="skippy-memory", daemon=True).start()

    def listing(self) -> tuple[int, dict]:
        if not (self.root / ".skippy").is_dir():
            return 503, {"error": "Skippy Memory isn't set up yet (its first import hasn't run)."}
        body = sync.listing(Archive(self.root))
        body["last_error"] = self.last_error
        return 200, body

    def read(self, key: str) -> tuple[int, dict]:
        if not KEY.fullmatch(key or ""):
            return 400, {"error": "bad key"}
        found = sync.read(Archive(self.root), key)
        return (200, found) if found else (404, {"error": "unknown_conversation"})
