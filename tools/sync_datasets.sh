#!/usr/bin/env bash
# D:\datasets -> skippy-core:/srv/datasets over the LAN (both on the same gigabit switch), incremental: re-run it any time.
#   bash tools/sync_datasets.sh            # dry run: what would change
#   bash tools/sync_datasets.sh --apply    # copy it (the first run moves everything; later runs only what changed)
# rsync runs in WSL (Git Bash has none) and rides on Windows' own ssh.exe, whose key skippy-core already trusts, so no key is
# added anywhere. --delete is never used: a file removed here stays on skippy-core until someone removes it there.
set -euo pipefail
CORE_LAN="${CORE_LAN:-192.168.0.242}"     # skippy-core's eno1 (the tailnet would also work, but through WireGuard)
SRC=/mnt/d/datasets/
DEST="aaron@${CORE_LAN}:/srv/datasets/"
DRY=--dry-run
[[ "${1:-}" == "--apply" ]] && DRY=
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -- rsync -a --partial --info=progress2,stats1 $DRY \
  -e "/mnt/c/Windows/System32/OpenSSH/ssh.exe -o HostKeyAlias=skippy-core" "$SRC" "$DEST"
