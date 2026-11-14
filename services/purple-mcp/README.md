# Purple Skippy and the house, as MCP servers

Two MCP doors, so a game mod, a hub or any model can use the Skippy estate without knowing its insides. Both speak MCP Streamable HTTP
(`POST /mcp`, stateless, JSON replies; `GET /health`), bind **127.0.0.1** by default, and refuse a wider bind without a key.

| Door | Port | Run by | Tools |
|---|---|---|---|
| Purple Skippy (development) | 47860 | `tools/purple_mcp_run.sh` (task `SkippyPurpleMcp`) | `purple_ask`, `purple_result`, `purple_cancel` over the claude-bridge (the home token stays in the server) |
| The house | 47861 | `tools/house_mcp_run.sh` (task `SkippyHouseMcp`) | `home_devices`, `home_state`, `home_control`, `music`, `play_music`, `speakers`, `dj_mode`, `spotify`, `audiobook` |

- **Key:** set `PURPLE_MCP_KEY` / `HOUSE_MCP_KEY` (and `*_HOST`) in the vault `.env` to listen beyond this PC; callers send `Authorization: Bearer <key>`.
- **Stdio too:** `python services/purple-mcp/purple_mcp.py` and `services/satellite/.venv/Scripts/python services/satellite/house_mcp.py` (no flag) for Claude Desktop / Claude Code (`claude mcp add`).
- **Purple uses the house door itself:** a run's `skippy-house` server is the shared HTTP one while `:47861/health` answers (one house, one set of HomeKit/Matter links), else its own stdio copy (`workshop_engine.house_http_url`). Tool names (`mcp__skippy-house__*`) are unchanged.
- **The house never confirms a lock or garage from text** (only his voice does), on every transport.
- **A Factorio mod cannot call HTTP** (Lua is sandboxed): put a small external process beside the game (RCON or `script-output` files) and let it call these doors.
- Tests: `services/purple-mcp/tests`, `services/satellite/tests/test_sat_house_mcp.py`, `services/claude-bridge/tests/test_house_mcp_shared.py`.
