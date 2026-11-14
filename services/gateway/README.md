# Skippy gateway

One MCP address, `http://127.0.0.1:47863/mcp` (stateless Streamable HTTP, `GET /health` shows each door), in front of every door the house has.
It only routes: a lookup of tool name → door, no model. Started at sign-in by the `SkippyGateway` task (`tools/install_gateway_startup.sh`,
runner `tools/gateway_run.sh`, log `state/gateway.log`).

| Door | Where | What |
|---|---|---|
| purple | `127.0.0.1:47860` (services/purple-mcp) | the development agent: `purple_ask` / `purple_result` / `purple_cancel`; slow, works in jobs |
| house | `127.0.0.1:47861` (services/satellite/house_mcp.py) | lights, switches, music, speakers, Spotify, texts, vision moments |
| mentat | `skippy-core:7350` | physics: `ask`, `find_tools`, `call_tool` |

Listed tools are three on purpose (Mentat's 220 once cost a chat ~73k tokens): `find_tools(query?, backend?)`, `call_tool(name, arguments?)`,
`backends()`. Any real tool name also works directly in `tools/call`; `backend.name` picks one when two doors share a name; `GATEWAY_FULL=1`
lists everything. A dead door is just absent. Add doors with `GATEWAY_BACKENDS='{"name": {"url": "...", "key": "..."}}'`.

Off loopback it refuses to start without `GATEWAY_KEY` (`Authorization: Bearer`). The house door's rules hold through it (a lock or garage
door never completes from text). Port 47862 is NOT this: it is the Factorio bridge's voice door. Speech in/out is not a gateway tool yet:
the hub's ears and voice (passes, wake word, several voices) stay where they are.

Tests: `python -m pytest services/gateway/tests -q`.
