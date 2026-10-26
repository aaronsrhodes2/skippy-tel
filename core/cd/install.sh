#!/usr/bin/env bash
# Install SkippyCore's cloud deployer (core/cd): its own copy in /opt/skippy-core/cd (so it runs whatever the box's checkout
# holds), its signing key (made once, root-only; its public half goes in infra/bootstrap/skippy-core-jwks.json), and the timer.
# Idempotent.
#   sudo bash core/cd/install.sh              # dry run
#   sudo bash core/cd/install.sh --apply      # copy and install the units, but leave the timer off
#   sudo bash core/cd/install.sh --enable     # ... and switch the timer on (the day GitHub's deploy.yml goes)
set -euo pipefail
MODE="${1:-dry}"; APPLY=0; [[ "$MODE" == --apply || "$MODE" == --enable ]] && APPLY=1
HERE="$(cd "$(dirname "$0")" && pwd)"
do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }

do_ install -d -m 0755 /opt/skippy-core/cd /var/lib/skippy-core/cd
do_ install -m 0755 "$HERE/cd.py" "$HERE/oidc.py" "$HERE/rollback_pick.py" /opt/skippy-core/cd/
do_ install -m 0644 "$HERE/deploy.json" /opt/skippy-core/cd/deploy.json
[[ -f /etc/skippy-core/gcp-oidc/signing.pem ]] || do_ python3 "$HERE/oidc.py" keygen
do_ install -m 0644 "$HERE/skippy-core-cd.service" "$HERE/skippy-core-cd.timer" /etc/systemd/system/
do_ systemctl daemon-reload
if [[ "$MODE" == --enable ]]; then do_ systemctl enable --now skippy-core-cd.timer; fi
