#!/usr/bin/env bash
# Registers (or removes) the Windows scheduled task "SkippyGateway": at every sign-in it runs tools/gateway_run.sh, which keeps the Skippy gateway running.
#   bash tools/install_gateway_startup.sh            DRY RUN      --apply  registers it      --status  asks Windows (read-only)      --remove --apply
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export STARTUP_TASK=SkippyGateway
export STARTUP_SCRIPT=tools/gateway_run.sh
export STARTUP_DESC="Keeps the Skippy gateway running (README: services/gateway). Log: state/gateway.log. Made by tools/install_gateway_startup.sh."
export STARTUP_NOTE="the runner restarts it itself, and logs to state\gateway.log"
exec bash "$here/install_bridge_startup.sh" "$@"
