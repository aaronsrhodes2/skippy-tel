#!/usr/bin/env bash
# Copy this Mac's (or Linux box's) Claude Code history into Skippy Memory on
# Google Drive. One-way (machine -> Drive), newer files only.
#
#   bash scripts/claude-history-sync.sh              # sync now
#   bash scripts/claude-history-sync.sh --install    # every hour (macOS launchd)
#   bash scripts/claude-history-sync.sh --uninstall
#
# Lands in <Skippy Memory>/claude-code/<hostname>/, which
# `python -m memory inbox` turns into searchable Markdown (docs/memory/README.md).
set -euo pipefail

LABEL=local.skippy.claude-history
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

find_memory() {
  if [ -n "${SKIPPY_MEMORY_DIR:-}" ]; then echo "$SKIPPY_MEMORY_DIR"; return; fi
  # Google Drive for Desktop on macOS: ~/Library/CloudStorage/GoogleDrive-<account>/My Drive
  local d
  for d in "$HOME"/Library/CloudStorage/GoogleDrive-*/"My Drive"; do
    [ -d "$d" ] && { echo "$d/Skippy Memory"; return; }
  done
}

case "${1:-}" in
  --uninstall)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"; echo "Removed $LABEL."; exit 0 ;;
  --install)
    mkdir -p "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$SELF</string></array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>/tmp/$LABEL.log</string>
</dict></plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "Installed $LABEL (hourly; log: /tmp/$LABEL.log)."; exit 0 ;;
  "") ;;
  *) echo "usage: $0 [--install|--uninstall]"; exit 2 ;;
esac

SRC="$HOME/.claude/projects"
MEMORY="$(find_memory)"
[ -d "$SRC" ] || { echo "No Claude Code history at $SRC yet."; exit 0; }
[ -n "$MEMORY" ] && [ -d "$MEMORY" ] || { echo "Skippy Memory folder not found (is Google Drive running? set SKIPPY_MEMORY_DIR)" >&2; exit 1; }
DEST="$MEMORY/claude-code/$(hostname -s)"
mkdir -p "$DEST"
if command -v rsync >/dev/null; then   # macOS ships it
  rsync -a --update --include='*/' --include='*.jsonl' --exclude='*' "$SRC/" "$DEST/"
else                                   # Linux without rsync: GNU cp --update
  (cd "$SRC" && find . -name '*.jsonl' -exec cp --parents --update=older -p {} "$DEST/" \;)
fi
echo "Synced $SRC -> $DEST"
