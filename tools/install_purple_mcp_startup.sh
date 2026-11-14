#!/usr/bin/env bash
# Registers (or removes) the Windows scheduled task "SkippyPurpleMcp": at every sign-in it runs tools/purple_mcp_run.sh, which keeps the purple MCP door running.
#   bash tools/install_purple_mcp_startup.sh            DRY RUN      --apply  registers it      --status  asks Windows (read-only)      --remove --apply
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export STARTUP_TASK=SkippyPurpleMcp
export STARTUP_SCRIPT=tools/purple_mcp_run.sh
export STARTUP_DESC="Keeps the purple MCP door running (README: services/purple-mcp). Log: state/purple-mcp.log. Made by tools/install_purple_mcp_startup.sh."
export STARTUP_NOTE="the runner restarts it itself, and logs to state\purple-mcp.log"
exec bash "$here/install_bridge_startup.sh" "$@"
