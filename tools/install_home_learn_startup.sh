#!/usr/bin/env bash
# Registers (or removes) the Windows scheduled task "SkippyHomeLearn": every night at 03:30 it runs tools/home_learn.sh (routines learned from the house log).
# Same installer as the hub's, with a daily time instead of a logon trigger:
#
#   bash tools/install_home_learn_startup.sh                 DRY RUN: shows the task and the exact schtasks command. Changes nothing.
#   bash tools/install_home_learn_startup.sh --apply         registers it
#   bash tools/install_home_learn_startup.sh --status        asks Windows about it (read-only)
#   bash tools/install_home_learn_startup.sh --remove --apply
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export STARTUP_TASK=SkippyHomeLearn
export STARTUP_SCRIPT=tools/home_learn.sh
export STARTUP_AT="${STARTUP_AT:-03:30}"
export STARTUP_RESTART=0
export STARTUP_DESC="Nightly: learns the household's routines from skippy-home/log, writes routines/ and proposals/, commits to the local skippy-home repo. Log: state/home-learn.log. Made by tools/install_home_learn_startup.sh."
export STARTUP_NOTE="runs tools/home_learn.sh once a night at ${STARTUP_AT}; logs to state\home-learn.log"
exec bash "$here/install_bridge_startup.sh" "$@"
