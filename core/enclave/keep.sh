#!/usr/bin/env bash
# Keeps the Captain's Enclave Audio speakers playing the hub's sound and the house radio's (core/install/enclave.sh installs it as aaron's user service
# `enclave-keep`). Two ways to reach the speaker (ENCLAVE_LINK):
#   aux (2026-11-01, the default): a cable from skippy-core's headphone jack to the Enclave. No Bluetooth at all: across the floor it
#       stalled twice in 15 minutes and once stayed "connected" but silent until the speaker's power was pulled.
#   bt: A2DP from skippy-core's own radio, paired once with `enclave.sh --pair`.
# Every few seconds: when the speaker's sink is there, it is made the default and snapclient (`enclave-snap`, the hub's stream from
# snapserver) plays into it; when it is gone (bt: the speaker is off), snapclient stops, so snapserver lists no client and the hub's router
# takes the output as closed (router.py NetStream). So "a snapclient is connected to snapserver" means "the Enclave can play".
set -uo pipefail
LINK="${ENCLAVE_LINK:-aux}"
MAC="${ENCLAVE_MAC:-00:07:80:C5:32:90}"
if [[ "$LINK" == "aux" ]]; then
  PREFIX="${ENCLAVE_SINK:-alsa_output.pci-0000_c5_00.6.analog-stereo}"   # the Ryzen codec's analog out (its headphone port)
else
  PREFIX="bluez_output.${MAC//:/_}"   # PipeWire names the A2DP sink bluez_output.<mac>.<n>
fi
EVERY="${ENCLAVE_EVERY_S:-5}"
RETRY="${ENCLAVE_RETRY_S:-10}"          # bt: how often to page a speaker that is off
last_try=0

connected() { [[ "$LINK" == "aux" ]] || bluetoothctl info "$MAC" 2>/dev/null | grep -q "Connected: yes"; }
sink() { pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -m1 "^$PREFIX"; }
snap_up() { systemctl --user is-active -q enclave-snap; }
# The house radio's own snapclient (enclave-radio) runs beside the hub's; snapserver must have it on the `radio` stream (a new client
# lands on the first stream, the hub's). Set once, snapserver keeps it in its server.json.
radio_stream() {
  python3 - <<'PY' 2>/dev/null
import json, urllib.request
def rpc(method, params=None):
    body = json.dumps({"id": 1, "jsonrpc": "2.0", "method": method, **({"params": params} if params else {})}).encode()
    req = urllib.request.Request("http://127.0.0.1:1780/jsonrpc", data=body, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=3).read())
for g in rpc("Server.GetStatus")["result"]["server"]["groups"]:
    ids = [c["id"] for c in g["clients"]]
    if "enclave-radio" in ids and g["stream_id"] != "radio":
        if len(ids) > 1:                     # never move the hub's client with it: the radio's gets a group of its own
            rpc("Group.SetClients", {"id": g["id"], "clients": [i for i in ids if i != "enclave-radio"]})
            continue
        rpc("Group.SetStream", {"id": g["id"], "stream_id": "radio"})
        print("the house radio's client is on the radio stream")
PY
}

while true; do
  SINK=""
  connected && SINK=$(sink)
  if [[ -n "$SINK" ]]; then
    pactl set-default-sink "$SINK" 2>/dev/null
    if ! snap_up; then                 # full volume once (the speaker's own knob is the volume), then the stream
      echo "the Enclave's output ($LINK) is there: playing the hub's stream into $SINK"
      pactl set-sink-mute "$SINK" 0 2>/dev/null
      pactl set-sink-volume "$SINK" 100% 2>/dev/null
      systemctl --user start enclave-snap
    fi
    systemctl --user is-active -q enclave-radio || systemctl --user start enclave-radio
    radio_stream
  else
    if snap_up; then echo "the Enclave's output ($LINK) is gone: stopping the stream"; systemctl --user stop enclave-snap enclave-radio; fi
    if [[ "$LINK" == "bt" ]]; then
      now=$(date +%s)
      if (( now - last_try >= RETRY )); then
        last_try=$now
        timeout 15 bluetoothctl connect "$MAC" >/dev/null 2>&1 && echo "connected to the Enclave"
      fi
    fi
  fi
  sleep "$EVERY"
done
