#!/usr/bin/env bash
# Install SkippyCore's reporter (core/reporter/reporter.py): its own copy in /opt/skippy-core/reporter, and a 30-second timer. Idempotent.
# It needs SKIPPY_HOME_TOKEN and PORTAL_URL in /etc/nut/skippy-ups.env (core/install/ups.sh writes them).
#   sudo bash core/reporter/install.sh            # dry run
#   sudo bash core/reporter/install.sh --apply
set -euo pipefail
APPLY=0; [[ "${1:-}" == "--apply" ]] && APPLY=1
HERE="$(cd "$(dirname "$0")" && pwd)"
do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }

do_ install -d -m 0755 /opt/skippy-core/reporter
do_ install -m 0644 "$HERE/../quiet.py" "$HERE/../shipper.py" /opt/skippy-core/
do_ install -m 0755 "$HERE/reporter.py" /opt/skippy-core/reporter/reporter.py
do_ install -m 0644 "$HERE/skippy-core-reporter.service" "$HERE/skippy-core-reporter.timer" /etc/systemd/system/
do_ systemctl daemon-reload
do_ systemctl enable --now skippy-core-reporter.timer
(( APPLY )) || echo "dry run: nothing changed. Re-run with --apply."
