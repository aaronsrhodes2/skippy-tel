#!/usr/bin/env bash
# Registers (or removes) the Windows scheduled task "SkippyModelsBaseline" and, with --apply, STARTS IT RIGHT AWAY
# (the first download needn't wait for midnight; the task ignores a second start while one runs). Every night at 00:00 it runs
# tools/models_baseline_run.sh, which downloads whatever of tools/models_baseline.json is not archived yet into D:\models
# (it wakes the PC for it; a finished archive makes the nightly run a quick no-op). Same installer as the bridge's:
#
#   bash tools/install_models_baseline_task.sh              DRY RUN: shows the task. Changes nothing.
#   bash tools/install_models_baseline_task.sh --apply      registers it
#   bash tools/install_models_baseline_task.sh --status     asks Windows about it (read-only)
#   bash tools/install_models_baseline_task.sh --remove --apply
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export STARTUP_TASK=SkippyModelsBaseline
export STARTUP_SCRIPT=tools/models_baseline_run.sh
export STARTUP_AT=${STARTUP_AT:-00:00}
export STARTUP_RESTART=0
export STARTUP_WAKE=1
export STARTUP_RUN_NOW=${STARTUP_RUN_NOW:-1}
export STARTUP_DESC="Downloads the baseline model archive (tools/models_baseline.json) into D:\\models, resuming each night until it is complete. Log: state/models-baseline.log. Made by tools/install_models_baseline_task.sh."
export STARTUP_NOTE="it logs to state\\models-baseline.log; progress: python tools/models_baseline.py plan"
exec bash "$here/install_bridge_startup.sh" "$@"
