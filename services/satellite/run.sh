#!/usr/bin/env bash
# Start the always-on Skippy home listener. First run: python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cd "$(dirname "$0")" && exec ./.venv/Scripts/python ear.py "$@"
