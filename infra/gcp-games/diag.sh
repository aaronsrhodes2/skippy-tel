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
  # Valheim (crossplay): `Session "<name>" registered with join code 106969`. The latest one wins, and the
  # early `has join code , now 0` line has no digits, so it can't match.
  vcode=$(docker logs valheim 2>&1 | grep -oE 'join code [0-9]{6}' | tail -1 | grep -oE '[0-9]{6}')
  [ -n "$vcode" ] && echo "JOIN_CODE valheim=$vcode"
  # Player join/leave lines, to learn each game's format (players-online count).
  if docker ps --format '{{.Names}}' | grep -qx dragonwilds; then
    echo "--- dragonwilds: player events"
    docker logs dragonwilds 2>&1 | grep -iE 'join|login|logout|left|disconnect|kick|player' \
      | grep -viE 'heartbeat|JoinCode|PlayerCapacity|PlayerPlatform' | tail -25 | cut -c1-300
  fi
  # ── EverQuest (AkkStack): is anything bound to the login/world ports INSIDE the container, and what the login
  # server logged. A client that "times out" at login could be a filtered port, a dead login server or a bad config;
  # the host can't tell (docker-proxy accepts on the host either way). Read-only. </dev/null on every docker exec:
  # this script arrives on bash's stdin, and a command that reads stdin would swallow the rest of it.
  EQ=akk-stack-eqemu-server-1
  if docker ps --format '{{.Names}}' | grep -qx "$EQ"; then
    echo "--- everquest: sockets bound inside the server container (hex ports: 176E=5998 176F=5999 2328=9000 1B5B=7003)"
    for proto in udp tcp; do
      echo "$proto:"
      docker exec "$EQ" sh -c "grep -E ':(176E|176F|2328|1B5B) ' /proc/net/$proto" </dev/null 2>&1 | awk '{print $2, "state", $4}' | head -12
    done
    echo "--- everquest: server processes"
    docker exec "$EQ" sh -c 'ps -eo pid,etime,args | grep -E "loginserver|world|zone|ucs|queryserv|eqlaunch|shared_memory" | grep -v grep' </dev/null 2>&1 | cut -c1-150 | head -15
    LOGS=/srv/games/data/everquest/akk-stack/server/logs
    echo "--- everquest: newest server logs"
    ls -lt --time-style=+%H:%M "$LOGS" 2>/dev/null | head -8 | cut -c1-120
    for f in $(ls -t "$LOGS"/login*.log 2>/dev/null | head -1); do echo "--- everquest: tail of $(basename "$f")"; tail -n 25 "$f" | cut -c1-200; done
    # Which login servers the world is configured for, and how the world itself talks about them (secrets hidden).
    echo "--- everquest: login-related config (keys; account/password values hidden)"
    python3 - "$LOGS/../eqemu_config.json" "$LOGS/../login.json" </dev/null 2>&1 <<'PY' | cut -c1-200 | head -30
import json, sys
def walk(node, path, out):
    for key, value in node.items():
        here = f"{path}.{key}" if path else key
        if isinstance(value, dict):
            walk(value, here, out)
        elif "login" in here.lower() or here.lower().startswith("worldservers"):
            hidden = any(w in key.lower() for w in ("pass", "account", "secret", "token"))
            out.append(f"{here} = {'<hidden>' if hidden else value!r}")
for name in sys.argv[1:]:
    try:
        rows = []
        walk(json.load(open(name)), "", rows)
        print(f"[{name.rsplit('/', 1)[-1]}]", *rows, sep="\n  ")
    except Exception as exc:
        print(f"[{name.rsplit('/', 1)[-1]}] unreadable: {exc}")
PY
    for f in $(ls -t "$LOGS"/world_*.log 2>/dev/null | head -1); do echo "--- everquest: login lines in $(basename "$f")"; grep -i -E "login|LS " "$f" | tail -n 12 | cut -c1-200; done
  fi
  # ── end everquest ──
  echo "--- udp listeners (game ports)"
  ss -ulnp 2>/dev/null | grep -E ':(2456|2457|7777|7778|8888|34197)\b' || echo "(none)"
  echo "--- memory";    free -m | head -2
  echo "--- data disk"; df -h /srv/games/data | tail -1
  echo "=== end ==="
} > /dev/ttyS1 2>&1
