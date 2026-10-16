#!/usr/bin/env bash
# Start the simulator's own dockerd, then run the scenario.
set -euo pipefail
dockerd >/var/log/dockerd.log 2>&1 &
for _ in $(seq 60); do docker info >/dev/null 2>&1 && break; sleep 1; done
docker info >/dev/null 2>&1 || { echo "dockerd did not start:"; tail -50 /var/log/dockerd.log; exit 1; }
exec /sim/scenario.sh "$@"
