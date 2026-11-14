#!/usr/bin/env bash
# Keeps the house MCP door (services/satellite/house_mcp.py --http) running; the SkippyHouseMcp scheduled task runs this at sign-in.
# Loopback only (127.0.0.1:47861/mcp) unless HOUSE_MCP_HOST and HOUSE_MCP_KEY are both set in the vault .env (the server refuses a wider bind without the key).
# Restarts it if it exits, 3 s doubling to 60 s. Log: state/house-mcp.log. One copy only: state/house-mcp-run.pid.
cd "$(dirname "$0")/.." || exit 1
mkdir -p state
PIDFILE=state/house-mcp-run.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then exit 0; fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT
export PYTHONIOENCODING=utf-8:replace PYTHONUTF8=1
for f in "/g/My Drive/vault/.env"; do [ -f "$f" ] && while IFS='=' read -r k v; do case "$k" in HOUSE_MCP_KEY|HOUSE_MCP_HOST) export "$k=$(printf %s "$v" | tr -d '"'"'"'\r')";; esac; done < "$f"; done
delay=3
while true; do
  started=$(date +%s)
  services/satellite/.venv/Scripts/python -u services/satellite/house_mcp.py --http 47861 --host "${HOUSE_MCP_HOST:-127.0.0.1}" >> state/house-mcp.log 2>&1
  [ $(( $(date +%s) - started )) -gt 120 ] && delay=3
  sleep "$delay"
  delay=$(( delay * 2 > 60 ? 60 : delay * 2 ))
done
