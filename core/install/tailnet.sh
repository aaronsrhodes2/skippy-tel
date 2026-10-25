#!/usr/bin/env bash
# SkippyCore's tailnet-only doors for skippy-pc (never the Funnel: these ports have no key of their own).
#   11434  the Ollama shim (core/ollama-shim): every OLLAMA_URL on skippy-pc, Core's models under the old names
#   7350   Mentat with Core's chat model (the reporter's MENTAT_URL, wa-trickle)
#   sudo bash tailnet.sh            # dry run: says what it would do
#   sudo bash tailnet.sh --apply    # serve each port to the tailnet (idempotent; Tailscale keeps it across reboots)
#   sudo bash tailnet.sh --check    # read-only: each door served, tailnet only. Exit 1 if not
# Each proxies to 127.0.0.1, where compose publishes the container, so ufw keeps denying everything inbound.
set -euo pipefail
DOORS=(11434 7350)
declare -A PROBE=([11434]=/api/version [7350]=/health)
MODE="${1:-dry}"
served() { tailscale serve status 2>/dev/null | grep -q ":$1 (tailnet only)"; }

if [[ "$MODE" == "--check" ]]; then
  bad=0
  for p in "${DOORS[@]}"; do
    if served "$p" && curl -sf -m 10 -o /dev/null "http://127.0.0.1:$p${PROBE[$p]}"; then echo "ok    :$p served, answering"
    else echo "FAIL  :$p"; bad=1; fi
  done
  exit $bad
fi
for p in "${DOORS[@]}"; do
  if served "$p"; then echo "ok: :$p already served"
  elif [[ "$MODE" == "--apply" ]]; then tailscale serve --bg --http="$p" "http://127.0.0.1:$p"
  else echo "would: tailscale serve --bg --http=$p http://127.0.0.1:$p"; fi
done
