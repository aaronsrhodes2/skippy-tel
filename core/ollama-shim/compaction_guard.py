"""The compaction guard: a summary request may not be answered with a tool call (the Captain, 2026-10-26).

When a long Claude Code run fills its context, Claude Code asks the model to summarize the conversation. That request still
declares every tool (so it can share the prompt cache) and ends with "CRITICAL: Respond with TEXT ONLY. Do NOT call any
tools ... Tool calls will be REJECTED". Claude models obey. The Core coder (Qwen3.6-35B-A3B), after hundreds of tool calls in
its history, often answers with its habit instead: one sentence ("Let me fix the lock recording:") and a tool call. Claude
Code rejects the call and keeps the sentence as the summary. Measured on Shifting Prophesy's purple trials T04 to T06: 4 of
8 compactions came out as a 122 to 287 character stub, and an agent that gets one has lost its task and its work so far.

The fix is at the gateway, for every project and every agent that runs on the coder: recognise the summary request and take
the tools away from it. With nothing to call, the model can only write the summary. Any other request passes untouched,
byte for byte.

    guard_request(raw)  -> (raw', record)   the request body to forward, and what the guard saw (one log line)
    GuardLog(path)      .append(record)  .tail(since_ms)  a JSON-lines file, rotated, never holding prompt text

Nothing here raises: a body that isn't JSON, or isn't a /v1/messages request, is forwarded as it came.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

# Claude Code's own wording, from the installed binary (2.1.285). Its three summary prompts (whole conversation, the recent
# part, a continuing session) all open with the first line and close with the second, so either one finds them; a version
# that rewords both stops being recognised and the log shows it (summarization false, a stub in the run's record).
MARKERS = (
    "Respond with TEXT ONLY. Do NOT call any tools",
    "REMINDER: Do NOT call any tools. Respond with plain text only",
)
MAX_LOG_BYTES = 5 * 1024 * 1024        # one file; the previous one is kept beside it as .1


def _user_text(message: dict) -> str:
    """The words the user turn itself carries (not a tool result: a file that quotes the marker is not a summary request)."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def summary_marker(body: dict) -> str | None:
    """The marker that makes this a summary request: the LAST message is the user's and carries it. None otherwise."""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or not isinstance(messages[-1], dict):
        return None
    last = messages[-1]
    if last.get("role") != "user":
        return None
    text = _user_text(last)
    return next((m for m in MARKERS if m in text), None)


def guard_request(raw: bytes) -> tuple[bytes, dict]:
    """(the body to forward, a record of what was seen). A summary request that declares tools is forwarded without
    `tools` and `tool_choice`; anything else is returned as it came."""
    record: dict = {"ts": int(time.time() * 1000), "bytes": len(raw or b"")}
    try:
        body = json.loads(raw)
    except (TypeError, ValueError):
        record["error"] = "not json"
        return raw, record
    if not isinstance(body, dict):
        record["error"] = "not an object"
        return raw, record
    tools = body.get("tools")
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    record.update(model=body.get("model"), stream=bool(body.get("stream")), messages=len(messages),
                  tools=len(tools) if isinstance(tools, list) else 0)
    marker = summary_marker(body)
    record["summarization"] = marker is not None
    if marker is None:
        return raw, record
    record["marker"] = marker[:30]
    record["tool_uses_in_history"] = sum(1 for m in messages if isinstance(m, dict) and isinstance(m.get("content"), list)
                                         for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use")
    if not tools and "tool_choice" not in body:
        record["stripped"] = 0
        return raw, record
    record["stripped"] = len(tools) if isinstance(tools, list) else 0
    body.pop("tools", None)
    body.pop("tool_choice", None)
    return json.dumps(body, separators=(",", ":")).encode("utf-8"), record


class GuardLog:
    """The guard's record, one JSON object per line, no prompt text, no keys. Rotates at MAX_LOG_BYTES (one old file kept)."""

    def __init__(self, path: str | os.PathLike, max_bytes: int = MAX_LOG_BYTES):
        self.path, self.max_bytes = Path(path), max_bytes
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    self.path.replace(self.path.with_name(self.path.name + ".1"))
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError:
            pass                                    # a full disk must not stop a request

    def tail(self, since_ms: int = 0) -> list[dict]:
        out: list[dict] = []
        for p in (self.path.with_name(self.path.name + ".1"), self.path):
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("ts", 0) >= since_ms:
                    out.append(r)
        return out


def summarize(records: list[dict]) -> dict:
    """What a window of the log says: requests seen, summary requests, tools taken away, summary requests that had none to take."""
    s = [r for r in records if r.get("summarization")]
    return {"requests": len(records), "summary_requests": len(s), "stripped": sum(r.get("stripped", 0) for r in s),
            "summary_requests_without_tools": sum(1 for r in s if not r.get("stripped"))}
