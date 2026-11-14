#!/usr/bin/env bash
# Keeps Purple Skippy's MCP door (services/purple-mcp/purple_mcp.py --http) running; the SkippyPurpleMcp scheduled task runs this at sign-in.
# Loopback only (127.0.0.1:47860/mcp) unless PURPLE_MCP_HOST and PURPLE_MCP_KEY are both set in the vault .env (the server refuses a wider bind without the key).
# Restarts it if it exits, 3 s doubling to 60 s. Log: state/purple-mcp.log. One copy only: state/purple-mcp-run.pid.
cd "$(dirname "$0")/.." || exit 1
mkdir -p state
PIDFILE=state/purple-mcp-run.pid
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then exit 0; fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT
export PYTHONIOENCODING=utf-8:replace PYTHONUTF8=1
for f in "/g/My Drive/vault/.env"; do [ -f "$f" ] && while IFS='=' read -r k v; do case "$k" in PURPLE_MCP_KEY|PURPLE_MCP_HOST) export "$k=$(printf %s "$v" | tr -d '"'"'"'\r')";; esac; done < "$f"; done
delay=3
while true; do
  started=$(date +%s)
  python -u services/purple-mcp/purple_mcp.py --http 47860 --host "${PURPLE_MCP_HOST:-127.0.0.1}" >> state/purple-mcp.log 2>&1
  [ $(( $(date +%s) - started )) -gt 120 ] && delay=3
  sleep "$delay"
  delay=$(( delay * 2 > 60 ? 60 : delay * 2 ))
done
