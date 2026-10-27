"""purple Skippy's own tools as one MCP server (Claude Code on skippy-pc, Core brain), over stdio. Claude starts it per run; it listens on no port.

    docs/popup-inventory.md, section 5 and 6: the tools Claude Code gets from Anthropic's servers (WebSearch) or that a local model has no senses
    for (PDFs) do not exist for purple; they are built here, one tool each, each backed by something that runs at home. Standard library only, so any
    Python on skippy-pc runs it (no venv).

        python services/purple-tools/server.py        # speaks JSON-RPC (one message a line) on stdin and stdout

    The bridge hands every run `--mcp-config` naming this file (workshop_engine.py, like services/satellite/house_mcp.py); the config text is
    `mcp_config()` below. A tool never raises: a failure comes back as the tool's own words with isError, so the model can read why and try again.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tools  # noqa: E402

PROTOCOL = "2025-03-26"


def mcp_config(python: str | None = None) -> dict:
    """What `--mcp-config` should hold to start this server (the bridge writes it per run)."""
    return {"mcpServers": {"purple-tools": {"type": "stdio", "command": python or sys.executable, "args": [str(HERE / "server.py")]}}}


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, its reply out (None for a notification)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "purple-tools", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": tools.tool_list()}
    elif method == "tools/call":
        params = msg.get("params") or {}
        text, is_error = tools.call(str(params.get("name") or ""), params.get("arguments") if isinstance(params.get("arguments"), dict) else {})
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main(stdin=sys.stdin, stdout=sys.stdout) -> None:
    for line in stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        try:
            reply = handle(msg)
        except Exception as exc:  # noqa: BLE001 — the server must outlive any one request
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": f"{type(exc).__name__}"}}
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


if __name__ == "__main__":
    main()
