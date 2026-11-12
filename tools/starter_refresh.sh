#!/usr/bin/env bash
# Daily: gather Skippy's conversation starters (breakthroughs in the Captain's interests, world news that touches him; tools/starter_refresh.py).
# Run by the scheduled task "Skippy starters refresh" on skippy-pc. Log: state/starter-refresh.log.
cd "$(dirname "$0")/.." || exit 1
mkdir -p state
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
services/satellite/.venv/Scripts/python -u tools/starter_refresh.py "$@" >> state/starter-refresh.log 2>&1
