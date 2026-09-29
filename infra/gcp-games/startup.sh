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
mkdir -p "$DATA"/{valheim,factorio,satisfactory,dragonwilds,everquest}
chown 845:845 "$DATA/factorio"                     # factoriotools default uid
chown 1000:1000 "$DATA/satisfactory" "$DATA/dragonwilds"

# ── 3. Compose project ────────────────────────────────────────────────────────
meta compose-file > "$ROOT/docker-compose.yml"
meta games-env > "$ROOT/.env"
[ -s "$ROOT/.env" ] || echo "WARNING: no games-env metadata; run 'scripts/games.sh env' from the PC"
chmod 600 "$ROOT/.env"
grep -q '^DATA_DIR=' "$ROOT/.env" && sed -i "s|^DATA_DIR=.*|DATA_DIR=$DATA|" "$ROOT/.env" \
  || echo "DATA_DIR=$DATA" >> "$ROOT/.env"

ALL_PROFILES="--profile valheim --profile factorio --profile satisfactory --profile dragonwilds"
compose() { docker compose --project-directory "$ROOT" "$@"; }

# ── 4. Idle watchdog ──────────────────────────────────────────────────────────
meta idle-watchdog > /usr/local/bin/skippy-idle-watchdog
meta discord-notify > /usr/local/bin/skippy-discord
chmod 755 /usr/local/bin/skippy-idle-watchdog /usr/local/bin/skippy-discord
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

# ── 5. Games ──────────────────────────────────────────────────────────────────
GAMES=$(meta games)
if [ -z "$GAMES" ]; then
  echo "metadata 'games' is empty; nothing to start"
  exit 0
fi
PROFILES=""
EVERQUEST=""
for g in $GAMES; do
  if [ "$g" = everquest ]; then EVERQUEST=1; continue; fi   # AkkStack: its own project (everquest.sh)
  PROFILES="$PROFILES --profile $g"
done
meta everquest-script > /usr/local/bin/skippy-everquest && chmod 755 /usr/local/bin/skippy-everquest

# Start clean: containers left over from a previous session (restart policy
# would bring them back) are removed; world data is on bind mounts, untouched.
compose $ALL_PROFILES down --timeout 120 || true
/usr/local/bin/skippy-everquest stop || true
if [ -n "$PROFILES" ]; then
  compose $PROFILES pull || echo "pull failed; starting with cached images"
  compose $PROFILES up -d
fi
# EverQuest last: its first boot installs for ~15-20 min and shouldn't hold the others up.
[ -n "$EVERQUEST" ] && { /usr/local/bin/skippy-everquest start || echo "everquest: start FAILED"; }
echo "started: $GAMES"
/usr/local/bin/skippy-discord online || true
