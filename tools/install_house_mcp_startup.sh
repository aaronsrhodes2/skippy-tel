#!/usr/bin/env bash
# Registers (or removes) the Windows scheduled task "SkippyHouseMcp": at every sign-in it runs tools/house_mcp_run.sh, which keeps the house MCP door running.
#   bash tools/install_house_mcp_startup.sh            DRY RUN      --apply  registers it      --status  asks Windows (read-only)      --remove --apply
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export STARTUP_TASK=SkippyHouseMcp
export STARTUP_SCRIPT=tools/house_mcp_run.sh
export STARTUP_DESC="Keeps the house MCP door running (README: services/purple-mcp). Log: state/house-mcp.log. Made by tools/install_house_mcp_startup.sh."
export STARTUP_NOTE="the runner restarts it itself, and logs to state\house-mcp.log"
exec bash "$here/install_bridge_startup.sh" "$@"
