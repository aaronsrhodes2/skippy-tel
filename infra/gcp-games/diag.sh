#!/bin/bash
# Snapshot of the game containers for the portal / ops workflow, written to
# serial port 2. Fetched from instance metadata (`skippy-diag`) and run every
# 2 minutes by the skippy-diag timer (startup.sh), so edits here go live on
# the next deploy, no reboot. Keep it cheap and never print settings values.
{
  echo "=== skippy-diag $(date -Is) ==="
  docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>&1
  for c in $(docker ps -a --format '{{.Names}}' 2>/dev/null); do
    echo "--- $c: last 40 log lines"
    docker logs --tail 40 "$c" 2>&1 | tail -c 6000
  done
  # Invite codes some games generate per session (Dragonwilds: new every boot).
  # Pulled from the whole container log, since the line scrolls out of the tail.
  code=$(docker logs dragonwilds 2>&1 | grep -o 'Setting \["JoinCode"\].*value\[[^]]*\]' | tail -1 \
         | sed -E 's/.*value\[([^]]*)\]/\1/')
  [ -n "$code" ] && echo "JOIN_CODE dragonwilds=$code"
  # Player join/leave lines, to learn each game's format (players-online count).
  if docker ps --format '{{.Names}}' | grep -qx dragonwilds; then
    echo "--- dragonwilds: player events"
    docker logs dragonwilds 2>&1 | grep -iE 'join|login|logout|left|disconnect|kick|player' \
      | grep -viE 'heartbeat|JoinCode|PlayerCapacity|PlayerPlatform' | tail -25 | cut -c1-300
  fi
  echo "--- udp listeners (game ports)"
  ss -ulnp 2>/dev/null | grep -E ':(2456|2457|7777|7778|8888|34197)\b' || echo "(none)"
  echo "--- memory";    free -m | head -2
  echo "--- data disk"; df -h /srv/games/data | tail -1
  echo "=== end ==="
} > /dev/ttyS1 2>&1
