#!/bin/bash
# Runs as root on every boot of the pop-up game VM (GCE startup-script).
#
#   1. install Docker (first boot only)
#   2. mount the world-data disk at /srv/games/data
#   3. write docker-compose.yml + .env from instance metadata
#   4. arm the idle watchdog (fresh idle clock every boot)
#   5. start exactly the games listed in metadata `games`
set -euo pipefail
exec > >(tee -a /var/log/skippy-games.log) 2>&1
echo "=== skippy-games boot $(date -Is) ==="

META=http://metadata.google.internal/computeMetadata/v1/instance/attributes
meta() { curl -sf -H "Metadata-Flavor: Google" "$META/$1" || true; }

ROOT=/srv/games
DATA=$ROOT/data
DEVICE=/dev/disk/by-id/google-games-data

# ── 1. Docker ─────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null; then
  apt-get update
  apt-get install -y ca-certificates curl gnupg iptables
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable --now docker
fi

# ── 2. Data disk ──────────────────────────────────────────────────────────────
mkdir -p "$DATA"
if ! blkid "$DEVICE" >/dev/null 2>&1; then
  echo "formatting blank data disk"
  mkfs.ext4 -m 0 -F -E lazy_itable_init=0,lazy_journal_init=0 "$DEVICE"
fi
if ! mountpoint -q "$DATA"; then
  grep -q "$DEVICE" /etc/fstab || echo "$DEVICE $DATA ext4 defaults,nofail 0 2" >> /etc/fstab
  mount "$DATA"
fi
# Containers that run as non-root need to own their folders.
mkdir -p "$DATA"/{valheim,factorio,satisfactory,dragonwilds,everquest} "$DATA"/garrysmod/{data,addons}
chown 845:845 "$DATA/factorio"                     # factoriotools default uid
chown 1000:1000 "$DATA/satisfactory" "$DATA/dragonwilds"
chown -R 1000:1000 "$DATA/garrysmod"

# ── 3. Compose project ────────────────────────────────────────────────────────
meta compose-file > "$ROOT/docker-compose.yml"
meta games-env > "$ROOT/.env"
[ -s "$ROOT/.env" ] || echo "WARNING: no games-env metadata; run 'scripts/games.sh env' from the PC"
chmod 600 "$ROOT/.env"
grep -q '^DATA_DIR=' "$ROOT/.env" && sed -i "s|^DATA_DIR=.*|DATA_DIR=$DATA|" "$ROOT/.env" \
  || echo "DATA_DIR=$DATA" >> "$ROOT/.env"

ALL_PROFILES="--profile valheim --profile factorio --profile satisfactory --profile dragonwilds --profile garrysmod"
compose() { docker compose --project-directory "$ROOT" "$@"; }

# ── 4. Idle watchdog ──────────────────────────────────────────────────────────
meta idle-watchdog > /usr/local/bin/skippy-idle-watchdog
meta discord-notify > /usr/local/bin/skippy-discord
chmod 755 /usr/local/bin/skippy-idle-watchdog /usr/local/bin/skippy-discord
# The watchdog is idle_watchdog.py (Python, stdlib only). A metadata fetch that came back empty would leave a timer
# that fails every 5 minutes without a word (which is what the old bash version did, from its first iptables
# line), so say so on the console; then start its packet counters now rather than 5 minutes in.
head -1 /usr/local/bin/skippy-idle-watchdog | grep -q python \
  || echo "WARNING: metadata idle-watchdog is not the Python watchdog; this VM will not auto-sleep"
python3 /usr/local/bin/skippy-idle-watchdog arm || echo "WARNING: idle watchdog could not add its iptables counters"
cat > /etc/systemd/system/skippy-idle-watchdog.service <<'UNIT'
[Unit]
Description=Stop game servers and power off when nobody is playing
[Service]
Type=oneshot
ExecStart=/usr/local/bin/skippy-idle-watchdog
UNIT
cat > /etc/systemd/system/skippy-idle-watchdog.timer <<'UNIT'
[Unit]
Description=Check for players every 5 minutes
[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
UNIT
# Diagnostics for the portal's server logs: every 2 minutes a snapshot of the
# game containers goes to serial port 2 (/dev/ttyS1), which the portal and the
# `ops` workflow read through the Compute API — no SSH, no logging agent.
# The script itself lives in metadata (`skippy-diag`, from diag.sh) and is
# fetched on every run, so a deploy updates it without a reboot.
cat > /usr/local/bin/skippy-diag <<'DIAG'
#!/bin/bash
curl -sf -H "Metadata-Flavor: Google" \
  "http://metadata.google.internal/computeMetadata/v1/instance/attributes/skippy-diag" | bash
DIAG
chmod 755 /usr/local/bin/skippy-diag
cat > /etc/systemd/system/skippy-diag.service <<'UNIT'
[Unit]
Description=Snapshot game containers to serial port 2 for the portal
[Service]
Type=oneshot
ExecStart=/usr/local/bin/skippy-diag
UNIT
cat > /etc/systemd/system/skippy-diag.timer <<'UNIT'
[Unit]
Description=Game container snapshot every 2 minutes
[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
UNIT

# Ops agent: answers Claude's requests (log search, stats, per-game restart)
# sent through metadata `ops-request`, replying on serial port 3. The loop
# fetches ops_agent.py fresh from metadata (`ops-agent`) every pass, so a
# deploy updates it without a reboot.
cat > /usr/local/bin/skippy-ops <<'OPS'
#!/bin/bash
while true; do
  curl -sf -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/attributes/ops-agent" > /run/ops_agent.py \
    && python3 /run/ops_agent.py
  sleep 2
done
OPS
chmod 755 /usr/local/bin/skippy-ops
cat > /etc/systemd/system/skippy-ops.service <<'UNIT'
[Unit]
Description=Skippy ops agent (requests via metadata, replies on serial port 3)
After=docker.service
[Service]
ExecStart=/usr/local/bin/skippy-ops
Restart=always
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now skippy-idle-watchdog.timer skippy-diag.timer skippy-ops.service

# Game containers are third-party images pulled fresh each boot. None needs the
# metadata server, which holds games-env (every game's passwords and Discord
# webhooks) and one-time import tokens, so containers can't reach it; this
# host's own scripts still can (they don't pass through DOCKER-USER).
iptables -C DOCKER-USER -d 169.254.169.254 -j DROP 2>/dev/null \
  || iptables -I DOCKER-USER -d 169.254.169.254 -j DROP \
  || echo "WARNING: couldn't fence the metadata server off from containers"
# One hole in the fence: DNS. On GCE the metadata server's address is also the VM's DNS resolver, and `docker build`
# (any container on Docker's default bridge) takes its resolver from the host, so dropping everything to that address
# broke name lookups inside builds: EverQuest's install died in the PEQ-editor image with "DNS: transient error".
# Only port 53 goes through (inserted above the DROP); the metadata HTTP API on port 80 stays blocked.
for proto in udp tcp; do
  iptables -C DOCKER-USER -d 169.254.169.254 -p "$proto" --dport 53 -j ACCEPT 2>/dev/null \
    || iptables -I DOCKER-USER 1 -d 169.254.169.254 -p "$proto" --dport 53 -j ACCEPT \
    || echo "WARNING: couldn't let container DNS through to the resolver ($proto)"
done

# ── 5. Games ──────────────────────────────────────────────────────────────────
# >>> games (portal/tests/test_fix_watchdog_startup.py runs everything from here to the end of the script)
GAMES=$(meta games)
if [ -z "$GAMES" ]; then
  echo "metadata 'games' is empty; nothing to start"
  exit 0
fi
PROFILES=""
COMPOSE_GAMES=""
EVERQUEST=""
for g in $GAMES; do
  if [ "$g" = everquest ]; then EVERQUEST=1; continue; fi   # AkkStack: its own project (everquest.sh)
  PROFILES="$PROFILES --profile $g"
  COMPOSE_GAMES="$COMPOSE_GAMES $g"
done
meta everquest-script > /usr/local/bin/skippy-everquest && chmod 755 /usr/local/bin/skippy-everquest

# Start clean: containers left over from a previous session (restart policy
# would bring them back) are removed; world data is on bind mounts, untouched.
compose $ALL_PROFILES down --timeout 120 || true
/usr/local/bin/skippy-everquest stop || true
# Archive each game's flagged files (TARGETS in game_backup.py) now, with everything stopped: this is the state
# the session starts from, the point to roll back to after a bad day. Unchanged files aren't archived twice.
# Restores are driven from the ops workflow (ops_agent.py: backups, restore-game). Can't stop the boot.
meta game-backup > /usr/local/bin/skippy-game-backup
python3 /usr/local/bin/skippy-game-backup backup $GAMES || echo "game backup: FAILED for at least one game; starting anyway"
# Valheim grows its world from the seed in settings (VALHEIM_WORLD_SEED). Runs with the container removed,
# never deletes (a world with another seed is set aside on the data disk), and can't stop the boot.
case " $GAMES " in
  *" valheim "*)
    meta valheim-seed > /usr/local/bin/skippy-valheim-seed
    python3 /usr/local/bin/skippy-valheim-seed --env "$ROOT/.env" --data "$DATA" \
      || echo "valheim seed: FAILED; the world starts as it is" ;;
esac
# Garry's Mod: our mod (sigma-ground's folder stage/gmod/addon) is put into the persistent addons
# folder, fresh from the games bucket (skippy-core publishes it), before srcds starts (gmod_addon.sh: one authenticated GET). It never fails: a fetch that doesn't
# work keeps the previous copy and the game starts anyway. `ops restart-game garrysmod` runs the same script (ops_agent.py).
case " $GAMES " in
  *" garrysmod "*)
    meta gmod-addon > /usr/local/bin/skippy-gmod-addon
    DATA="$DATA" bash /usr/local/bin/skippy-gmod-addon \
      || echo "gmod addon: the refresh script itself failed; garrysmod starts with the addon as it was" ;;
esac
FAILED=""
if [ -n "$PROFILES" ]; then
  compose $PROFILES pull || echo "pull failed; starting with cached images"
  # One image that can't be pulled or doesn't exist (a FACTORIO_VERSION that is a well-formed typo, say) makes compose
  # exit 1 and create NOTHING for any game in the same command. So when the one command fails, start the games one
  # at a time: the good ones come up, and the ones that don't are named. (Never swapped for another tag: a pinned
  # version exists to keep the world and the players' clients on it, and `stable` could upgrade a save for good.)
  if ! compose $PROFILES up -d; then
    echo "compose up failed for:$COMPOSE_GAMES; starting the games one at a time"
    for g in $COMPOSE_GAMES; do
      compose --profile "$g" up -d || FAILED="$FAILED $g"
    done
    if [ -n "$FAILED" ]; then
      echo "STARTUP FAILED for:$FAILED (compose's error is above; nothing else was held back)"
    fi
  fi
fi
# EverQuest last: its first boot installs for ~15-20 min and shouldn't hold the others up.
[ -n "$EVERQUEST" ] && { /usr/local/bin/skippy-everquest start || echo "everquest: start FAILED"; }
UP=""
for g in $GAMES; do
  case " $FAILED " in *" $g "*) ;; *) UP="$UP $g" ;; esac
done
if [ -n "$UP" ]; then
  echo "started:$UP"
  /usr/local/bin/skippy-discord online || true
else
  echo "no game started; not announcing anything on Discord"
fi
