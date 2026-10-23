#!/usr/bin/env bash
# Build and flash one room box (the Waveshare ESP32-S3 Smart 86 Box) with skippy-room.yaml.
#   bash services/satellite/rooms/flash.sh kitchen "Kitchen"          first time: plug the box into skippy-pc by USB-C
#   bash services/satellite/rooms/flash.sh kitchen "Kitchen"          afterwards: the same line updates it over Wi-Fi
# The settings come from the vault .env (ROOM_WIFI_SSID, ROOM_WIFI_PASSWORD, SATELLITE_ESPHOME_KEY, SATELLITE_ESPHOME_OTA); a missing key or
# OTA password is generated and printed once, to be added to the vault. ESPHome is pinned to the dev commit that has I2S full duplex
# (needed: the mics and the speaker share one bus) until 2026.11.0 is released; then set ESPHOME_PIN=esphome==2026.11.0.
set -euo pipefail
ROOM="${1:?usage: flash.sh <id: kitchen> <name: Kitchen>}"
NAME="${2:-$ROOM}"
[[ "$ROOM" =~ ^[a-z0-9-]{1,20}$ ]] || { echo "the room id is lowercase letters, digits and dashes (it becomes skippy-$ROOM.local)"; exit 1; }
KIT="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$KIT/../../.." && pwd)"
PIN="${ESPHOME_PIN:-esphome @ git+https://github.com/esphome/esphome@55a62993c832653ea2eb1a94f2b86a757b8895fe}"
VENV="$ROOT/state/esphome-venv"
VAULT="${SKIPPY_ENV:-/g/My Drive/vault/.env}"

get() { [ -f "$VAULT" ] && grep -E "^$1=" "$VAULT" | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true; }
SSID="$(get ROOM_WIFI_SSID)"; PASS="$(get ROOM_WIFI_PASSWORD)"; KEY="$(get SATELLITE_ESPHOME_KEY)"; OTA="$(get SATELLITE_ESPHOME_OTA)"
[ -n "$SSID" ] && [ -n "$PASS" ] || { echo "add ROOM_WIFI_SSID and ROOM_WIFI_PASSWORD (a 2.4 GHz network) to $VAULT"; exit 1; }
if [ -z "$KEY" ]; then KEY="$(python -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())')"; echo "NEW: add to the vault .env:  SATELLITE_ESPHOME_KEY=$KEY"; fi
if [ -z "$OTA" ]; then OTA="$(python -c 'import secrets;print(secrets.token_urlsafe(18))')"; echo "NEW: add to the vault .env:  SATELLITE_ESPHOME_OTA=$OTA"; fi

umask 077
cat > "$KIT/secrets.yaml" <<YAML
wifi_ssid: "$SSID"
wifi_password: "$PASS"
skippy_room_api_key: "$KEY"
skippy_room_ota_password: "$OTA"
YAML

if [ ! -x "$VENV/Scripts/esphome" ] && [ ! -x "$VENV/bin/esphome" ]; then
  python -m venv "$VENV"
  "$VENV"/Scripts/pip install "$PIN" 2>/dev/null || "$VENV"/bin/pip install "$PIN"
fi
ESPHOME="$VENV/Scripts/esphome"; [ -x "$ESPHOME" ] || ESPHOME="$VENV/bin/esphome"
cd "$KIT"
"$ESPHOME" -s name "skippy-$ROOM" -s room "$NAME" run skippy-room.yaml
echo "flashed skippy-$ROOM. Add it to SATELLITE_ROOMS in the vault: $ROOM=esphome://skippy-$ROOM.local"
