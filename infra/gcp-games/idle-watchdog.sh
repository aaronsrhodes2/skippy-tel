#!/bin/bash
# Idle watchdog for the pop-up game VM. Run every 5 minutes by a systemd timer.
#
# Counts inbound UDP packets on the game ports with an iptables rule (every
# game here keeps a steady UDP stream while anyone is connected). If fewer
# than THRESHOLD packets arrive in an interval, that interval counts as idle;
# after `idle-minutes` of consecutive idle time the worlds are saved (graceful
# `docker compose stop`) and the VM powers off, which stops compute billing.
#
# The idle clock starts at boot, so a server nobody joins shuts itself down
# `idle-minutes` after it was started.
set -euo pipefail

INTERVAL_MIN=5
THRESHOLD=${THRESHOLD:-500}      # packets / 5 min; one connected player sends thousands
PORTS="2456:2457,34197,7777,7778,9000,5998:5999,7000:7030"   # + EverQuest (world, login, zones)
STATE=/run/skippy-games          # tmpfs: resets on every boot
ROOT=/srv/games
META=http://metadata.google.internal/computeMetadata/v1/instance/attributes

IDLE_LIMIT=$(curl -sf -H "Metadata-Flavor: Google" "$META/idle-minutes" || echo 30)
[ "$IDLE_LIMIT" = "0" ] && exit 0   # 0 = watchdog disabled for this session

mkdir -p "$STATE"
RULE=(-t mangle PREROUTING -p udp -m multiport --dports "$PORTS" -m comment --comment skippy-games)
iptables -C "${RULE[@]}" 2>/dev/null || iptables -I "${RULE[@]}"

packets=$(iptables -t mangle -L PREROUTING -v -x -n | awk '/skippy-games/ {print $1; exit}')
prev=$(cat "$STATE/packets" 2>/dev/null || echo "$packets")
idle=$(cat "$STATE/idle" 2>/dev/null || echo 0)
echo "$packets" > "$STATE/packets"

if [ $((packets - prev)) -lt "$THRESHOLD" ]; then
  idle=$((idle + INTERVAL_MIN))
else
  idle=0
fi
echo "$idle" > "$STATE/idle"
logger -t skippy-idle "udp packets +$((packets - prev)), idle ${idle}/${IDLE_LIMIT} min"

if [ "$idle" -ge "$IDLE_LIMIT" ]; then
  logger -t skippy-idle "no players for ${idle} min: saving worlds and powering off"
  docker compose --project-directory "$ROOT" \
    --profile valheim --profile factorio --profile satisfactory --profile dragonwilds \
    stop --timeout 120 || true
  /usr/local/bin/skippy-everquest stop || true
  /usr/local/bin/skippy-discord offline "nobody played for ${idle} min" || true
  systemctl poweroff
fi
