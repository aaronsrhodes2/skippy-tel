#!/usr/bin/env bash
# Keeps services/satellite/hub.py running (the SkippyHub scheduled task runs this at sign-in): Skippy in every room box in SATELLITE_ROOMS.
# Restarts it if it ever exits, 3 s doubling to 60 s. Log: state/hub.log.
# ONE copy only (2026-10-31: three of these, started by hand by different sessions, kept relaunching hubs that fought over the media
# server's port 10800 and cut Skippy off): a second one finds the first in state/hub-run.pid and exits.
cd "$(dirname "$0")/.." || exit 1
mkdir -p state
PIDFILE=state/hub-run.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "$(date '+%H:%M:%S') hub_run: another supervisor (pid $(cat "$PIDFILE")) already keeps the hub running; this one exits" >> state/hub.log
  exit 0
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT
delay=3
while true; do
  started=$(date +%s)
  services/satellite/.venv/Scripts/python -u services/satellite/hub.py >> state/hub.log 2>&1
  [ $(( $(date +%s) - started )) -gt 120 ] && delay=3
  sleep "$delay"
  delay=$(( delay * 2 > 60 ? 60 : delay * 2 ))
done
