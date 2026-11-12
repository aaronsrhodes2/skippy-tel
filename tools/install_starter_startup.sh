#!/usr/bin/env bash
# Registers (or removes) the Windows scheduled task "SkippyStarter": at every sign-in it runs tools/starter_run.sh, which keeps tools/skippy_starter.py (the always-on
# service manager the portal can ask to start or stop the whole stack) running. Same installer as the bridge's, pointed at the starter:
#
#   bash tools/install_starter_startup.sh                 DRY RUN: shows the task and the exact schtasks command. Changes nothing.
#   bash tools/install_starter_startup.sh --apply         registers it
#   bash tools/install_starter_startup.sh --status        asks Windows about it (read-only)
#   bash tools/install_starter_startup.sh --remove --apply
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export STARTUP_TASK=SkippyStarter
export STARTUP_SCRIPT=tools/starter_run.sh
export STARTUP_DESC="Keeps the starter (the always-on service manager of skippy-pc, which the portal can ask to start or stop the stack) running: starts tools/starter_run.sh at logon. Log: state/starter.log. Made by tools/install_starter_startup.sh."
export STARTUP_NOTE="the runner restarts the starter itself, and logs to state\starter.log"
exec bash "$here/install_bridge_startup.sh" "$@"
