#!/usr/bin/env bash
# Build and flash the garage door's ratgdo32 with garage.yaml (flash.sh's sibling; the room boxes use flash.sh).
#   bash services/satellite/rooms/flash_garage.sh          first time: the ratgdo32 on skippy-pc by USB-C (GARAGE_PORT=COM9 picks the port)
#   bash services/satellite/rooms/flash_garage.sh          afterwards: the same line updates it over Wi-Fi (skippy-garage.local)
#   bash services/satellite/rooms/flash_garage.sh --check  only compiles it (nothing is flashed)
# The settings come from the vault .env (ROOM_WIFI_SSID/ROOM_WIFI_PASSWORD, else HOME_WIFI/HOME_WIFI_PWD; GARAGE_ESPHOME_KEY, GARAGE_ESPHOME_OTA);
# a missing key or OTA password is generated and printed once, to be added to the vault. ESPHome is pinned to a release (its own venv,
# not the room boxes' dev pin): ratgdo's component is built and tested against releases.
set -euo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$KIT/../../.." && pwd)"
PIN="${GARAGE_ESPHOME_PIN:-esphome==2026.10.1}"
VENV="$ROOT/state/esphome-garage-venv"
VAULT="${SKIPPY_ENV:-/g/My Drive/vault/.env}"
MODE=run; [ "${1:-}" = "--check" ] && MODE=compile

get() { [ -f "$VAULT" ] && grep -E "^$1=" "$VAULT" | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true; }
SSID="$(get ROOM_WIFI_SSID)"; PASS="$(get ROOM_WIFI_PASSWORD)"
[ -n "$SSID" ] || { SSID="$(get HOME_WIFI)"; PASS="$(get HOME_WIFI_PWD)"; }
KEY="$(get GARAGE_ESPHOME_KEY)"; OTA="$(get GARAGE_ESPHOME_OTA)"
[ -n "$SSID" ] && [ -n "$PASS" ] || { echo "add ROOM_WIFI_SSID and ROOM_WIFI_PASSWORD (a 2.4 GHz network that reaches the garage; else HOME_WIFI/HOME_WIFI_PWD) to $VAULT"; exit 1; }
if [ -z "$KEY" ]; then KEY="$(python -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())')"; echo "NEW: add to the vault .env:  GARAGE_ESPHOME_KEY=$KEY"; fi
if [ -z "$OTA" ]; then OTA="$(python -c 'import secrets;print(secrets.token_urlsafe(18))')"; echo "NEW: add to the vault .env:  GARAGE_ESPHOME_OTA=$OTA"; fi

# secrets.yaml is shared with skippy-room.yaml (flash.sh rewrites it): keep the room keys in it too, so either build works afterwards
umask 077
cat > "$KIT/secrets.yaml" <<YAML
wifi_ssid: "$SSID"
wifi_password: "$PASS"
skippy_room_api_key: "$(get SATELLITE_ESPHOME_KEY)"
skippy_room_ota_password: "$(get SATELLITE_ESPHOME_OTA)"
skippy_garage_api_key: "$KEY"
skippy_garage_ota_password: "$OTA"
YAML

if [ ! -x "$VENV/Scripts/esphome" ] && [ ! -x "$VENV/bin/esphome" ]; then
  python -m venv "$VENV"
  "$VENV"/Scripts/pip install "$PIN" 2>/dev/null || "$VENV"/bin/pip install "$PIN"
fi
ESPHOME="$VENV/Scripts/esphome"; [ -x "$ESPHOME" ] || ESPHOME="$VENV/bin/esphome"
cd "$KIT"
ARGS="$MODE garage.yaml"; [ "$MODE" = run ] && ARGS="$ARGS --no-logs ${GARAGE_PORT:+--device $GARAGE_PORT}"
if [ -n "${MSYSTEM:-}" ]; then                 # Git Bash: ESP-IDF's installer refuses to run while MSYSTEM is set (as in flash.sh)
  powershell.exe -NoProfile -Command "Remove-Item Env:MSYSTEM -ErrorAction SilentlyContinue; & '$(cygpath -w "$ESPHOME")' $ARGS; if (-not \$?) { exit 1 }; exit \$LASTEXITCODE"
else
  # shellcheck disable=SC2086
  "$ESPHOME" $ARGS
fi
if [ "$MODE" = run ]; then echo "flashed skippy-garage. In the vault: GARAGE_ESPHOME=esphome://skippy-garage.local (or its IP) and HASS_SENSITIVE=1; then restart the hub."; else echo "compiled garage.yaml (nothing flashed)"; fi
