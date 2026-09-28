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
  --profile valheim --profile factorio --profile satisfactory --profile dragonwilds \
  stop --timeout 120 || true

GAMES=$(meta games)
if [ -n "$GAMES" ]; then
  /usr/local/bin/skippy-discord offline "stopped" || true
fi
