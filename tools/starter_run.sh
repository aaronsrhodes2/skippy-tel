#!/usr/bin/env bash
# Keeps tools/skippy_starter.py running (the SkippyStarter scheduled task runs this at sign-in): restarts it if it ever exits, 3 s doubling to 60 s.
# A second copy of the starter exits at once (it holds a loopback lock), so this is safe to run twice. Log: state/starter.log.
cd "$(dirname "$0")/.." || exit 1
mkdir -p state
delay=3
while true; do
  started=$(date +%s)
  python tools/skippy_starter.py >> state/starter.log 2>&1
  [ $(( $(date +%s) - started )) -gt 120 ] && delay=3
  sleep "$delay"
  delay=$(( delay * 2 > 60 ? 60 : delay * 2 ))
done
