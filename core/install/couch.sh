#!/usr/bin/env bash
# The bedroom TV on skippy-core's HDMI: a console that is skippy-pc (2026-11-01, the Captain: "jump right onto it as if we are on this PC").
#
#   skippy-pc: Sunshine (NVENC on the RTX card; apps "Desktop" and "Steam Big Picture") ──LAN──▶ skippy-core: Moonlight ──HDMI──▶ TV
#
#   sudo bash core/install/couch.sh            # dry run: says what it would do, changes nothing
#   sudo bash core/install/couch.sh --apply    # install and start it (idempotent)
#   sudo bash core/install/couch.sh --pair     # pair Moonlight with skippy-pc's Sunshine (SUNSHINE_USER/SUNSHINE_PASSWORD in the env)
#   sudo bash core/install/couch.sh --controller   # pair a Bluetooth controller: put it in pairing mode first
#   sudo bash core/install/couch.sh --check    # read-only: is all of it live? exit 1 if not
#
#   * No SteamOS and no desktop: skippy-core stays the house's AI box. A user `couch` (no sudo, no docker, nothing of aaron's) owns the TV:
#     couch.service logs it in on tty1 and runs cage (a one-app Wayland kiosk) with Moonlight full screen. No login screen, no password:
#     the TV shows skippy-pc's Desktop (COUCH_APP) and comes back by itself when the stream ends or skippy-pc was off.
#   * Moonlight from Flathub (Ubuntu has no package; it is Moonlight's own Linux build). Video is decoded by the iGPU's VCN block, so the
#     models barely notice.
#   * Sound: the TV's HDMI is couch's (its own PipeWire); the analog jack (the Enclave's aux, core/install/enclave.sh) is aaron's. Each
#     WirePlumber is told to leave the other's card alone, so they never fight over a device.
#   * Controllers: Valve's udev rules (steam-devices) and the input group; a Bluetooth one is paired once with --controller and trusted,
#     so it reconnects whenever it is switched on.
set -euo pipefail
MODE="${1:-}"
APPLY=0; [[ "$MODE" == "--apply" ]] && APPLY=1
COUCH="${COUCH_USER:-couch}"
HOST="${COUCH_HOST:-192.168.0.164}"          # skippy-pc (reserved in the router)
APP="${COUCH_APP:-Desktop}"
FLATPAK_ID=com.moonlight_stream.Moonlight
HDMI_CARD="${COUCH_HDMI_CARD:-alsa_card.pci-0000_c5_00.1}"      # the Radeon's HDMI audio: the TV
ANALOG_CARD="${COUCH_ANALOG_CARD:-alsa_card.pci-0000_c5_00.6}"  # the Ryzen codec: the Enclave's aux
AARON="${ENCLAVE_USER:-aaron}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

as() { local u="$1"; shift; local id; id="$(id -u "$u")"; sudo -u "$u" XDG_RUNTIME_DIR="/run/user/$id" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$id/bus" "$@"; }
moonlight() { as "$COUCH" env QT_QPA_PLATFORM=offscreen flatpak run --command=moonlight "$FLATPAK_ID" "$@"; }

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "user $COUCH exists, no sudo, no docker"  'id "$COUCH" && ! id -nG "$COUCH" | grep -qwE "sudo|docker"'
  chk "Moonlight installed (Flathub)"          'flatpak info --system "$FLATPAK_ID"'
  chk "couch.service running (cage + Moonlight)" 'systemctl is-active -q couch'
  # "active" was green for 4.5 hours of black TV (2026-11-03): a Moonlight that is really streaming has its render thread and a quiet decoder queue
  chk "Moonlight is streaming (not wedged)"    'p=$(pgrep -u "$COUCH" -x moonlight | head -1); [[ -n "$p" ]] && ps -L -o comm= -p "$p" | grep -x PacerRender && ! tail -n 200 "/home/$COUCH/moonlight.log" | grep "queue overflow"'
  chk "the TV is connected"                    'grep -qx connected /sys/class/drm/card*-HDMI-A-1/status'
  chk "Moonlight paired with $HOST"            'grep -q "$HOST" /home/$COUCH/.var/app/$FLATPAK_ID/config/Moonlight\ Game\ Streaming\ Project/Moonlight.conf'
  chk "skippy-pc's Sunshine answers"           'timeout 3 bash -c "</dev/tcp/$HOST/47989"'
  chk "a controller paired"                    'bluetoothctl devices Paired | grep -q .'
  exit $bad
fi

if [[ $EUID -ne 0 && ( $APPLY == 1 || "$MODE" == "--pair" || "$MODE" == "--controller" ) ]]; then echo "run it with sudo" >&2; exit 1; fi
do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }
put() {  # put <path> <owner> : stdin to <path> when it differs
  local new; new="$(cat)"
  if [[ -f "$1" ]] && [[ "$(cat "$1")" == "$new" ]]; then echo "ok: $1"; return 1; fi
  if (( APPLY )); then echo "+ write $1"; mkdir -p "$(dirname "$1")"; printf '%s\n' "$new" > "$1"; chown "$2" "$1"; else echo "would: write $1"; fi
}

if [[ "$MODE" == "--pair" ]]; then
  : "${SUNSHINE_USER:?SUNSHINE_USER (the vault)}" "${SUNSHINE_PASSWORD:?SUNSHINE_PASSWORD (the vault)}"
  pin=$(printf '%04d' $((RANDOM % 10000)))
  echo "== pairing Moonlight with $HOST (PIN $pin, sent to Sunshine's API)"
  systemctl stop couch                                             # one Moonlight at a time: they share the paired-hosts file
  moonlight pair "$HOST" --pin "$pin" & pid=$!
  api="https://$HOST:47990/api/pin"; auth="$SUNSHINE_USER:$SUNSHINE_PASSWORD"
  id=""                                          # Sunshine (2026) lists the waiting request; the PIN must name its pairing_id
  for _ in $(seq 20); do
    sleep 1
    id=$(curl -sk -m 5 -u "$auth" "$api" | python3 -c 'import json,sys; p=json.load(sys.stdin).get("pairings") or []; print(p[0]["id"] if p else "")' 2>/dev/null || true)
    [[ -n "$id" ]] && break
  done
  [[ -n "$id" ]] || { kill "$pid" 2>/dev/null; echo "Sunshine never saw the pairing request" >&2; exit 1; }
  curl -sk -m 10 -u "$auth" -H "Content-Type: application/json" -d "{\"pairing_id\": \"$id\", \"pin\": \"$pin\", \"name\": \"skippy-core TV\"}" "$api"; echo
  # Moonlight's CLI saves the pairing but doesn't always exit: give it 20 s, then end it (the saved certificate is what counts)
  for _ in $(seq 20); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done; kill "$pid" 2>/dev/null || true
  grep -q "$HOST" "/home/$COUCH/.var/app/$FLATPAK_ID/config/Moonlight Game Streaming Project/Moonlight.conf" && echo "paired" \
    || { echo "pairing failed" >&2; exit 1; }
  systemctl restart couch
  exit 0
fi

if [[ "$MODE" == "--controller" ]]; then
  secs="${COUCH_PAIR_S:-90}"
  echo "== pairing a controller (hold its pairing button now; $secs s)"
  bluetoothctl pairable on >/dev/null
  before=$(bluetoothctl devices | sort)
  bluetoothctl --timeout "$secs" scan on >/dev/null 2>&1 &
  for _ in $(seq "$secs"); do
    new=$(comm -13 <(echo "$before") <(bluetoothctl devices | sort) | grep -iE "controller|gamepad|xbox|dualsense|wireless|pro|8bitdo" | head -1 || true)
    [[ -n "$new" ]] && break; sleep 1
  done
  [[ -n "${new:-}" ]] || { echo "no controller seen: is it in pairing mode?" >&2; exit 1; }
  mac=$(awk '{print $2}' <<<"$new"); echo "found: $new"
  # One bluetoothctl session with its agent, trusted BEFORE pairing: an Xbox pad answers one-shot `pair` with AuthenticationFailed
  { echo "agent NoInputNoOutput"; echo "default-agent"; echo "pairable on"; echo "trust $mac"; sleep 1; echo "pair $mac"; sleep 15
    echo "connect $mac"; sleep 6; echo "quit"; } | bluetoothctl 2>&1 | grep -E "Pairing successful|Failed|Connection successful|Paired: yes" || true
  bluetoothctl info "$mac" | grep -E "Paired|Bonded|Trusted|Connected"
  echo "then: sudo bash $0 --check"
  exit 0
fi

echo "== 1/5 packages"
PKGS=(cage flatpak steam-devices pipewire pipewire-pulse wireplumber)
missing=(); for p in "${PKGS[@]}"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "ok installed" || missing+=("$p"); done
if (( ${#missing[@]} )); then DEBIAN_FRONTEND=noninteractive do_ apt-get install -y -q --no-install-recommends "${missing[@]}"; else echo "ok: all installed"; fi
flatpak remotes --system 2>/dev/null | grep -q flathub || do_ flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak info --system "$FLATPAK_ID" >/dev/null 2>&1 && echo "ok: Moonlight" || do_ flatpak install --system -y --noninteractive flathub "$FLATPAK_ID"

echo "== 2/5 the couch user (no sudo, no docker)"
id "$COUCH" >/dev/null 2>&1 && echo "ok: $COUCH" || do_ useradd --create-home --shell /usr/sbin/nologin --groups video,render,audio,input "$COUCH"
id "$COUCH" >/dev/null 2>&1 && for g in video render audio input; do id -nG "$COUCH" | grep -qw "$g" || do_ usermod -aG "$g" "$COUCH"; done
loginctl show-user "$COUCH" -p Linger 2>/dev/null | grep -q yes && echo "ok: linger $COUCH" || do_ loginctl enable-linger "$COUCH"

echo "== 3/5 sound: the TV's HDMI is couch's, the analog jack aaron's"
off() {  # off <user> <card>: that user's WirePlumber leaves <card> alone
  local home; home="$(getent passwd "$1" | cut -d: -f6)"
  (( APPLY )) && for d in .config .config/wireplumber .config/wireplumber/wireplumber.conf.d; do
    [[ -d "$home/$d" ]] || install -d -o "$1" -g "$1" "$home/$d"          # its owner's, not root's: else its user services can't write
  done
  put "$home/.config/wireplumber/wireplumber.conf.d/60-skippy-cards.conf" "$1:$1" <<EOF || true
# core/install/couch.sh: one owner per sound card (the TV's HDMI: couch; the Enclave's aux: aaron).
monitor.alsa.rules = [
  { matches = [ { device.name = "$2" } ], actions = { update-props = { device.disabled = true } } }
]
EOF
}
off "$AARON" "$HDMI_CARD"
id "$COUCH" >/dev/null 2>&1 && off "$COUCH" "$ANALOG_CARD"
if (( APPLY )); then
  for _ in $(seq 20); do [[ -S "/run/user/$(id -u "$COUCH")/bus" ]] && break; sleep 0.5; done
  as "$COUCH" systemctl --user enable --now pipewire pipewire-pulse wireplumber
  as "$COUCH" systemctl --user restart wireplumber
  as "$AARON" systemctl --user restart wireplumber
fi

echo "== 4/5 the kiosk: couch on tty1, cage, Moonlight full screen"
if ! cmp -s "$HERE/../couch/run.sh" /usr/local/lib/skippy-core/couch-run.sh 2>/dev/null; then
  do_ install -D -m 755 "$HERE/../couch/run.sh" /usr/local/lib/skippy-core/couch-run.sh
else echo "ok: run.sh"; fi
put /etc/systemd/system/couch.service root:root <<EOF || true
[Unit]
Description=The bedroom TV: Moonlight full screen on skippy-pc (core/install/couch.sh)
After=systemd-user-sessions.service network-online.target
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
User=$COUCH
PAMName=login
TTYPath=/dev/tty1
StandardInput=tty
UtmpIdentifier=tty1
UtmpMode=user
Environment=XDG_SESSION_TYPE=wayland COUCH_HOST=$HOST COUCH_APP=$APP
ExecStart=/usr/bin/cage -s -- /usr/local/lib/skippy-core/couch-run.sh
Restart=always
RestartSec=3
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF
if (( APPLY )); then
  systemctl daemon-reload
  systemctl disable --now getty@tty1.service 2>/dev/null || true
  systemctl enable couch.service
  systemctl restart couch.service
fi

echo "== 5/5 next"
(( APPLY )) && echo "then: sudo SUNSHINE_USER=... SUNSHINE_PASSWORD=... bash $0 --pair; sudo bash $0 --controller; sudo bash $0 --check"
exit 0
