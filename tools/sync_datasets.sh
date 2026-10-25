#!/usr/bin/env bash
# D:\datasets -> skippy-core:/srv/datasets over the LAN (both on the same gigabit switch), incremental: re-run it any time.
#   bash tools/sync_datasets.sh            # dry run: what would change
#   bash tools/sync_datasets.sh --apply    # copy it (the first run moves everything; later runs only what changed)
# rsync runs in WSL (Git Bash has none) and rides on Windows' own ssh.exe, whose key skippy-core already trusts, so no key is
# added anywhere. WSL reads D: slowly (~8 MB/s against the LAN's ~90), so a FIRST copy of a big new folder is faster as a tar
# stream from Git Bash (73 MB/s, 2026-10-25):  cd /d/datasets && tar -cf - <folder> | ssh -o HostKeyAlias=skippy-core aaron@192.168.0.242 'tar -C /srv/datasets -xf -'
# then this script for the changes after (tar keeps the times, so rsync skips what already matches). --delete is never used: a file removed here stays on skippy-core until someone removes it there.
set -euo pipefail
CORE_LAN="${CORE_LAN:-192.168.0.242}"     # skippy-core's eno1 (the tailnet would also work, but through WireGuard)
SRC=/mnt/d/datasets/
DEST="aaron@${CORE_LAN}:/srv/datasets/"
DRY=--dry-run
[[ "${1:-}" == "--apply" ]] && DRY=
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -- rsync -a --partial --info=progress2,stats1 $DRY \
  -e "/mnt/c/Windows/System32/OpenSSH/ssh.exe -o HostKeyAlias=skippy-core" "$SRC" "$DEST"
