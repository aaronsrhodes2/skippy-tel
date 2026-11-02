#!/usr/bin/env bash
# The Captain's cameras on SkippyCore: what the box needs around core/compose.cams.yml (Frigate is a container the deployer runs once
# CORE_CAMS=1). Reolink PoE doorbell and cameras, set up on the LAN with no Reolink account (2026-11-01; Ring is off the list).
#   sudo bash core/install/cams.sh camera front_door 192.168.1.60 doorbell   # name a camera (doorbell: it has the button); repeat per camera
#   sudo bash core/install/cams.sh                # dry run: says what it would do, changes nothing
#   sudo bash core/install/cams.sh --apply
#   sudo bash core/install/cams.sh --check        # read-only: is all of it live? exit 1 if not
# Idempotent.
#
#   * /etc/skippy-core/cams.json (root, 600): the cameras by name and LAN address. Addresses only.
#   * /etc/skippy-core/cams.env (root, 600): FRIGATE_REOLINK_PASSWORD, copied from vault.env (the cameras' admin password, the one he set
#     in each camera's own local setup; the same key is in skippy-pc's vault for the hub, which hears the doorbell's button itself).
#   * ${CORE_STATE}/frigate/config/config.yml: made by core/cams/frigate_config.py from cams.json; Frigate is restarted when it changes.
#   * the tailnet door (never the Funnel): :7355 Frigate's API and page (the hub's FRIGATE_URL, and his live view). It proxies to
#     127.0.0.1, so ufw keeps denying everything inbound.
#   * /etc/skippy-core/core.env: CORE_CAMS=1, then a repair (only if Frigate isn't running yet) so it starts now.
set -euo pipefail
MODE="${1:-}"
APPLY=0; [[ "$MODE" == "--apply" ]] && APPLY=1
CORE_ENV="${CORE_ENV:-/etc/skippy-core/core.env}"
VAULT="${VAULT:-/etc/skippy-core/vault.env}"
CAMS="${CAMS:-/etc/skippy-core/cams.json}"
CAMS_ENV="${CAMS_ENV:-/etc/skippy-core/cams.env}"
CORE_STATE="${CORE_STATE:-/var/lib/skippy-core}"
REPO="${REPO:-/opt/skippy-core/repo}"
CONFIG="$CORE_STATE/frigate/config/config.yml"
DOOR=7355
served() { tailscale serve status 2>/dev/null | grep -q ":$1 (tailnet only)"; }
password() { grep -m1 '^FRIGATE_REOLINK_PASSWORD=' "$VAULT" 2>/dev/null | cut -d= -f2-; }

if [[ "$MODE" == "camera" ]]; then
  [[ $EUID -eq 0 ]] || { echo "run it with sudo" >&2; exit 1; }
  NAME="${2:?camera NAME HOST [doorbell]}"; HOST="${3:?camera NAME HOST [doorbell]}"; BELL="${4:-}"
  python3 - "$CAMS" "$NAME" "$HOST" "$BELL" <<'PY'
import json, os, sys
path, name, host, bell = sys.argv[1:5]
try:
    data = json.load(open(path, encoding="utf-8"))
except (OSError, ValueError):
    data = {"cameras": {}}
data.setdefault("cameras", {})[name] = {"host": host, "doorbell": bell == "doorbell"}
sys.path.insert(0, os.path.join(os.environ.get("REPO", "/opt/skippy-core/repo"), "core", "cams"))
import frigate_config
frigate_config.check(data)                      # a bad name or address stops here, before it is saved
old = os.umask(0o077)
with open(path + ".new", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
os.umask(old)
os.replace(path + ".new", path)
print(f"ok: {name} at {host}{' (doorbell)' if bell == 'doorbell' else ''}; now: sudo bash core/install/cams.sh --apply")
PY
  exit 0
fi

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "$CAMS names a camera, root only"   '[[ $(stat -c %a "$CAMS") == 600 ]] && python3 -c "import json,sys;assert json.load(open(sys.argv[1]))[\"cameras\"]" "$CAMS"'
  chk "$CAMS_ENV has the password, root only" '[[ $(stat -c %a "$CAMS_ENV") == 600 ]] && grep -q "^FRIGATE_REOLINK_PASSWORD=." "$CAMS_ENV"'
  chk "the config matches cams.json"      'diff -q <(python3 "$REPO/core/cams/frigate_config.py" "$CAMS") "$CONFIG"'
  chk "CORE_CAMS=1 in $CORE_ENV"          'grep -qx "CORE_CAMS=1" "$CORE_ENV"'
  chk ":$DOOR served to the tailnet only" "served $DOOR"
  chk "Frigate answers on :$DOOR"          "curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:$DOOR/api/version"
  chk "every camera sends frames"          "python3 $REPO/core/smoke.py --only cams"
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

echo "== 1/4 the cameras"
if ! python3 "$REPO/core/cams/frigate_config.py" "$CAMS" >/dev/null; then
  echo "no cameras yet: sudo bash core/install/cams.sh camera front_door <its LAN address> doorbell" >&2; exit 1
fi
[[ -n "$(password)" ]] || { echo "FRIGATE_REOLINK_PASSWORD isn't in $VAULT: add the cameras' password there first" >&2; exit 1; }

echo "== 2/4 the password and the config"
if (( APPLY )); then
  umask 077
  printf 'FRIGATE_REOLINK_PASSWORD=%s\n' "$(password)" > "$CAMS_ENV.new" && mv "$CAMS_ENV.new" "$CAMS_ENV"
  echo "+ $CAMS_ENV (the password only)"
  mkdir -p "$CORE_STATE/frigate/config" "$CORE_STATE/frigate/media"
  python3 "$REPO/core/cams/frigate_config.py" "$CAMS" > "$CONFIG.new"
  if [[ -f "$CONFIG" ]] && cmp -s "$CONFIG.new" "$CONFIG"; then rm "$CONFIG.new"; echo "ok: config unchanged"
  else
    mv "$CONFIG.new" "$CONFIG"; echo "+ $CONFIG"
    if docker ps --format '{{.Names}}' | grep -q 'skippycore-frigate-'; then do_ docker restart "$(docker ps --format '{{.Names}}' | grep 'skippycore-frigate-')"; fi
  fi
else
  echo "would: write $CAMS_ENV and $CONFIG"
fi

echo "== 3/4 the tailnet door"
if served "$DOOR"; then echo "ok: :$DOOR already served"; else do_ tailscale serve --bg --http="$DOOR" "http://127.0.0.1:$DOOR"; fi

echo "== 4/4 the deployer runs it"
setting CORE_CAMS 1
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'skippycore-frigate-'; then
  echo "ok: Frigate is already running"
elif (( APPLY )); then
  do_ python3 "$REPO/core/deployer/deployer.py" repair
  echo "then: sudo bash $REPO/core/install/cams.sh --check"
else
  echo "would: python3 $REPO/core/deployer/deployer.py repair   (starts it now)"
fi
