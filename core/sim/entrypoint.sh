#!/usr/bin/env bash
# Start the simulator's own dockerd, then run the scenario.
set -euo pipefail
# cgroup v2 nesting (moby's hack/dind): move this shell out of the root cgroup and enable every controller for the
# children, or dockerd fails with "domain controllers ... threaded mode".
if [[ -f /sys/fs/cgroup/cgroup.controllers ]]; then
  mkdir -p /sys/fs/cgroup/init
  xargs -rn1 < /sys/fs/cgroup/cgroup.procs > /sys/fs/cgroup/init/cgroup.procs 2>/dev/null || :
  sed -e 's/ / +/g' -e 's/^/+/' < /sys/fs/cgroup/cgroup.controllers > /sys/fs/cgroup/cgroup.subtree_control || :
fi
dockerd >/var/log/dockerd.log 2>&1 &
for _ in $(seq 60); do docker info >/dev/null 2>&1 && break; sleep 1; done
docker info >/dev/null 2>&1 || { echo "dockerd did not start:"; tail -50 /var/log/dockerd.log; exit 1; }
exec /sim/scenario.sh "$@"
