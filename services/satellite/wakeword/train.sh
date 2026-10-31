#!/usr/bin/env bash
# Train Skippy's wake word on skippy-pc's GPU (Docker Desktop with the NVIDIA runtime). First run: ~20 GB of downloads and a few hours.
#   bash services/satellite/wakeword/train.sh
# Output: state/wakeword/skippy.onnx (git-ignored), which ear.py and hub.py pick up on their next start. Details: README.md beside this.
set -euo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$KIT/../../.." && pwd)"
STATE="$ROOT/state/wakeword"
mkdir -p "$STATE"
to_host() { if command -v cygpath > /dev/null; then cygpath -w "$1"; else echo "$1"; fi; }   # Git Bash on Windows: Docker wants D:\...
export MSYS_NO_PATHCONV=1
docker build -t skippy-wakeword "$(to_host "$KIT")"
docker run --rm --gpus all -v skippy-wake-train:/fast -v "$(to_host "$STATE"):/work" -v "$(to_host "$KIT"):/kit:ro" skippy-wakeword bash /kit/inside.sh
