"""The loop breaker: the same thing failing again and again is the stuck state that wastes a night (the Captain, 2026-11-13: purple is slow and he cannot
check in, so a run must never spend hours repeating one failure). After each tool call (a PostToolUse hook, or a callback in the SDK engine) the same
failing call is counted; at 3, 5, 7 and 10 failures in a row the run is handed one more step up a ladder as extra context, never a refusal:

  3  stop retrying: read the whole error, state a hypothesis, test it with one small experiment
  5  research: web_search the exact error, find a working example, read the source of the thing that fails
  7  change the approach: another tool or library, or build a small tool for it (rule 2: its own tools, no paid ones)
  10 split the goal: finish everything else, close this part as blocked, and report it precisely (what was tried, what the error says)

A success of the same call, or a different call succeeding in its place, resets the count. A call is identified by the tool and its command (or file).
State for the CLI hook lives in the temp folder per session; the SDK engine keeps it in memory.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile

LADDER = {
    3: "The same call has now failed 3 times in a row. Stop retrying it unchanged: read the whole error, write down one hypothesis for the cause, and test it with one small experiment before the next attempt.",
    5: "5 failures of the same call. Research it: web_search the exact error, find a working example, read the source of the thing that fails. Do not repeat the same attempt again.",
    7: "7 failures. Change the approach: another tool or library, a different route to the same goal, or build a small tool that does the job (your own tools only, nothing paid).",
    10: "10 failures. Split the goal: finish everything else, close this part as blocked, and report it precisely (what you tried, what the error says, what you would need). Do not keep spending the run on it.",
}
_FAIL = re.compile(r"exit code[: ]+[1-9]|traceback \(most recent|\bfailed\b|command not found|no such file|cannot find|\berror:|\bexception\b|not recognized as|permission denied|timed out|refused", re.I)
_OK_HINT = re.compile(r"\b0 failed\b|\bpassed\b.*\b0 errors?\b", re.I)


def signature(tool: str, args: dict) -> str:
    if tool == "Bash":
        key = re.sub(r"\s+", " ", str(args.get("command") or "")).strip()[:160]
    elif tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        key = str(args.get("file_path") or args.get("notebook_path") or "")
    else:
        key = json.dumps(args, sort_keys=True, default=str)[:100]
    return f"{tool}:{key}"


def failed(tool: str, response, is_error: bool = False) -> bool:
    if is_error:
        return True
    text = response if isinstance(response, str) else json.dumps(response, default=str)
    text = text[:4000]
    if tool == "Bash":
        return bool(_FAIL.search(text)) and not _OK_HINT.search(text)
    return False


class LoopBreaker:
    def __init__(self, counts: dict | None = None):
        self.counts = counts if counts is not None else {}

    def observe(self, tool: str, args: dict, response, is_error: bool = False) -> str:
        """The step up the ladder this call earns, or ''."""
        sig = signature(tool, args if isinstance(args, dict) else {})
        if not failed(tool, response, is_error):
            self.counts.pop(sig, None)                  # a success of this call resets it; other calls' counts are left alone
            return ""
        self.counts[sig] = self.counts.get(sig, 0) + 1
        return LADDER.get(self.counts[sig], "")


def _state_path(session: str) -> str:
    return os.path.join(tempfile.gettempdir(), "skippy-loop-" + hashlib.sha1(session.encode()).hexdigest()[:16] + ".json")


def main() -> int:
    """The CLI hook: PostToolUse. Its counts are kept per session in a temp file."""
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    path = _state_path(str(event.get("session_id") or event.get("transcript_path") or "none"))
    try:
        with open(path, encoding="utf-8") as f:
            counts = json.load(f)
    except (OSError, ValueError):
        counts = {}
    br = LoopBreaker(counts)
    msg = br.observe(str(event.get("tool_name") or ""), event.get("tool_input") or {}, event.get("tool_response"))
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(br.counts, f)
    except OSError:
        pass
    if msg:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
