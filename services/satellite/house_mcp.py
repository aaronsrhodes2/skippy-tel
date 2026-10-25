"""The house as an MCP server for the private Skippy (Claude Code on skippy-pc, both brains): the same three tools the voice listener has
(house.TOOLS: home_devices, home_state, home_control), over stdio. Claude starts it per run; it listens on no port.

    workshop_engine.py hands every run `--mcp-config` naming this file and the satellite's venv (.venv: python -m venv .venv &&
    .venv/Scripts/pip install requests decora_wifi==1.5); `claude mcp add --scope user skippy-house -- <venv python> <this file>` does
    the same for the Captain's own Claude Code.

A text chat cannot prove he said yes out loud, so a lock or a garage door never completes here: `confirm` is dropped, and those ask him
to use his voice. With HASS_SENSITIVE unset (the default) they are not even listed.
"""
from __future__ import annotations

import json
import sys

PROTOCOL = "2025-03-26"


def tool_list() -> list[dict]:
    from house import TOOLS                                         # noqa: PLC0415
    return [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]} for t in TOOLS]


def handle(msg: dict, house_of) -> dict | None:
    """One JSON-RPC message in, its reply out (None for a notification). house_of() builds the house the first time it's needed."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "skippy-house", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        from house import run_tool                                  # noqa: PLC0415
        params = msg.get("params") or {}
        args = dict(params.get("arguments") or {})
        args.pop("confirm", None)                                   # never from text: only his voice confirms a lock
        house = house_of()
        text = run_tool(house, str(params.get("name") or ""), args) if house else \
            "Nothing in the house is set up (no HomeKit pairings, no MATTER_URL, no Leviton login in the vault)."
        result = {"content": [{"type": "text", "text": text}], "isError": False}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main(stdin=sys.stdin, stdout=sys.stdout) -> None:
    from homectl import build_house, load_env                       # noqa: PLC0415
    built: list = []

    def house_of():
        if not built:
            built.append(build_house(load_env()))
        return built[0]
    for line in stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle(msg, house_of) if isinstance(msg, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    main()
