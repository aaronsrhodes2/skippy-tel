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
#   * /etc/skippy-core/core.env: CORE_TESLA=1, then a repair so it starts now rather than at the next commit.
set -euo pipefail
MODE="${1:-}"
APPLY=0; [[ "$MODE" == "--apply" ]] && APPLY=1
CORE_ENV="${CORE_ENV:-/etc/skippy-core/core.env}"
TESLA_ENV="${TESLA_ENV:-/etc/skippy-core/tesla.env}"
REPO="${REPO:-/opt/skippy-core/repo}"
DOORS=(4000 7351)
served() { tailscale serve status 2>/dev/null | grep -q ":$1 (tailnet only)"; }

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "$TESLA_ENV exists, root only"     '[[ $(stat -c %a "$TESLA_ENV") == 600 ]] && grep -q "^ENCRYPTION_KEY=." "$TESLA_ENV"'
  chk "CORE_TESLA=1 in $CORE_ENV"        'grep -qx "CORE_TESLA=1" "$CORE_ENV"'
  for p in "${DOORS[@]}"; do chk ":$p served to the tailnet only" "served $p"; done
  chk "TeslaMate's page answers on :4000" 'curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:4000/'
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

echo "== 2/3 the tailnet doors (TeslaMate's page, the car reader)"
for p in "${DOORS[@]}"; do
  if served "$p"; then echo "ok: :$p already served"; else do_ tailscale serve --bg --http="$p" "http://127.0.0.1:$p"; fi
done

echo "== 3/3 the deployer runs it"
setting CORE_TESLA 1
if (( APPLY )); then
  do_ python3 "$REPO/core/deployer/deployer.py" repair
  echo "then: sudo bash $REPO/core/install/tesla.sh --check"
else
  echo "would: python3 $REPO/core/deployer/deployer.py repair   (starts it now)"
fi
