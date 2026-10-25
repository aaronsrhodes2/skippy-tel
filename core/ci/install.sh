#!/usr/bin/env bash
# Install SkippyCore's CI (core/ci/ci.py): its own copy in /opt/skippy-core/ci (so it runs whatever the deployer's checkout holds),
# the timer, and the deployer's gate on it (CI_LOCAL in core.env). Idempotent.
#   sudo bash core/ci/install.sh            # dry run: says what it would do
#   sudo bash core/ci/install.sh --apply
set -euo pipefail
APPLY=0; [[ "${1:-}" == "--apply" ]] && APPLY=1
HERE="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE=/etc/skippy-core/core.env
do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }

do_ install -d -m 0755 /opt/skippy-core/ci /var/lib/skippy-core/ci
do_ install -m 0755 "$HERE/ci.py" /opt/skippy-core/ci/ci.py
do_ install -m 0644 "$HERE/skippy-core-ci.service" "$HERE/skippy-core-ci.timer" /etc/systemd/system/
if ! grep -q '^CI_LOCAL=' "$ENV_FILE" 2>/dev/null; then
  if (( APPLY )); then printf '\n# The deployer gates on this box'"'"'s own CI (core/ci), not GitHub (docs/no-github.md)\nCI_LOCAL=/var/lib/skippy-core/ci\n' >> "$ENV_FILE"; echo "+ CI_LOCAL in $ENV_FILE"
  else echo "would: add CI_LOCAL=/var/lib/skippy-core/ci to $ENV_FILE"; fi
fi
do_ systemctl daemon-reload
do_ systemctl enable --now skippy-core-ci.timer
