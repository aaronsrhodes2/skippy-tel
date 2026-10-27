"""The request, restated after a compaction (the Captain's rule, 2026-10-25: fix the tool, agnostically).

When a long run fills its context, Claude Code compacts it: older turns become a short summary, and the Captain's own words go
with them. Measured on Skippy Core's coder (Shifting Prophesy, trial T04): three compactions, each cutting about 100K tokens
down to 8K to 22K, after which the agent described its task as "the trial base from turn 1, just a title screen" and polished
one sub-feature instead of doing what was asked. Haiku, on the same task, stayed on it.

This is a SessionStart hook for the "compact" source (workshop_engine.lane_settings): right after a compaction it reads the
session's transcript (which keeps everything; only the model's context is cut), finds the last message the Captain actually
sent (not a tool result, not a compaction summary, not a meta line), and hands it back to the agent word for word as added
context. Every run gets it, every project, both brains: it knows nothing about any task.

Prints Claude Code's SessionStart JSON, or nothing (any other source, no transcript, no request found). Never fails a run.
"""
from __future__ import annotations

import json
import sys

MAX_CHARS = 8000          # a long pasted request is cut at the end, with a note; the start says what the job is
HEADER = ("Your conversation was just compacted, so here is the Captain's request for this run again, word for word. "
          "It is still the job: finish it.\n\n")


def _text(content) -> str | None:
    """A user turn's own words: a plain string, or text blocks; None for a tool result or anything else."""
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content and all(isinstance(b, dict) for b in content):
        if any(b.get("type") == "tool_result" for b in content):
            return None
        texts = [b.get("text", "") for b in content if b.get("type") == "text"]
        return "\n".join(t for t in texts if t) or None
    return None


def last_request(lines) -> str | None:
    """The newest message the Captain sent, from a transcript's JSON lines."""
    found = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "user":
            continue
        if entry.get("isCompactSummary") or entry.get("isMeta") or entry.get("isVisibleInTranscriptOnly"):
            continue
        text = _text((entry.get("message") or {}).get("content"))
        if text and text.strip() and not text.lstrip().startswith(("<command-", "<local-command", "Caveat:")):
            found = text.strip()
    return found


def restated(event: dict) -> dict | None:
    if event.get("source") != "compact":
        return None
    path = event.get("transcript_path")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            request = last_request(f)
    except OSError:
        return None
    if not request:
        return None
    if len(request) > MAX_CHARS:
        request = request[:MAX_CHARS] + "\n\n[the rest of the request is longer than this restatement keeps]"
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": HEADER + request}}


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    out = restated(event if isinstance(event, dict) else {})
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
