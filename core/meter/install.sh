#!/usr/bin/env bash
# Install SkippyCore's token meter (core/meter/meter.py) as a systemd service. Idempotent.
#   sudo bash core/meter/install.sh            # dry run
#   sudo bash core/meter/install.sh --apply
set -euo pipefail
APPLY=0; [[ "${1:-}" == "--apply" ]] && APPLY=1
HERE="$(cd "$(dirname "$0")" && pwd)"
do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }

do_ install -d -m 0755 /opt/skippy-core/meter /var/lib/skippy-core/meter
do_ install -m 0755 "$HERE/meter.py" /opt/skippy-core/meter/meter.py
do_ install -m 0644 "$HERE/savings.py" /opt/skippy-core/meter/savings.py
do_ install -m 0644 "$HERE/skippy-core-meter.service" /etc/systemd/system/
do_ systemctl daemon-reload
do_ systemctl enable skippy-core-meter.service
do_ systemctl restart skippy-core-meter.service
(( APPLY )) || echo "dry run: nothing changed. Re-run with --apply."
