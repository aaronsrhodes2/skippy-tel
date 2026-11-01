#!/usr/bin/env bash
# Keeps the Captain's Enclave Audio speakers playing the hub's sound (core/install/enclave.sh installs it as aaron's user service
# `enclave-keep`). Every few seconds:
#   * not connected: ask bluez to connect (the speaker was off, or skippy-core rebooted); a speaker that is off just fails quietly;
#   * connected: make sure PipeWire has its A2DP sink and snapclient (`enclave-snap`, the hub's stream from snapserver) plays into it;
#   * gone: stop snapclient, so snapserver lists no client and the hub's router takes the output as closed (router.py NetStream).
# So "a snapclient is connected to snapserver" means "the Enclave is on and playing": the one thing the hub has to ask.
set -uo pipefail
MAC="${ENCLAVE_MAC:?ENCLAVE_MAC}"
PREFIX="bluez_output.${MAC//:/_}"   # PipeWire names the A2DP sink bluez_output.<mac>.<n>
EVERY="${ENCLAVE_EVERY_S:-5}"
RETRY="${ENCLAVE_RETRY_S:-10}"          # how often to page a speaker that is off (or dropped by a car command: back within seconds)
last_try=0

connected() { bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes"; }
sink() { pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -m1 "^$PREFIX"; }
snap_up() { systemctl --user is-active -q enclave-snap; }

while true; do
  if connected; then
    SINK=$(sink)
    if [[ -n "$SINK" ]]; then
      pactl set-default-sink "$SINK" 2>/dev/null
      if ! snap_up; then                 # full volume once per connection (the speaker's own knob is the volume), then the stream
        echo "the Enclave is connected: playing the hub's stream into $SINK"
        pactl set-sink-volume "$SINK" 100% 2>/dev/null
        systemctl --user start enclave-snap
      fi
    fi
  else
    if snap_up; then echo "the Enclave dropped: stopping the stream"; systemctl --user stop enclave-snap; fi
    now=$(date +%s)
    if (( now - last_try >= RETRY )); then
      last_try=$now
      timeout 15 bluetoothctl connect "$MAC" >/dev/null 2>&1 && echo "connected to the Enclave"
    fi
  fi
  sleep "$EVERY"
done
