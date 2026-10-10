#!/bin/bash
# GCE shutdown-script: runs automatically whenever this VM stops, however
# that stop was triggered — the idle watchdog's own timer, `scripts/games.sh
# down`, the portal's Stop button, or a manual `gcloud compute instances
# stop`. Same graceful-shutdown logic the idle watchdog already runs before
# it calls `systemctl poweroff` itself; this just guarantees it also runs
# for every OTHER way the VM can be told to stop, so worlds are saved and
# Discord is notified no matter who or what pulled the trigger.
set -euo pipefail

ROOT=/srv/games
META=http://metadata.google.internal/computeMetadata/v1/instance/attributes
meta() { curl -sf -H "Metadata-Flavor: Google" "$META/$1" || true; }

logger -t skippy-shutdown "VM stopping: saving worlds"
docker compose --project-directory "$ROOT" \
  --profile valheim --profile factorio --profile satisfactory --profile dragonwilds --profile garrysmod \
  stop --timeout 120 || true
/usr/local/bin/skippy-everquest stop || true

GAMES=$(meta games)
# The idle watchdog says "went to sleep: <why>" itself, before it powers the VM off (idle_watchdog.py stop_everything),
# and leaves this note when Discord took it. Posting again would ping every game channel a second time and put a bare
# "stopped" over that reason on the pinned post. Every other way the VM stops (the portal's Stop, `games.sh down`,
# gcloud) leaves no note, so this is still their one post. Only a fresh note counts: not one left by a poweroff that
# never happened.
NOTE=/run/skippy-games/offline-posted
if [ -n "$(find "$NOTE" -mmin -30 2>/dev/null)" ]; then
  logger -t skippy-shutdown "Discord already told by the idle watchdog: $(head -c 200 "$NOTE")"
elif [ -n "$GAMES" ]; then
  /usr/local/bin/skippy-discord offline "stopped" || true
fi
