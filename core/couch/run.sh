#!/usr/bin/env bash
# What cage runs on the bedroom TV (core/install/couch.sh): skippy-pc's Desktop over Moonlight, full screen, for good. When the stream
# ends (skippy-pc asleep or rebooting, a quit from the controller) it starts again; while skippy-pc doesn't answer it waits, gently.
# COUCH_KBPS: 80 Mbit/s on gigabit. (A 100 Mbit switch in the bedroom run dropped frames at 80 until it was bypassed, 2026-11-01: use 40000 behind one.)
# 1080p and YUV 4:4:4 (2026-11-01, the Captain: "static text looks like it is being anti-aliased away"): skippy-pc's desktop is 1920x1080, so
# --4K only stretched it on the PC, and 4:2:0 halves the colour around every letter; the TV does the one upscale. Decoded in SOFTWARE:
# the iGPU's VCN can't decode HEVC 4:4:4 (with hardware Moonlight quietly fell back to 4:2:0); 1080p60 4:4:4 costs the CPU ~3%.
# The stream is SDR (no --hdr): with HDR on, the picture was washed out and too bright, and turning HDR off fixed it (2026-11-01).
# A Moonlight that hangs instead of ending is ended by watch() (2026-11-03: skippy-core ran out of memory at 17:37, the OOM killer took the
# audio servers, the control stream timed out and Moonlight's teardown stuck in "Stopping audio stream" for good. This loop waited on a process
# that never ended: a black TV for 4.5 hours, skippy-pc still sending frames into "Video decode unit queue overflow"). Each round also starts
# the audio services first: an OOM kill leaves them "failed" and Moonlight would find no sound server.
set -uo pipefail
HOST="${COUCH_HOST:-192.168.0.164}"
APP="${COUCH_APP:-Desktop}"
FLATPAK_ID=com.moonlight_stream.Moonlight
LOG="${HOME:-/home/couch}/moonlight.log"                           # what Moonlight says (cage keeps it off the journal)
WATCH_S="${COUCH_WATCH_S:-5}"                                      # how often watch() looks
HANG_S="${COUCH_HANG_S:-20}"                                       # how long a teardown may take (a clean one is under a second)

new_lines() { tail -c +"$(( $1 + 1 ))" "$LOG" 2>/dev/null; }       # new_lines <log size when Moonlight started>: only what THIS Moonlight said

watch() {  # watch <pid> <log size when it started>: ends a Moonlight that is stuck: a teardown that hangs, or a decoder queue that floods
  local pid="$1" from="$2" torn=0 why=""
  while kill -0 "$pid" 2>/dev/null; do
    sleep "$WATCH_S"
    why=""
    if (( torn == 0 )) && (( $(new_lines "$from" | grep -c "Stopping .* stream") > 0 )); then torn=$(date +%s); fi
    if (( torn > 0 )) && (( $(date +%s) - torn >= HANG_S )); then why="its teardown hung"
    elif (( $(new_lines "$from" | tail -n 200 | grep -c "queue overflow") >= 40 )); then why="its decoder queue is flooded"; fi
    [[ -n "$why" ]] || continue
    echo "$(date +%T) - couch: ending Moonlight, $why" >>"$LOG"
    logger -t couch "ending Moonlight, $why" 2>/dev/null
    setpriv --inh-caps=-all --ambient-caps=-all flatpak kill "$FLATPAK_ID" >/dev/null 2>&1; kill -9 "$pid" 2>/dev/null
    return
  done
}

wait_s=3
while true; do
  [[ -f "$LOG" ]] && { tail -n 400 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; }   # the last few sessions only (a killed stream never got to trim)
  timeout 10 systemctl --user reset-failed pipewire.service pipewire-pulse.service wireplumber.service 2>/dev/null
  timeout 10 systemctl --user start pipewire.service pipewire-pulse.service wireplumber.service 2>/dev/null
  started=$(date +%s)
  from=$(stat -c %s "$LOG" 2>/dev/null || echo 0)
  # the login session hands down an ambient capability (CAP_WAKE_ALARM) and Flatpak's sandbox refuses to start with any: drop them
  setpriv --inh-caps=-all --ambient-caps=-all flatpak run "$FLATPAK_ID" stream "$HOST" "$APP" \
    --1080 --yuv444 --fps 60 --bitrate "${COUCH_KBPS:-80000}" --video-codec HEVC --video-decoder software --display-mode fullscreen --no-audio-on-host --keep-awake     >>"$LOG" 2>&1 &
  moonlight=$!
  watch "$moonlight" "$from" &
  watcher=$!
  wait "$moonlight"
  kill "$watcher" 2>/dev/null
  if (( $(date +%s) - started > 60 )); then wait_s=3; else wait_s=$(( wait_s * 2 > 30 ? 30 : wait_s * 2 )); fi
  sleep "$wait_s"
done
