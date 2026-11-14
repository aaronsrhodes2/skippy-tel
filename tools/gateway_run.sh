#!/usr/bin/env bash
# Keeps the Skippy gateway (services/gateway/gateway.py) running; the SkippyGateway scheduled task runs this at sign-in.
# Loopback only (127.0.0.1:47863/mcp) unless GATEWAY_HOST and GATEWAY_KEY are both set in the vault .env (the server refuses a wider bind without the key).
# Restarts it if it exits, 3 s doubling to 60 s. Log: state/gateway.log. One copy only: state/gateway-run.pid.
cd "$(dirname "$0")/.." || exit 1
mkdir -p state
PIDFILE=state/gateway-run.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then exit 0; fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT
export PYTHONIOENCODING=utf-8:replace PYTHONUTF8=1
for f in "/g/My Drive/vault/.env"; do [ -f "$f" ] && while IFS='=' read -r k v; do case "$k" in GATEWAY_KEY|GATEWAY_HOST|PURPLE_MCP_KEY|HOUSE_MCP_KEY) export "$k=$(printf %s "$v" | tr -d '"'"'"'\r')";; esac; done < "$f"; done
delay=3
while true; do
  started=$(date +%s)
  python -u services/gateway/gateway.py --http 47863 --host "${GATEWAY_HOST:-127.0.0.1}" >> state/gateway.log 2>&1
  [ $(( $(date +%s) - started )) -gt 120 ] && delay=3
  sleep "$delay"
  delay=$(( delay * 2 > 60 ? 60 : delay * 2 ))
done
