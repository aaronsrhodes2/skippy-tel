#!/usr/bin/env bash
# Pop-up game servers on GCP. Runs from the PC (Git Bash) or the Mac.
#
#   scripts/games.sh env                        push games/.env + compose file + VM scripts to the VM
#   scripts/games.sh up valheim dragonwilds     size + start the VM with those games
#   scripts/games.sh up factorio --idle 90      custom idle shutdown (minutes; 0 = never)
#   scripts/games.sh down                       save worlds + stop now
#   scripts/games.sh status                     running? which games? IP?
#   scripts/games.sh logs dragonwilds           tail a server's log
#
# Games: valheim factorio satisfactory dragonwilds garrysmod
# Needs: gcloud (logged in, project set), the VM created by infra/gcp-games.
set -euo pipefail

INSTANCE=${GAMES_INSTANCE:-skippy-games}
ZONE=${GAMES_ZONE:-us-west1-b}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$HERE/games/.env"
KNOWN="valheim factorio satisfactory dragonwilds garrysmod everquest"

# RAM each server wants (GB), used to pick the machine size.
# (case, not an associative array: macOS still ships bash 3.2)
ram_gb() {
  case $1 in
    valheim) echo 4 ;; factorio) echo 2 ;; satisfactory) echo 12 ;; dragonwilds) echo 8 ;; garrysmod) echo 4 ;; everquest) echo 6 ;;
  esac
}

die() { echo "error: $*" >&2; exit 1; }
g() { gcloud compute "$@" --zone "$ZONE"; }

envval() { # envval KEY → value from games/.env (quotes removed, inline "# comments" stripped)
  [ -f "$ENV_FILE" ] || return 0
  local v; v=$(grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2-)
  case "$v" in
    \'*\'*) v=${v#\'}; v=${v%%\'*} ;;
    \"*\"*) v=${v#\"}; v=${v%%\"*} ;;
    *) v=$(printf '%s' "$v" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//') ;;
  esac
  printf '%s\n' "$v"
}

require() { # require KEY "why"
  local v; v=$(envval "$1")
  [ -n "$v" ] && [ "$v" != "change-me" ] || die "set $1 in games/.env ($2), then run: $0 env"
}

check_env() {
  for game in "$@"; do
    case $game in
      valheim)
        require VALHEIM_PASSWORD "join password, min 5 chars"
        [ "$(envval VALHEIM_PASSWORD | wc -c)" -gt 5 ] || die "VALHEIM_PASSWORD must be at least 5 characters" ;;
      dragonwilds)
        require DRAGONWILDS_OWNER_ID "your Player ID from in-game Settings"
        require DRAGONWILDS_ADMIN_PASSWORD "server admin password" ;;
    esac
  done
}

machine_for() { # smallest E2 that fits the games + 2 GB for the OS
  local total=2
  for game in "$@"; do total=$((total + $(ram_gb "$game"))); done
  if   [ "$total" -le 6 ];  then echo e2-standard-2   # 8 GB: one light server
  elif [ "$total" -le 16 ]; then echo e2-standard-4   # 16 GB
  elif [ "$total" -le 32 ]; then echo e2-highmem-4    # 32 GB
  else                           echo e2-highmem-8    # 64 GB
  fi
}

status_of() { g instances describe "$INSTANCE" --format='value(status)'; }
ip_of() { g instances describe "$INSTANCE" --format='value(networkInterfaces[0].accessConfigs[0].natIP)'; }

join_hints() {
  local ip; ip=$(ip_of)
  echo
  echo "Server IP: $ip"
  for game in "$@"; do
    case $game in
      valheim)      echo "  Valheim       → Join IP: $ip:2456" ;;
      factorio)     echo "  Factorio      → Multiplayer → Connect to address: $ip" ;;
      satisfactory) echo "  Satisfactory  → Server Manager → Add Server: $ip (port 7777)" ;;
      garrysmod)    echo "  Garry's Mod   → game console (~): connect $ip:27015" ;;
      dragonwilds)  echo "  Dragonwilds   → Public tab → search server name \"$(envval DRAGONWILDS_SERVER_NAME)\" (Xbox/PC crossplay)" ;;
    esac
  done
  echo "  First boot downloads the servers: allow ~5-10 min before joining."
}

sync_vm() { # push secrets + the repo's current compose file and VM scripts (applied on next start)
  [ -f "$ENV_FILE" ] || die "copy games/.env.example to games/.env and fill it in first"
  local infra="$HERE/infra/gcp-games"
  g instances add-metadata "$INSTANCE" --metadata-from-file \
games-env="$ENV_FILE",compose-file="$HERE/games/docker-compose.yml",\
startup-script="$infra/startup.sh",idle-watchdog="$infra/idle_watchdog.py",discord-notify="$infra/discord-notify.py" >/dev/null
  echo "synced games/.env, compose file and VM scripts to $INSTANCE"
}

cmd=${1:-status}; shift || true
case $cmd in
  env)
    sync_vm
    ;;

  up)
    games=(); idle=30
    while [ $# -gt 0 ]; do
      case $1 in
        --idle) idle=$2; shift 2 ;;
        *) [[ " $KNOWN " == *" $1 "* ]] || die "unknown game '$1' (known: $KNOWN)"
           games+=("$1"); shift ;;
      esac
    done
    [ ${#games[@]} -gt 0 ] || die "which games? ($KNOWN)"
    check_env "${games[@]}"
    sync_vm
    machine=$(machine_for "${games[@]}")
    state=$(status_of)

    if [ "$state" = "RUNNING" ]; then
      echo "already running: switching to ${games[*]} (machine size unchanged)"
      g instances add-metadata "$INSTANCE" --metadata games="${games[*]}",idle-minutes="$idle"
      g ssh "$INSTANCE" --command "sudo google_metadata_script_runner startup"
    else
      [ "$state" = "TERMINATED" ] || die "instance is $state; wait and retry"
      current=$(g instances describe "$INSTANCE" --format='value(machineType.basename())')
      if [ "$current" != "$machine" ]; then
        echo "resizing $current → $machine"
        g instances set-machine-type "$INSTANCE" --machine-type "$machine"
      fi
      g instances add-metadata "$INSTANCE" --metadata games="${games[*]}",idle-minutes="$idle"
      g instances start "$INSTANCE"
    fi
    echo "games: ${games[*]} on $machine; auto-off after ${idle} idle minutes"
    join_hints "${games[@]}"
    ;;

  down)
    if [ "$(status_of)" = "RUNNING" ]; then
      g ssh "$INSTANCE" --command "sudo docker compose --project-directory /srv/games \
        --profile valheim --profile factorio --profile satisfactory --profile dragonwilds --profile garrysmod stop --timeout 120; \
        sudo /usr/local/bin/skippy-discord offline 'stopped by the Captain'" || true
      g instances stop "$INSTANCE"
    fi
    echo "stopped"
    ;;

  status)
    state=$(status_of)
    games=$(g instances describe "$INSTANCE" --flatten='metadata.items[]' \
      --format='value(metadata.items.key,metadata.items.value)' | awk -F'\t' '$1=="games" {print $2}')
    echo "$INSTANCE: $state  games: ${games:-none}  ip: $(ip_of)"
    [ "$state" = "RUNNING" ] && g ssh "$INSTANCE" --command \
      "sudo docker ps --format '{{.Names}}: {{.Status}}'; journalctl -t skippy-idle -n 1 --no-pager -o cat" || true
    ;;

  logs)
    [ -n "${1:-}" ] || die "which game?"
    g ssh "$INSTANCE" --command "sudo docker logs --tail 100 -f $1"
    ;;

  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
