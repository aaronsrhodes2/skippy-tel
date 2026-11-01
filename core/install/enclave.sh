#!/usr/bin/env bash
# The Captain's Enclave Audio speakers on skippy-core's Bluetooth: the office's main speaker (2026-11-01; the projector's HDMI is off at
# night, and skippy-pc's own Bluetooth is weak). The hub on skippy-pc still makes every sound; this box only plays it.
#
#   hub (skippy-pc, router.py NetStream) ──tcp :4953 (tailnet)──▶ snapserver ──▶ snapclient ──▶ PipeWire ──A2DP──▶ Enclave
#
#   sudo bash core/install/enclave.sh            # dry run: says what it would do, changes nothing
#   sudo bash core/install/enclave.sh --apply    # install and start everything (idempotent)
#   sudo bash core/install/enclave.sh --pair     # pair + trust the speaker: put it in Bluetooth pairing mode first
#   sudo bash core/install/enclave.sh --check    # read-only: is all of it live? exit 1 if not
#
#   * PipeWire + WirePlumber + bluez5 as aaron's user services, kept running with no login (linger). WirePlumber's Bluetooth is told
#     not to wait for a desk seat (a headless box never has one).
#   * snapserver (system service): one source, raw PCM pushed by the hub on 127.0.0.1:4953 (48 kHz, 16 bit, stereo), a 300 ms buffer;
#     its JSON-RPC on 127.0.0.1:1780 says whether a client plays. Both reach the tailnet through `tailscale serve`, never the LAN or
#     the Funnel (they have no key of their own).
#   * enclave-keep (aaron's user service, core/enclave/keep.sh): connects the speaker whenever it is on and starts snapclient
#     (enclave-snap) into its A2DP sink only while it is connected. So snapserver lists a client exactly when the Enclave plays.
#   * The radio: tesla-control (core/car-ble) takes its adapter away from bluez for every charge command, which drops the speaker for
#     ~30 s (the keeper reconnects it; the hub's router moves the voice to the other outputs meanwhile). The fix is a second USB radio
#     for the car: TESLA_BT_ADAPTER=hci1 in /etc/skippy-core/tesla-ble/vin.env (core/car-ble/ble.py), and the Enclave keeps hci0.
set -euo pipefail
MODE="${1:-}"
APPLY=0; [[ "$MODE" == "--apply" || "$MODE" == "--pair" ]] && APPLY=1
MAC="${ENCLAVE_MAC:-00:07:80:C5:32:90}"
USER_NAME="${ENCLAVE_USER:-aaron}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UID_N="$(id -u "$USER_NAME")"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
RUN="/run/user/$UID_N"
as_user() { sudo -u "$USER_NAME" XDG_RUNTIME_DIR="$RUN" DBUS_SESSION_BUS_ADDRESS="unix:path=$RUN/bus" "$@"; }

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "linger on for $USER_NAME"            'loginctl show-user "$USER_NAME" -p Linger | grep -q yes'
  chk "PipeWire running"                    'as_user systemctl --user is-active -q pipewire wireplumber pipewire-pulse'
  chk "snapserver running"                  'systemctl is-active -q snapserver'
  chk "enclave-keep running"                'as_user systemctl --user is-active -q enclave-keep'
  chk "Enclave bonded and trusted"          'bluetoothctl info "$MAC" | grep -q "Bonded: yes" && bluetoothctl info "$MAC" | grep -q "Trusted: yes"'
  chk "Enclave connected"                   'bluetoothctl info "$MAC" | grep -q "Connected: yes"'
  chk "its A2DP sink in PipeWire"           'as_user pactl list short sinks | grep -q "bluez_output.${MAC//:/_}"'
  chk "snapclient playing into it"          'as_user systemctl --user is-active -q enclave-snap'
  chk ":4953 and :1780 on the tailnet"      'tailscale serve status | grep -q ":4953" && tailscale serve status | grep -q ":1780 (tailnet only)"'
  chk "the car's BLE door answers"          'curl -fsS -m 5 http://127.0.0.1:7352/health | grep -q "\"ready\": true"'
  exit $bad
fi

if (( APPLY )) && [[ $EUID -ne 0 ]]; then echo "run it with sudo" >&2; exit 1; fi
do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }
put() {  # put <path> <owner> : stdin to <path> when it differs; prints whether it changed
  local new; new="$(cat)"
  if [[ -f "$1" ]] && [[ "$(cat "$1")" == "$new" ]]; then echo "ok: $1"; return 1; fi
  if (( APPLY )); then echo "+ write $1"; mkdir -p "$(dirname "$1")"; printf '%s\n' "$new" > "$1"; chown "$2" "$1" "$(dirname "$1")"
  else echo "would: write $1"; fi
}

if [[ "$MODE" == "--pair" ]]; then
  echo "== pairing $MAC (the speaker must be in Bluetooth pairing mode)"
  # Bonded, not just paired: the link key kept on both sides, so the speaker comes back after it is switched off. A pairing made while
  # the adapter wasn't pairable (2026-11-01, the first one) says Paired but not Bonded: removed and made again.
  bluetoothctl pairable on >/dev/null
  if bluetoothctl info "$MAC" 2>/dev/null | grep -q "Bonded: no"; then echo "paired but not bonded: pairing again"; bluetoothctl remove "$MAC"; fi
  bluetoothctl info "$MAC" 2>/dev/null | grep -q "Paired: yes" && echo "ok: already paired" || {
    bluetoothctl --timeout 25 scan on >/dev/null 2>&1 &
    for _ in $(seq 25); do bluetoothctl devices | grep -q "$MAC" && break; sleep 1; done
    bluetoothctl --agent NoInputNoOutput pair "$MAC"
  }
  bluetoothctl trust "$MAC"
  bluetoothctl connect "$MAC" || true
  echo "then: sudo bash $0 --check"
  exit 0
fi

echo "== 1/5 packages"
PKGS=(pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils snapserver snapclient bluez)
missing=(); for p in "${PKGS[@]}"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "ok installed" || missing+=("$p"); done
if (( ${#missing[@]} )); then DEBIAN_FRONTEND=noninteractive do_ apt-get install -y -q --no-install-recommends "${missing[@]}"; else echo "ok: all installed"; fi
# The package's own snapclient runs as a system user on ALSA, which a Bluetooth speaker isn't: ours is aaron's, on PipeWire.
systemctl is-enabled -q snapclient 2>/dev/null && do_ systemctl disable --now snapclient || echo "ok: system snapclient off"
systemctl is-active -q bluetooth 2>/dev/null && echo "ok: bluetooth running" || do_ systemctl enable --now bluetooth

echo "== 2/5 snapserver: the hub's stream in on 127.0.0.1:4953"
if put /etc/snapserver.conf root:root <<'EOF'
# core/install/enclave.sh: one source, pushed by skippy-pc's hub (services/satellite/router.py NetStream), raw PCM, local client only.
[server]
threads = -1

[stream]
bind_to_address = 127.0.0.1
port = 1704
source = tcp://127.0.0.1:4953?name=hub&mode=server&sampleformat=48000:16:2
codec = pcm
buffer = 300
chunk_ms = 20
send_to_muted = false

[tcp-control]
enabled = false

[http]
enabled = true
bind_to_address = 127.0.0.1
port = 1780
doc_root =

[logging]
filter = *:info
EOF
then do_ systemctl restart snapserver; fi
do_ systemctl enable --now snapserver

echo "== 3/5 PipeWire for $USER_NAME, running with no login"
loginctl show-user "$USER_NAME" -p Linger 2>/dev/null | grep -q yes && echo "ok: linger" || do_ loginctl enable-linger "$USER_NAME"
(( APPLY )) && for _ in $(seq 20); do [[ -S "$RUN/bus" ]] && break; sleep 0.5; done
put "$HOME_DIR/.config/wireplumber/wireplumber.conf.d/50-skippy-headless.conf" "$USER_NAME:$USER_NAME" <<'EOF' || true
# core/install/enclave.sh: a headless box has no desk seat; WirePlumber would otherwise keep Bluetooth off waiting for one.
wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
EOF
do_ as_user systemctl --user enable --now pipewire pipewire-pulse wireplumber
(( APPLY )) && as_user systemctl --user restart wireplumber

echo "== 4/5 enclave-keep and enclave-snap"
install_keep=0
if ! cmp -s "$HERE/../enclave/keep.sh" /usr/local/lib/skippy-core/enclave-keep.sh; then
  do_ install -D -m 755 "$HERE/../enclave/keep.sh" /usr/local/lib/skippy-core/enclave-keep.sh; install_keep=1
else echo "ok: keep.sh"; fi
UNITS="$HOME_DIR/.config/systemd/user"
put "$UNITS/enclave-snap.service" "$USER_NAME:$USER_NAME" <<'EOF' && install_keep=1 || true
[Unit]
Description=The hub's stream from snapserver into the Enclave (started and stopped by enclave-keep)
After=pipewire-pulse.service

[Service]
ExecStart=/usr/bin/snapclient --hostID enclave --player pulse tcp://127.0.0.1:1704
Restart=on-failure
RestartSec=2
EOF
put "$UNITS/enclave-keep.service" "$USER_NAME:$USER_NAME" <<EOF && install_keep=1 || true
[Unit]
Description=Keeps the Enclave connected and playing the hub's stream (core/enclave/keep.sh)
After=pipewire-pulse.service wireplumber.service

[Service]
Environment=ENCLAVE_MAC=$MAC
ExecStart=/usr/local/lib/skippy-core/enclave-keep.sh
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
if (( APPLY )); then
  as_user systemctl --user daemon-reload
  as_user systemctl --user enable enclave-keep
  if (( install_keep )); then as_user systemctl --user restart enclave-keep; else as_user systemctl --user start enclave-keep; fi
else echo "would: enable and start enclave-keep (aaron's user service)"; fi

echo "== 5/5 the tailnet doors (never the LAN or the Funnel)"
tailscale serve status 2>/dev/null | grep -q ":4953" && echo "ok: :4953 served" || do_ tailscale serve --bg --tcp=4953 tcp://127.0.0.1:4953
tailscale serve status 2>/dev/null | grep -q ":1780 (tailnet only)" && echo "ok: :1780 served" || do_ tailscale serve --bg --http=1780 http://127.0.0.1:1780
(( APPLY )) && echo "then: sudo bash $0 --pair (speaker in pairing mode), and sudo bash $0 --check"
exit 0
