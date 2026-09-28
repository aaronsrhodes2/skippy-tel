#!/usr/bin/env bash
# Set the portal's secrets in Secret Manager. Input is VISIBLE (it's your own
# Cloud Shell) and each value is format-checked and read back before saving.
#
#   bash infra/bootstrap/secrets.sh            # ask only for secrets not set yet
#   bash infra/bootstrap/secrets.sh google     # re-enter the Google sign-in pair
#   bash infra/bootstrap/secrets.sh anthropic  # re-enter the Anthropic key
#   bash infra/bootstrap/secrets.sh discord    # re-enter the Discord bot token
#
# After changing a secret, tell Claude: the next deploy picks it up.
set -euo pipefail

PROJECT="${PROJECT:-skippy-tel}"
gcloud config set project "$PROJECT" >/dev/null 2>&1

RESET="${1:-}"
case "$RESET" in ""|google|anthropic|discord) ;; *)
  echo "usage: $0 [google|anthropic|discord]"; exit 2 ;;
esac

has_version() { gcloud secrets versions list "$1" --filter="state=ENABLED" --format="value(name)" --limit=1 2>/dev/null | grep -q .; }
put() { printf '%s' "$2" | gcloud secrets versions add "$1" --data-file=- >/dev/null; }
preview() { local v="$1"; [ ${#v} -le 12 ] && echo "$v" || echo "${v:0:8}…${v: -6} (${#v} chars)"; }

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
  '^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$' "123456789012-abc123….apps.googleusercontent.com"
ask portal-google-client-secret google "OAuth Client secret" required \
  '^GOCSPX-[A-Za-z0-9_-]+$' "GOCSPX-…"
ask anthropic-api-key  anthropic "Anthropic API key (enables Skippy chat)" optional \
  '^sk-ant-[A-Za-z0-9_-]+$' "sk-ant-…"
ask discord-bot-token  discord   "Discord bot token (enables Discord setup from the portal)" optional \
  '^[A-Za-z0-9_.-]{50,}$' "a long token with two dots in it"

echo
echo "Secrets done."
