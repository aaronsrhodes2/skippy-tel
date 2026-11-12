#!/usr/bin/env bash
# The Captain's Tesla on SkippyCore: what the box needs around core/compose.tesla.yml (TeslaMate and the car reader are containers the
# deployer runs once CORE_TESLA=1).
#   sudo bash core/install/tesla.sh            # dry run: says what it would do, changes nothing
#   sudo bash core/install/tesla.sh --apply
#   sudo bash core/install/tesla.sh --check    # read-only: is all of it live? exit 1 if not
# Idempotent.
#
#   * /etc/skippy-core/tesla.env (root, 600): ENCRYPTION_KEY (TeslaMate encrypts the Tesla tokens with it), DATABASE_PASS and
#     POSTGRES_PASSWORD (the same, made here once), TZ (the box's own). Made once and never rewritten: a new ENCRYPTION_KEY would
#     make the stored tokens unreadable and the Captain would have to sign in again.
#   * the tailnet doors (never the Funnel): :4000 TeslaMate's page (the one-time sign-in, and its settings), :7351 the car reader
#     (skippy-pc's reporter, CAR_URL). Both proxy to 127.0.0.1, so ufw keeps denying everything inbound.
#   * /etc/skippy-core/tesla-ble/vin.env (root, 700 directory): TESLA_VIN, read from TeslaMate, for car-fleet. (The folder's name is from
#     the Bluetooth charging door, core/car-ble, retired 2026-11-01: charging goes through the Fleet API now, car-fleet's /charge.)
#   * /etc/skippy-core/tesla-fleet/telemetry/ (root, 700): the car's streaming door (docs/tesla.md → Sentry): our own CA and a server
#     certificate for telemetry.makerportal.live (Tesla takes only a name under the app's domain, so not the Funnel; the CA is made
#     once and goes to the car in its signed config), host.env (TELEMETRY_HOST). The home router forwards TCP 7443 to this box's
#     7443 (fleet-telemetry: the car's mutual TLS ends there) and cd.py keeps the name on home's public IP. :7353 is
#     car-fleet's tailnet door (the guard: Sentry's events, arm/disarm, the alarm's answer; charging on/off and the limit).
#   * /etc/skippy-core/core.env: CORE_TESLA=1, then a repair so it starts now rather than at the next commit.
set -euo pipefail
MODE="${1:-}"
APPLY=0; [[ "$MODE" == "--apply" ]] && APPLY=1
CORE_ENV="${CORE_ENV:-/etc/skippy-core/core.env}"
TESLA_ENV="${TESLA_ENV:-/etc/skippy-core/tesla.env}"
REPO="${REPO:-/opt/skippy-core/repo}"
BLE="${BLE:-/etc/skippy-core/tesla-ble}"
DOORS=(4000 7351 7353)
TELE="${TELE:-/etc/skippy-core/tesla-fleet/telemetry}"
TELE_PORT=7443
TELE_HOST="${TELE_HOST:-telemetry.makerportal.live}"   # Tesla takes only a name under the app's domain
served() { tailscale serve status 2>/dev/null | grep -q ":$1 (tailnet only)"; }

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "$TESLA_ENV exists, root only"     '[[ $(stat -c %a "$TESLA_ENV") == 600 ]] && grep -q "^ENCRYPTION_KEY=." "$TESLA_ENV"'
  chk "CORE_TESLA=1 in $CORE_ENV"        'grep -qx "CORE_TESLA=1" "$CORE_ENV"'
  for p in "${DOORS[@]}"; do chk ":$p served to the tailnet only" "served $p"; done
  chk "TeslaMate's page answers on :4000" 'curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:4000/'
  chk "the VIN is known (vin.env)"       'grep -q "^TESLA_VIN=." "$BLE/vin.env"'
  chk "the old Bluetooth door is gone"   '! served 7352'
  chk "the telemetry certificate exists, root only" '[[ $(stat -c %a "$TELE/server.key") == 600 ]] && grep -q "^TELEMETRY_HOST=." "$TELE/host.env"'
  chk "$TELE_HOST points at home" '[[ $(getent ahostsv4 "$TELE_HOST" | awk "NR==1{print \$1}") == $(curl -s -m 10 -4 https://api.ipify.org) ]]'
  chk "fleet-telemetry listens on :7443" 'timeout 5 bash -c "</dev/tcp/127.0.0.1/7443"'
  chk "the guard answers on :7353" 'curl -sS --max-time 5 -o /dev/null http://127.0.0.1:7353/health'
  chk "car reader connected to MQTT"     'curl -fsS --max-time 5 http://127.0.0.1:7351/car | grep -q "\"mqtt\": true"'
  if curl -fsS --max-time 5 http://127.0.0.1:7351/health >/dev/null 2>&1; then echo "ok    a car is signed in"
  else echo "todo  no car yet: the Captain signs in to Tesla at http://skippy-core:4000 (not a failure)"; fi
  exit $bad
fi

do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }
setting() {  # setting KEY VALUE: in core.env, replaced if present
  if grep -q "^$1=" "$CORE_ENV" 2>/dev/null; then
    if grep -qx "$1=$2" "$CORE_ENV"; then echo "ok: $1"; else do_ sed -i "s|^$1=.*|$1=$2|" "$CORE_ENV"; fi
  else
    if (( APPLY )); then echo "+ $1=$2 >> $CORE_ENV"; printf '%s=%s\n' "$1" "$2" >> "$CORE_ENV"; else echo "would: add $1=$2 to $CORE_ENV"; fi
  fi
}

if (( APPLY )) && [[ $EUID -ne 0 ]]; then echo "run it with sudo" >&2; exit 1; fi

echo "== 1/3 the secrets ($TESLA_ENV)"
if [[ -s "$TESLA_ENV" ]]; then echo "ok: $TESLA_ENV (kept: a new key would lose the stored tokens)"
elif (( APPLY )); then
  pass="$(openssl rand -hex 24)"
  ( umask 077; printf 'ENCRYPTION_KEY=%s\nDATABASE_PASS=%s\nPOSTGRES_PASSWORD=%s\nTZ=%s\n' \
      "$(openssl rand -hex 32)" "$pass" "$pass" "$(timedatectl show -p Timezone --value 2>/dev/null || echo UTC)" > "$TESLA_ENV" )
  echo "+ made $TESLA_ENV (600)"
else echo "would: make $TESLA_ENV with a new ENCRYPTION_KEY and database password (600, root)"; fi

echo "== the car's VIN ($BLE/vin.env)"
(( APPLY )) && install -d -m 700 "$BLE"
if grep -q "^TESLA_VIN=." "$BLE/vin.env" 2>/dev/null; then echo "ok: $BLE/vin.env"
elif (( APPLY )); then
  db=$(docker ps --format '{{.Names}}' | grep -m1 'tesla-db' || true)
  vin=$([[ -n "$db" ]] && docker exec "$db" psql -U teslamate -d teslamate -Atc "select vin from cars order by id limit 1" || true)
  if [[ -n "$vin" ]]; then ( umask 077; echo "TESLA_VIN=$vin" > "$BLE/vin.env" ); echo "+ wrote $BLE/vin.env"
  else ( umask 077; : > "$BLE/vin.env" ); echo "todo: no car in TeslaMate yet; run --apply again after signing in to Tesla"; fi
else echo "would: write $BLE/vin.env with the car's VIN from TeslaMate"; fi

echo "== the car's streaming door ($TELE)"
host="$TELE_HOST"
if [[ -s "$TELE/server.key" ]] && openssl x509 -in "$TELE/server.pem" -noout -ext subjectAltName 2>/dev/null | grep -q "DNS:$host"; then
  echo "ok: $TELE/server.pem names $host"
elif (( APPLY )); then
  install -d -m 700 "$TELE"
  ( umask 077; cd "$TELE"
    if [[ ! -s ca.key ]]; then                                   # the CA is kept: the car's pushed config carries it
      openssl ecparam -name prime256v1 -genkey -noout -out ca.key
      openssl req -x509 -new -key ca.key -sha256 -days 3650 -subj "/CN=skippy-core car telemetry CA" -out ca.pem
    fi
    openssl ecparam -name prime256v1 -genkey -noout -out server.key
    openssl req -new -key server.key -subj "/CN=$host" -out server.csr
    printf 'subjectAltName=DNS:%s\nextendedKeyUsage=serverAuth\n' "$host" > server.ext
    openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 3650 -sha256 -extfile server.ext -out server.pem
    cat ca.pem >> server.pem
    rm -f server.csr server.ext )
  echo "+ made a telemetry certificate for $host (signed by the box's own CA)"
else echo "would: make a telemetry certificate for $host in $TELE (700, root; the CA made once and kept)"; fi
if grep -qx "TELEMETRY_HOST=$host" "$TELE/host.env" 2>/dev/null; then echo "ok: $TELE/host.env"
elif (( APPLY )); then ( umask 077; echo "TELEMETRY_HOST=$host" > "$TELE/host.env" ); echo "+ wrote $TELE/host.env"
else echo "would: write $TELE/host.env (TELEMETRY_HOST=$host)"; fi
echo "the router forwards TCP $TELE_PORT to this box's $TELE_PORT; cd.py keeps $host on home's public IP (deploy.json home_dns)"

echo "== 2/3 the tailnet doors (TeslaMate's page, the car reader, the guard and charging)"
for p in "${DOORS[@]}"; do
  if served "$p"; then echo "ok: :$p already served"; else do_ tailscale serve --bg --http="$p" "http://127.0.0.1:$p"; fi
done
if served 7352; then do_ tailscale serve --http=7352 off; fi   # the retired Bluetooth charging door (core/car-ble)

echo "== 3/3 the deployer runs it"
setting CORE_TESLA 1
if (( APPLY )); then
  do_ python3 "$REPO/core/deployer/deployer.py" repair
  echo "then: sudo bash $REPO/core/install/tesla.sh --check"
else
  echo "would: python3 $REPO/core/deployer/deployer.py repair   (starts it now)"
fi
