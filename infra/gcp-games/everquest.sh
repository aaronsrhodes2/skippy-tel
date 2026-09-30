#!/bin/bash
# EverQuest (EQEmu) on the pop-up VM, via AkkStack — the same stack the
# Captain runs at home, pinned to a known commit. It isn't a profile in
# games/docker-compose.yml: AkkStack is its own compose project with its own
# installer, living entirely on the world disk under data/everquest.
#
#   everquest.sh start   first boot: clone + install (~15-20 min); later: up
#                        then: import the home database once (if the portal
#                        has one ready), point the config at this VM, launch
#   everquest.sh stop    graceful down (called by shutdown.sh + idle watchdog)
#
# Lives in instance metadata (`everquest-script`); startup.sh fetches it on
# every boot, so a deploy updates it without touching the VM.
#
# Ports (infra/gcp-games/main.tf firewall): world 9000, login 5998-5999,
# zones 7000-7030/udp, chat (UCS) 7779 — moved off AkkStack's 7778, which
# RuneScape: Dragonwilds uses.
set -euo pipefail

ACTION=${1:-start}
DATA=/srv/games/data
ED=$DATA/everquest
AKK=$ED/akk-stack
AKK_REPO=https://github.com/EQEmu/akk-stack.git
AKK_REF=6e62b54886b9a77234b638e200a9991f5bd51a69
UCS_PORT=7779
META=http://metadata.google.internal/computeMetadata/v1
meta() { curl -sf -H "Metadata-Flavor: Google" "$META/$1" || true; }
log() { echo "everquest: $*"; }
export TERM=xterm COMPOSE_HTTP_TIMEOUT=1000

# AkkStack's Makefile calls the old `docker-compose` binary and runs its
# installer through `exec` (which wants a TTY). The VM has the compose plugin
# and no TTY, so: a shim that maps to `docker compose` and adds -T to exec.
if [ ! -x /usr/local/bin/docker-compose ]; then
  cat > /usr/local/bin/docker-compose <<'SHIM'
#!/bin/bash
args=(); done_t=0
for a in "$@"; do args+=("$a"); if [ "$a" = exec ] && [ $done_t = 0 ]; then args+=(-T); done_t=1; fi; done
exec docker compose "${args[@]}"
SHIM
  chmod 755 /usr/local/bin/docker-compose
fi

dc() { (cd "$AKK" && docker compose "$@"); }

if [ "$ACTION" = stop ]; then
  [ -f "$AKK/docker-compose.yml" ] && dc down --timeout 60 || true
  exit 0
fi

command -v git >/dev/null && command -v make >/dev/null || {
  apt-get update -qq && apt-get install -y -qq git make perl
}

# ── 1. AkkStack checkout + its .env (secrets generated once, kept on the disk)
mkdir -p "$ED"
if [ ! -d "$AKK/.git" ]; then
  log "cloning AkkStack @ ${AKK_REF:0:7}"
  git clone -q "$AKK_REPO" "$AKK"
  git -C "$AKK" checkout -q "$AKK_REF"
fi
if [ ! -f "$AKK/.env" ]; then
  cp "$AKK/.env.example" "$AKK/.env"
  (cd "$AKK" && make env-scramble-secrets >/dev/null)
fi
chown -R 1000:1000 "$AKK"           # the eqemu user inside the container

# Only the game ports are published (no SSH, Spire, database or phpMyAdmin),
# and chat moves to $UCS_PORT. `!override` replaces AkkStack's port list.
cat > "$AKK/docker-compose.override.yml" <<YML
services:
  eqemu-server:
    ports: !override
      - "9000:9000/tcp"
      - "9000:9000/udp"
      - "5998-5999:5998-5999/tcp"
      - "5998-5999:5998-5999/udp"
      - "$UCS_PORT:$UCS_PORT/tcp"
      - "$UCS_PORT:$UCS_PORT/udp"
      - "7000-7030:7000-7030/udp"
  mariadb:
    ports: !reset []
YML

# ── 2. First boot: AkkStack's own installer (PEQ world, quests, maps, binaries)
if [ ! -f "$ED/.installed" ]; then
  log "first boot: installing (downloads the PEQ world, quests, maps; ~15-20 min)"
  (cd "$AKK" && make install ip-address=0.0.0.0 port-range-high=7030)
  touch "$ED/.installed"
  log "install finished"
else
  dc up -d eqemu-server mariadb
fi

set -a; . "$AKK/.env"; set +a
until dc exec -T mariadb mysqladmin status -uroot -p"$MARIADB_ROOT_PASSWORD" --silent 2>/dev/null; do sleep 2; done
sql() { dc exec -T mariadb mysql -uroot -p"$MARIADB_ROOT_PASSWORD" "$@"; }
in_server() { dc exec -T eqemu-server bash -lc "cd ~/server && $1"; }

# ── 3. The Captain's home world, once: the portal relays the database dump
#     skippy-pc uploaded, and puts a one-time token in metadata to fetch it.
TOKEN=$(meta instance/attributes/eq-import-token)
URL=$(meta instance/attributes/eq-import-url)
if [ -n "$TOKEN" ] && [ -n "$URL" ] && [ ! -f "$ED/.imported-${TOKEN:0:12}" ]; then
  log "fetching the home database"
  if curl -sfL -H "Authorization: Bearer $TOKEN" "$URL" -o "$ED/import.sql.gz" && gzip -t "$ED/import.sql.gz"; then
    # Load into a scratch database first; the live world is only replaced once
    # the whole dump went in, so a bad file can't leave an empty world.
    STAGE="${MARIADB_DATABASE}_import"
    sql -e "DROP DATABASE IF EXISTS \`$STAGE\`; CREATE DATABASE \`$STAGE\`;"
    if gunzip -c "$ED/import.sql.gz" | sed -E '/^(CREATE DATABASE|USE )/d' | sql "$STAGE"; then
      log "loaded; swapping it in (replaces this server's world)"
      RENAMES=$(sql -N -e "SET SESSION group_concat_max_len = 1000000;
        SELECT GROUP_CONCAT(CONCAT('\`$STAGE\`.\`', table_name, '\` TO \`$MARIADB_DATABASE\`.\`', table_name, '\`')
          SEPARATOR ', ') FROM information_schema.tables WHERE table_schema = '$STAGE';")
      sql -e "DROP DATABASE IF EXISTS \`$MARIADB_DATABASE\`; CREATE DATABASE \`$MARIADB_DATABASE\`;
              RENAME TABLE $RENAMES; DROP DATABASE \`$STAGE\`;"
      in_server "./bin/world database:updates && ./bin/shared_memory"   # migrate to this build
      curl -sf -X POST -H "Authorization: Bearer $TOKEN" "$URL/done" || true
      touch "$ED/.imported-${TOKEN:0:12}"
      log "home database imported"
    else
      sql -e "DROP DATABASE IF EXISTS \`$STAGE\`;" || true
      log "WARNING: the home database didn't load; keeping the current world"
    fi
  else
    log "home database not available from the portal right now; starting with the current world"
  fi
  rm -f "$ED/import.sql.gz"
fi

# ── 4. Point the server at this VM: public IP for world/chat, chat on $UCS_PORT,
#     and the world's name from the (home) login server's records.
IP=$(meta instance/network-interfaces/0/access-configs/0/external-ip)
NAMES=$(sql -N -e "SELECT long_name, short_name FROM \`$MARIADB_DATABASE\`.login_world_servers ORDER BY id LIMIT 1" 2>/dev/null || true)
python3 - "$AKK/server/eqemu_config.json" "$IP" "$UCS_PORT" "$NAMES" <<'PY'
import json, sys
path, ip, ucs_port, names = sys.argv[1:5]
cfg = json.load(open(path))
srv = cfg.setdefault("server", {})
world = srv.setdefault("world", {})
if ip:
    world["address"] = ip
for key in ("ucs", "chatserver", "mailserver"):
    if key in srv:
        srv[key]["port"] = ucs_port
        if ip:
            srv[key]["host"] = ip
if names.strip():
    long_name, short_name = names.rstrip("\n").split("\t")[:2]
    world["longname"], world["shortname"] = long_name, short_name
# The login server. AkkStack's launcher starts it only when web-admin.launcher.runLoginserver is true, and the world
# must register with it locally (127.0.0.1:5998). At home both were set by hand; only the database was carried over
# here, so on the VM nothing listened on 5998/5999 and every login failed with "a connection to your server could not
# be reached". (The home config has no server-admin credentials: the world registers unauthenticated, so none are set.)
launcher = cfg.setdefault("web-admin", {}).setdefault("launcher", {})
launcher["runLoginserver"] = True
# EXACTLY ONE login server: the local one, as at home. AkkStack's fresh config lists three (loginserver1 = a public
# server with legacy=1, loginserver2 = 127.0.0.1:5998, loginserver3 = login.projecteq.net). With two of them pointing at the
# local login server the world connected twice, each registration destroyed the other ("a server with that name already
# exists") about every 100 ms, and the world also tried to register itself with a public login server. Keep loginserver1
# (its account/password untouched), make it local and non-legacy, and drop the rest.
for key in [k for k in world if k.startswith("loginserver") and k != "loginserver1"]:
    del world[key]
ls = world.setdefault("loginserver1", {})
ls["host"] = "127.0.0.1"
ls["port"] = type(ls.get("port", "5998"))("5998")        # keep the file's own type (string or number)
ls.pop("legacy", None)
with open(path, "w") as f:          # in place: keeps the eqemu user's ownership
    json.dump(cfg, f, indent=4)
PY

# ── 5. Launch world, login server, zones and chat
in_server "./bin/spire spire:launcher stop" >/dev/null 2>&1 || true   # restart picks up config/DB
in_server "./bin/spire spire:launcher start" || log "WARNING: launcher didn't start"
# Say "online" only when it is: the login server takes a few seconds, and 5999 (hex 176F) is what clients connect to.
login_up() { dc exec -T eqemu-server sh -c "grep -qE ':176F ' /proc/net/udp" </dev/null 2>/dev/null; }
for _ in $(seq 1 30); do login_up && break; sleep 3; done
if login_up; then
  log "online at ${IP:-?}:5999 (world ${IP:-?}:9000)"
else
  log "WARNING: started, but nothing is listening on the login port 5999: client logins will fail (see the login log in server/logs)"
fi
