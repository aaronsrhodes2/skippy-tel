#!/usr/bin/env bash
# SkippyCore's LLM on the internet, for the cloud portal: Tailscale Funnel -> llama-swap (127.0.0.1:8080), locked by a key.
#   sudo bash funnel.sh            # dry run: says what it would do, changes nothing
#   sudo bash funnel.sh --apply    # make the key (once), then open the Funnel, but only if the port already refuses strangers
#   sudo bash funnel.sh --check    # read-only: key set, port says 401 without it, Funnel on. Exit 1 if not
#   sudo bash funnel.sh --off      # close the Funnel (the key stays)
# The key is LLM_API_KEY in /etc/skippy-core/core.env (root-only). The deployer hands it to models.py render (llama-swap's
# apiKeys), the shim and the smoke test, so a fresh key takes effect on the next deploy:
#   sudo python3 core/deployer/deployer.py forget && sudo python3 core/deployer/deployer.py run
# It is never printed. skippy-pc copies it into the vault as SKIPPY_CORE_API_KEY (core/README.md -> "The portal's door").
# The Funnel needs no firewall hole: tailscaled proxies it to localhost, and ufw keeps denying everything inbound.
set -euo pipefail
ENV_FILE="${CORE_ENV:-/etc/skippy-core/core.env}"
PORT="${FUNNEL_PORT:-443}"              # Funnel allows 443, 8443 or 10000
TARGET="http://127.0.0.1:8080"
MODE="${1:-dry}"

has_key() { grep -qE '^LLM_API_KEY=.{20,}' "$ENV_FILE" 2>/dev/null; }
# 401 for a request with a wrong key, before any model is touched (llama-swap checks the key first).
refuses() { [[ "$(curl -s -o /dev/null -w '%{http_code}' -m 10 -H 'Authorization: Bearer wrong' \
               -H 'Content-Type: application/json' -d '{"model":"chat","messages":[],"max_tokens":1}' \
               "$TARGET/v1/chat/completions")" == 401 ]]; }
funnel_on() { tailscale funnel status 2>/dev/null | grep -q "proxy $TARGET"; }

if [[ "$MODE" == "--check" ]]; then
  bad=0
  chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; bad=1; fi; }
  chk "LLM_API_KEY set in $ENV_FILE"          has_key
  chk "llm port refuses a wrong key (401)"    refuses
  chk "Funnel :$PORT -> $TARGET"              funnel_on
  exit $bad
fi

if [[ $EUID -ne 0 ]]; then echo "run it with sudo" >&2; exit 1; fi

if [[ "$MODE" == "--off" ]]; then
  tailscale funnel --https="$PORT" off && echo "Funnel :$PORT closed"
  exit 0
fi
APPLY=0; [[ "$MODE" == "--apply" ]] && APPLY=1

echo "== 1/2 the key"
if has_key; then echo "ok: LLM_API_KEY is set"
elif (( APPLY )); then
  sed -i '/^LLM_API_KEY=/d' "$ENV_FILE"
  { echo "# llama-swap's API key (core/install/funnel.sh). The vault holds the same value as SKIPPY_CORE_API_KEY."
    echo "LLM_API_KEY=sk-core-$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"; } >> "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "+ wrote a new LLM_API_KEY to $ENV_FILE (not shown). Deploy so llama-swap uses it, then run this again."
else echo "would: generate LLM_API_KEY into $ENV_FILE"; fi

echo "== 2/2 the Funnel"
if funnel_on; then echo "ok: Funnel :$PORT -> $TARGET"
elif ! refuses; then
  echo "refusing to open the Funnel: $TARGET does not answer 401 to a wrong key yet (deploy with the key first)" >&2
  (( APPLY )) && exit 1 || true
elif (( APPLY )); then
  tailscale funnel --bg --https="$PORT" "$TARGET"
  echo "+ Funnel on: https://$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')$([[ $PORT == 443 ]] || echo ":$PORT")"
else echo "would: tailscale funnel --bg --https=$PORT $TARGET"; fi
