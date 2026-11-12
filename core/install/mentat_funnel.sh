#!/usr/bin/env bash
# Mentat on the internet, for the Captain's friends: Tailscale Funnel -> Mentat (127.0.0.1:7350). There is ONE Mentat (skippy-core's: the chat model, the router, the DE440 data); this is
# its public address, replacing the separate Cloud Run copy (decided with the Captain, 2026-10-27: "just a few of my friends").
#   sudo bash mentat_funnel.sh            # dry run: says what it would do, changes nothing
#   sudo bash mentat_funnel.sh --apply    # open the Funnel (only if Mentat answers /health here)
#   sudo bash mentat_funnel.sh --check    # read-only: Mentat ready here, Funnel on. Exit 1 if not
#   sudo bash mentat_funnel.sh --off      # close the Funnel
# No key on purpose: Mentat is a public read-only physics API (its own per-address rate limit, MENTAT_RATE_PER_MIN, and its research log apply), and a claude.ai custom connector
# cannot send a bearer token. The LLM door (funnel.sh, :443) keeps its key. The Funnel needs no firewall hole: tailscaled proxies to localhost and ufw keeps denying inbound.
# The public address is https://<this machine's tailnet DNS name>:8443/ ; Mentat's MCP endpoint is /mcp under it.
set -euo pipefail
PORT="${MENTAT_FUNNEL_PORT:-8443}"        # Funnel allows 443 (the LLM door), 8443 or 10000
TARGET="http://127.0.0.1:7350"
MODE="${1:-dry}"

ready() { [[ "$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$TARGET/health")" == 200 ]]; }
name() { tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'; }
# A tailnet-only `serve` of the same target also prints "proxy $TARGET", so ask for the Funnel flag of exactly this host:port.
funnel_on() { tailscale serve status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("AllowFunnel",{}).get(sys.argv[1]) else 1)' "$(name):$PORT"; }

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "Mentat answers /health 200 on $TARGET" ready
  chk "Funnel :$PORT -> $TARGET"             funnel_on
  exit $bad
fi

if [[ $EUID -ne 0 ]]; then echo "run it with sudo" >&2; exit 1; fi

if [[ "$MODE" == "--off" ]]; then
  tailscale funnel --https="$PORT" off && echo "Funnel :$PORT closed"
  exit 0
fi
APPLY=0; [[ "$MODE" == "--apply" ]] && APPLY=1

echo "== Mentat"
if ready; then echo "ok: $TARGET/health is 200"; else echo "refusing: Mentat is not ready on $TARGET" >&2; (( APPLY )) && exit 1 || true; fi
echo "== the Funnel"
if funnel_on; then echo "ok: Funnel :$PORT -> $TARGET"
elif (( APPLY )); then
  tailscale funnel --bg --https="$PORT" "$TARGET"
  echo "+ Funnel on: https://$(name):$PORT/   (MCP: /mcp)"
else echo "would: tailscale funnel --bg --https=$PORT $TARGET   (then https://$(name):$PORT/)"; fi
