#!/usr/bin/env bash
# Keeps services/satellite/hub.py running (the SkippyHub scheduled task runs this at sign-in): Skippy in every room box in SATELLITE_ROOMS.
# Restarts it if it ever exits, 3 s doubling to 60 s. A second copy can't bind the media server's port (10800) and exits, so this is safe to
# run twice. Log: state/hub.log.
cd "$(dirname "$0")/.." || exit 1
mkdir -p state
delay=3
while true; do
  started=$(date +%s)
  services/satellite/.venv/Scripts/python -u services/satellite/hub.py >> state/hub.log 2>&1
  [ $(( $(date +%s) - started )) -gt 120 ] && delay=3
  sleep "$delay"
  delay=$(( delay * 2 > 60 ? 60 : delay * 2 ))
done
