#!/usr/bin/env bash
# Registers (or removes) the Windows scheduled task "SkippyRadio": at every sign-in it runs tools/radio_run.sh, which keeps
# services/satellite/radio.py (Bilby's house radio) running. Same installer as the hub's and the bridge's:
#
#   bash tools/install_radio_startup.sh                 DRY RUN: shows the task and the exact schtasks command. Changes nothing.
#   bash tools/install_radio_startup.sh --apply         registers it
#   bash tools/install_radio_startup.sh --status        asks Windows about it (read-only)
#   bash tools/install_radio_startup.sh --remove --apply
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export STARTUP_TASK=SkippyRadio
export STARTUP_SCRIPT=tools/radio_run.sh
export STARTUP_DESC="Keeps Bilby's house radio (services/satellite/radio.py: the one station every speaker plays) running: starts tools/radio_run.sh at logon. Log: state/radio.log. Made by tools/install_radio_startup.sh."
export STARTUP_NOTE="the runner restarts the radio itself, and logs to state\radio.log"
exec bash "$here/install_bridge_startup.sh" "$@"
