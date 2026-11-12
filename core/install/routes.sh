#!/usr/bin/env bash
# Roads and addresses on SkippyCore: what the box needs around core/compose.routes.yml (Valhalla, Nominatim and the road door are
# containers the deployer runs once CORE_ROUTES=1).
#   sudo bash core/install/routes.sh            # dry run: says what it would do, changes nothing
#   sudo bash core/install/routes.sh --apply
#   sudo bash core/install/routes.sh --check    # read-only: is all of it live? exit 1 if not
# Idempotent.
#
#   * the tailnet door (never the Funnel): :7354 the road door (skippy-pc's morning briefing, SKIPPY_ROUTES_URL). It proxies to
#     127.0.0.1, so ufw keeps denying everything inbound.
#   * /etc/skippy-core/core.env: CORE_ROUTES=1, then a repair (only if the road door isn't running yet) so it starts now rather than at
#     the next commit. The first start downloads California's map and builds from it for about an hour: --check says "todo" meanwhile.
set -euo pipefail
MODE="${1:-}"
APPLY=0; [[ "$MODE" == "--apply" ]] && APPLY=1
CORE_ENV="${CORE_ENV:-/etc/skippy-core/core.env}"
REPO="${REPO:-/opt/skippy-core/repo}"
DOOR=7354
served() { tailscale serve status 2>/dev/null | grep -q ":$1 (tailnet only)"; }

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "CORE_ROUTES=1 in $CORE_ENV"        'grep -qx "CORE_ROUTES=1" "$CORE_ENV"'
  chk ":$DOOR served to the tailnet only" "served $DOOR"
  chk "the road door answers on :$DOOR"   "curl -sS --max-time 5 -o /dev/null http://127.0.0.1:$DOOR/health"
  if curl -fsS --max-time 5 "http://127.0.0.1:$DOOR/health" >/dev/null 2>&1; then echo "ok    ready: roads and addresses answer"
  else echo "todo  not ready yet: $(curl -sS --max-time 5 "http://127.0.0.1:$DOOR/health" 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("detail"))' 2>/dev/null || echo "no answer") (not a failure)"; fi
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

echo "== 1/2 the tailnet door"
if served "$DOOR"; then echo "ok: :$DOOR already served"; else do_ tailscale serve --bg --http="$DOOR" "http://127.0.0.1:$DOOR"; fi

echo "== 2/2 the deployer runs it"
setting CORE_ROUTES 1
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'skippycore-routes-'; then
  echo "ok: the road door is already running"
elif (( APPLY )); then
  do_ python3 "$REPO/core/deployer/deployer.py" repair
  echo "then: sudo bash $REPO/core/install/routes.sh --check"
else
  echo "would: python3 $REPO/core/deployer/deployer.py repair   (starts it now)"
fi
