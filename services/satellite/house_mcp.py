"""The house as an MCP server for the private Skippy (Claude Code on skippy-pc, both brains): the same three tools the voice listener has
(house.TOOLS: home_devices, home_state, home_control) plus the hubs' music tools (hub.MUSIC_TOOLS: music, play_music, speakers, dj_mode, spotify,
audiobook, sent to the house radio's API), over stdio. Claude starts it per run; it listens on no port.

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


def music_tools() -> list[dict]:
    """The hubs' music tools (hub.MUSIC_TOOLS: music, play_music, speakers, dj_mode, spotify, audiobook): the same ones the voice hub and the house
    chat have, so the private Skippy can start music on a room or both floors instead of answering that the pods are not media players (2026-11-11)."""
    try:
        from hub import MUSIC_TOOLS                                 # noqa: PLC0415
    except Exception:                                               # noqa: BLE001 — a venv without the hub's imports lists the lights and nothing else
        return []
    return MUSIC_TOOLS


def radio_tool(name: str, args: dict, url: str | None = None, timeout: float = 60.0) -> str:
    """One music tool through the house radio's API (radio.py `POST /tool`, 127.0.0.1 only); what the radio says back is the result."""
    import os                                                       # noqa: PLC0415
    import urllib.request                                           # noqa: PLC0415
    base = (url or os.environ.get("RADIO_URL") or f"http://127.0.0.1:{os.environ.get('RADIO_PORT', '47850')}").rstrip("/")
    req = urllib.request.Request(base + "/tool", data=json.dumps({"name": name, "args": args, "room": ""}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:     # noqa: S310 — 127.0.0.1
            return str(json.loads(r.read() or b"{}").get("said") or "Done.")
    except Exception as e:                                          # noqa: BLE001 — said, never raised
        return f"The house radio is not answering right now ({type(e).__name__})."


def tool_list() -> list[dict]:
    from house import TOOLS                                         # noqa: PLC0415
    return [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]} for t in [*TOOLS, *music_tools()]]


def handle(msg: dict, house_of, radio=radio_tool) -> dict | None:
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
        name = str(params.get("name") or "")
        if name in {t["name"] for t in music_tools()}:
            text = radio(name, args)                                # music needs no house at all: the radio plays it
        else:
            house = house_of()
            text = run_tool(house, name, args) if house else \
                "Nothing in the house is set up (no HomeKit pairings, no MATTER_URL, no GARAGE_ESPHOME, no Leviton login in the vault)."
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
