#!/usr/bin/env bash
# Once a year: a long-lived Claude token for the private Skippy's bridge, so Claude Code on skippy-pc never needs /login again.
#   bash tools/claude_token.sh
# It runs `claude setup-token` (a browser sign-in; the link also opens on a phone), then asks you to paste the token it printed. Nothing
# is echoed. The token goes into the vault .env as CLAUDE_CODE_OAUTH_TOKEN, with CLAUDE_CODE_OAUTH_TOKEN_SET=<today>, so the bridge's
# /health can warn 30 days before the year is up. The bridge reads the vault on every run: no restart needed.
set -euo pipefail
VAULT="${SKIPPY_VAULT_ENV:-G:/My Drive/vault/.env}"
[ -f "$VAULT" ] || { echo "No vault at $VAULT (set SKIPPY_VAULT_ENV)"; exit 1; }

claude setup-token
echo
read -rsp "Paste the token it printed (it won't show), then Enter: " TOKEN
echo
TOKEN="$(printf '%s' "$TOKEN" | tr -d '[:space:]')"
[ ${#TOKEN} -ge 40 ] || { echo "That doesn't look like a token (too short); nothing changed."; exit 1; }

TODAY="$(date +%F)"
TMP="$(mktemp)"
grep -v -E '^CLAUDE_CODE_OAUTH_TOKEN(_SET)?=' "$VAULT" > "$TMP" || true
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\nCLAUDE_CODE_OAUTH_TOKEN_SET=%s\n' "$TOKEN" "$TODAY" >> "$TMP"
cp "$VAULT" "$VAULT.bak-claude-token"
cat "$TMP" > "$VAULT"
rm -f "$TMP" "$VAULT.bak-claude-token"           # written: no second copy of the vault left lying in Drive
unset TOKEN
echo "Saved to the vault (dated $TODAY; good for a year)."
echo "Check: curl -s http://localhost:9337/health | python -c \"import sys,json;w=json.load(sys.stdin)['workshop'];print(w['login'],w.get('login_token_days_left'))\""
