#!/usr/bin/env bash
# Give wa-trickle ONLY the Wolfram|Alpha AppID (not the whole vault).
#   bash services/wa-trickle/setup.sh            # writes state/wa-trickle/wolfram.env (one line), never prints the value
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
VAULT="${VAULT_ENV:-G:/My Drive/vault/.env}"
DEST="${WA_TRICKLE_STATE:-./state/wa-trickle}/wolfram.env"
mkdir -p "$(dirname "$DEST")"
line="$(grep -m1 '^WOLFRAM_ALPHA_APP_ID=.' "$VAULT" || true)"
[[ -n "$line" ]] || { echo "no WOLFRAM_ALPHA_APP_ID in $VAULT" >&2; exit 1; }
printf '%s\n' "$line" > "$DEST"
echo "wrote $DEST ($(wc -c < "$DEST") bytes)"
