#!/usr/bin/env bash
# Set the portal's secrets in Secret Manager. Input is VISIBLE (it's your own
# Cloud Shell) and each value is format-checked and read back before saving.
#
#   bash infra/bootstrap/secrets.sh            # ask only for secrets not set yet
#   bash infra/bootstrap/secrets.sh google     # re-enter the Google sign-in pair
#   bash infra/bootstrap/secrets.sh anthropic  # re-enter the Anthropic key
#   bash infra/bootstrap/secrets.sh discord    # re-enter the Discord bot token
#   bash infra/bootstrap/secrets.sh --from-env ~/.env
#                                  # copy them from your vault .env, no typing
#
# After changing a secret, tell Claude: the next deploy picks it up.
set -euo pipefail

PROJECT="${PROJECT:-skippy-tel}"
gcloud config set project "$PROJECT" >/dev/null 2>&1

has_version() { gcloud secrets versions list "$1" --filter="state=ENABLED" --format="value(name)" --limit=1 2>/dev/null | grep -q .; }
put() { printf '%s' "$2" | gcloud secrets versions add "$1" --data-file=- >/dev/null; }
preview() { local v="$1"; [ ${#v} -le 12 ] && echo "$v" || echo "${v:0:8}…${v: -6} (${#v} chars)"; }

ID_RE='^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$'
SECRET_RE='^GOCSPX-[A-Za-z0-9_-]+$'
ANTHROPIC_RE='^sk-ant-[A-Za-z0-9_-]+$'
DISCORD_RE='^[A-Za-z0-9_.-]{50,}$'

# ── --from-env: vault key -> Secret Manager secret, no prompts ───────────────
if [ "${1:-}" = --from-env ]; then
  FILE="${2:?usage: $0 --from-env PATH_TO_.env}"
  [ -r "$FILE" ] || { echo "Can't read $FILE"; exit 1; }
  env_get() {  # last KEY=value wins; strips quotes, spaces, Windows line endings
    grep -E "^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=" "$FILE" | tail -n1 \
      | sed -E "s/^[^=]*=//; s/\r$//; s/^[[:space:]]*['\"]?//; s/['\"]?[[:space:]]*$//"
    true
  }
  saved=0
  while read -r key secret re; do
    v="$(env_get "$key")"
    if [ -z "$v" ]; then echo "  $key: not in $FILE (skipped)"; continue; fi
    if [[ ! "$v" =~ $re ]]; then echo "  $key: doesn't look right ($(preview "$v")) — NOT saved"; continue; fi
    put "$secret" "$v"; saved=$((saved + 1))
    echo "  $key -> $secret: saved $(preview "$v")"
  done <<EOF
SKIPPY_PORTAL_OAUTH_CLIENT_ID portal-google-client-id $ID_RE
SKIPPY_PORTAL_OAUTH_CLIENT_SECRET portal-google-client-secret $SECRET_RE
ANTHROPIC_API_KEY anthropic-api-key $ANTHROPIC_RE
DISCORD_BOT_TOKEN discord-bot-token $DISCORD_RE
EOF
  echo; echo "$saved secret(s) saved. Tell Claude to redeploy."
  exit 0
fi

RESET="${1:-}"
case "$RESET" in ""|google|anthropic|discord) ;; *)
  echo "usage: $0 [google|anthropic|discord | --from-env FILE]"; exit 2 ;;
esac

# ask NAME group "prompt" required|optional pattern "what it looks like"
ask() {
  local name="$1" group="$2" prompt="$3" need="$4" pattern="$5" looks="$6" value
  if [ "$RESET" != "$group" ] && has_version "$name"; then
    echo "  $name: already set"; return
  fi
  while true; do
    echo
    echo "  $prompt"
    echo "  (looks like: $looks)"
    [ "$need" = optional ] && echo "  (optional: press Enter to skip)"
    read -r -p "  > " value
    value="$(printf '%s' "$value" | tr -d '[:space:]')"   # stray spaces/newlines from pasting
    if [ -z "$value" ]; then
      if [ "$need" = optional ]; then value="unset"; break; fi
      echo "  This one is required."; continue
    fi
    if [[ ! "$value" =~ $pattern ]]; then
      echo "  That doesn't look right: $(preview "$value")"
      echo "  Expected: $looks"; continue
    fi
    echo "  Got: $(preview "$value")"
    break
  done
  put "$name" "$value"
  echo "  saved $name"
}

if [ -z "$RESET" ] || [ "$RESET" = google ]; then
  echo "Google sign-in: open https://console.cloud.google.com/auth/clients?project=$PROJECT"
  echo "and click your 'Skippy Portal' web client to copy these."
fi
ask portal-google-client-id     google "OAuth Client ID" required \
  "$ID_RE" "123456789012-abc123….apps.googleusercontent.com"
ask portal-google-client-secret google "OAuth Client secret" required \
  "$SECRET_RE" "GOCSPX-…"
ask anthropic-api-key  anthropic "Anthropic API key (enables Skippy chat)" optional \
  "$ANTHROPIC_RE" "sk-ant-…"
ask discord-bot-token  discord   "Discord bot token (enables Discord setup from the portal)" optional \
  "$DISCORD_RE" "a long token with two dots in it"

echo
echo "Secrets done."
