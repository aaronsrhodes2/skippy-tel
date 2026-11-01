#!/usr/bin/env bash
# What cage runs on the bedroom TV (core/install/couch.sh): skippy-pc's Desktop over Moonlight, full screen, for good. When the stream
# ends (skippy-pc asleep or rebooting, a quit from the controller) it starts again; while skippy-pc doesn't answer it waits, gently.
# COUCH_KBPS: 80 Mbit/s on gigabit. (A 100 Mbit switch in the bedroom run dropped frames at 80 until it was bypassed, 2026-11-01: use 40000 behind one.)
set -uo pipefail
HOST="${COUCH_HOST:-192.168.0.164}"
APP="${COUCH_APP:-Desktop}"
LOG="${HOME:-/home/couch}/moonlight.log"                           # what Moonlight says (cage keeps it off the journal)
wait_s=3
while true; do
  started=$(date +%s)
  # the login session hands down an ambient capability (CAP_WAKE_ALARM) and Flatpak's sandbox refuses to start with any: drop them
  setpriv --inh-caps=-all --ambient-caps=-all flatpak run com.moonlight_stream.Moonlight stream "$HOST" "$APP" \
    --4K --fps 60 --bitrate "${COUCH_KBPS:-80000}" --video-codec HEVC --video-decoder hardware --display-mode fullscreen --no-audio-on-host --keep-awake     >>"$LOG" 2>&1
  tail -n 400 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"           # the last few sessions only
  if (( $(date +%s) - started > 60 )); then wait_s=3; else wait_s=$(( wait_s * 2 > 30 ? 30 : wait_s * 2 )); fi
  sleep "$wait_s"
done
