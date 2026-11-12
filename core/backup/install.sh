#!/usr/bin/env bash
# Install SkippyCore's nightly git backup (core/backup/backup.py): its own copy in /opt/skippy-core/backup, and the timer (03:30).
# It signs in to Google through core/cd/oidc.py (install core/cd first). Idempotent.
#   sudo bash core/backup/install.sh            # dry run
#   sudo bash core/backup/install.sh --apply
set -euo pipefail
APPLY=0; [[ "${1:-}" == "--apply" ]] && APPLY=1
HERE="$(cd "$(dirname "$0")" && pwd)"
do_() { if (( APPLY )); then echo "+ $*"; "$@"; else echo "would: $*"; fi; }

[[ -f /opt/skippy-core/cd/oidc.py ]] || { echo "install core/cd first (bash core/cd/install.sh --apply): the backup signs in with its oidc.py"; exit 1; }
do_ install -d -m 0755 /opt/skippy-core/backup /var/lib/skippy-core/backup
do_ install -m 0755 "$HERE/backup.py" /opt/skippy-core/backup/backup.py
do_ install -m 0644 "$HERE/skippy-core-backup.service" "$HERE/skippy-core-backup.timer" /etc/systemd/system/
do_ systemctl daemon-reload
do_ systemctl enable --now skippy-core-backup.timer
