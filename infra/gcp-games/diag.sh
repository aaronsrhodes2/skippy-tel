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
  # >>> readiness (the patterns mirror portal/readiness.py; portal/tests/test_readiness_diag.py keeps them in sync and runs this block)
  # `READY <game>=<0|1>`: is the game joinable, read from its own log (the WHOLE log, not the 40 lines above). 1 = its
  # "joinable" line came after its latest "starting up" line; 0 = it is starting, or its container isn't running;
  # no line = nothing recognisable in the log, and the portal falls back to its boot timer. Rules and the evidence for
  # each pattern: portal/readiness.py. Patterns are POSIX ERE with no backslashes and no {n} (mawk reads them too).
  echo "--- readiness"
  scan_ready() {   # the log on stdin; $1 game, $2 boot pattern, $3 ready pattern
    BOOT="$2" READY="$3" awk -v game="$1" '$0 ~ ENVIRON["BOOT"] { s = "0" } $0 ~ ENVIRON["READY"] { s = "1" }
      END { if (s != "") print "READY " game "=" s }'
  }
  ready_container() {   # $1 = the game = its container's name, $2 boot pattern, $3 ready pattern
    rs=$(docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null) || return 0     # not started this boot: no line
    if [ "$rs" = running ]; then docker logs "$1" 2>&1 | scan_ready "$1" "$2" "$3"; else echo "READY $1=0"; fi
  }
  ready_container valheim 'supervisord started with pid|valheim-server INFO - Running Valheim Server' 'Opened Steam server|join code [0-9]+ and IP .* is active'
  ready_container factorio 'Factorio [0-9.]+ [(]build [0-9]+' 'changing state from[(]CreatingGame[)] to[(]InGame[)]'
  ready_container satisfactory 'LogInit: Engine Version:' 'IpNetDriver listening on port 7777'
  ready_container dragonwilds 'LogInit: Engine Version:' 'JoinCode.*written with key.*value.[^]]'
  # EverQuest isn't a container of ours: everquest.sh reports its progress into the startup script's output (the journal).
  case " $(curl -sf -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/attributes/games 2>/dev/null) " in
    *" everquest "*) journalctl -u google-startup-scripts.service -b -o cat --no-pager 2>/dev/null \
                       | scan_ready everquest '=== skippy-games boot' 'everquest: online at' ;;
  esac
  # <<< readiness
  # ── game facts for the portal's admin-only Server admin card (game_facts.py, from metadata `game-facts`) ──
  # VERSION / IMAGE / UPDATED / STARTED / MODS / MODUPDATE lines per running game, parsed by portal/diagnostics.py
  # game_facts(). Read-only, no settings or secrets; a missing script or a failure only means no lines.
  echo "--- game facts"
  curl -sf -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/game-facts" 2>/dev/null \
    | timeout 60 python3 - 2>/dev/null || true
  # ── end game facts ──
  echo "--- udp listeners (game ports)"
  ss -ulnp 2>/dev/null | grep -E ':(2456|2457|7777|7778|8888|34197)\b' || echo "(none)"
  echo "--- memory";    free -m | head -2
  echo "--- data disk"; df -h /srv/games/data | tail -1
  # ── idle watchdog (why the VM does, or doesn't, go to sleep) ─────────────────────────────────────────────────────
  # Read-only apart from two counting-only iptables rules (no target, no effect on traffic) that `report` adds if
  # missing. </dev/null on everything: this script arrives on bash's stdin, and a command that reads stdin would
  # swallow the rest of it. Nothing here prints a setting or a secret.
  echo "--- idle watchdog: timer and service"
  echo "timer: $(systemctl is-active skippy-idle-watchdog.timer </dev/null 2>&1)"
  systemctl list-timers skippy-idle-watchdog.timer --no-pager </dev/null 2>&1 | head -3
  systemctl show skippy-idle-watchdog.service -p ActiveState,Result,ExecMainStatus,ExecMainExitTimestamp --no-pager </dev/null 2>&1
  echo "installed: $(head -1 /usr/local/bin/skippy-idle-watchdog 2>&1 | cut -c1-60) ($(wc -c < /usr/local/bin/skippy-idle-watchdog 2>/dev/null) bytes)"
  echo "--- idle watchdog: journal, tag skippy-idle (its own log lines; empty = it never got as far as counting)"
  journalctl -t skippy-idle -n 20 --no-pager </dev/null 2>&1 | cut -c1-300
  echo "--- idle watchdog: journal, the service unit (its errors)"
  journalctl -u skippy-idle-watchdog.service -n 8 --no-pager </dev/null 2>&1 | cut -c1-300
  echo "--- idle watchdog: /run/skippy-games and the iptables counters"
  for f in packets idle; do echo "$f=$(cat /run/skippy-games/$f 2>&1)"; done
  iptables -w 5 -t mangle -L PREROUTING -v -x -n </dev/null 2>&1 | cut -c1-220 | head -8
  echo "--- idle watchdog: evidence (idle_watchdog.py report, run from metadata)"
  wd=/run/skippy-idle-report.py
  if curl -sf -H "Metadata-Flavor: Google" \
       "http://metadata.google.internal/computeMetadata/v1/instance/attributes/idle-watchdog" -o "$wd" \
     && head -1 "$wd" | grep -q python; then
    timeout 90 python3 "$wd" report </dev/null 2>&1 | tail -30 | cut -c1-300
  else
    echo "(metadata idle-watchdog is not the Python watchdog yet)"
  fi
  # ── end idle watchdog ────────────────────────────────────────────────────────────────────────────────────────────
  echo "=== end ==="
} > /dev/ttyS1 2>&1
