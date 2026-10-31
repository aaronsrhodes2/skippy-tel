#!/usr/bin/env bash
# Daily: retire Skippy's worn-out quips and write and voice new ones (tools/quip_refresh.py). Run by the scheduled task "Skippy quip refresh"
# on skippy-pc, like sigma-ground's distiller. Log: state/quip-refresh.log.
cd "$(dirname "$0")/.." || exit 1
mkdir -p state
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
services/satellite/.venv/Scripts/python -u tools/quip_refresh.py "$@" >> state/quip-refresh.log 2>&1
