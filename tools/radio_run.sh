#!/usr/bin/env bash
# Keeps services/satellite/radio.py running (the SkippyRadio scheduled task runs this at sign-in): Bilby's house radio, the ONE station every
# speaker plays (the hubs are only its remotes, so a hub restart never touches the music). Restarts it if it ever exits, 3 s doubling to 60 s;
# it resumes where it was (state/radio/station.json). Log: state/radio.log. One copy only: a second finds the first in state/radio-run.pid.
cd "$(dirname "$0")/.." || exit 1
mkdir -p state
PIDFILE=state/radio-run.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "$(date '+%H:%M:%S') radio_run: another supervisor (pid $(cat "$PIDFILE")) already keeps the radio running; this one exits" >> state/radio.log
  exit 0
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT
delay=3
export PYTHONIOENCODING=utf-8:replace PYTHONUTF8=1
while true; do
  started=$(date +%s)
  services/satellite/.venv/Scripts/python -u services/satellite/radio.py >> state/radio.log 2>&1
  [ $(( $(date +%s) - started )) -gt 120 ] && delay=3
  sleep "$delay"
  delay=$(( delay * 2 > 60 ? 60 : delay * 2 ))
done
