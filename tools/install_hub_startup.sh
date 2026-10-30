#!/usr/bin/env bash
# Registers (or removes) the Windows scheduled task "SkippyHub": at every sign-in it runs tools/hub_run.sh, which keeps services/satellite/hub.py
# (Skippy in every room box) running. Same installer as the bridge's, pointed at the hub:
#
#   bash tools/install_hub_startup.sh                 DRY RUN: shows the task and the exact schtasks command. Changes nothing.
#   bash tools/install_hub_startup.sh --apply         registers it
#   bash tools/install_hub_startup.sh --status        asks Windows about it (read-only)
#   bash tools/install_hub_startup.sh --remove --apply
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export STARTUP_TASK=SkippyHub
export STARTUP_SCRIPT=tools/hub_run.sh
export STARTUP_DESC="Keeps the room hub (services/satellite/hub.py: Skippy in every room box) running: starts tools/hub_run.sh at logon. Log: state/hub.log. Made by tools/install_hub_startup.sh."
export STARTUP_NOTE="the runner restarts the hub itself, and logs to state\hub.log"
exec bash "$here/install_bridge_startup.sh" "$@"
