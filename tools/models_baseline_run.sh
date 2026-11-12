#!/usr/bin/env bash
# The midnight model download's body (registered by tools/install_models_baseline_task.sh): runs
# tools/models_baseline.py into D:\models and appends to state/models-baseline.log. Safe to run by hand any time.
set -u
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1
mkdir -p state
py=python; command -v python >/dev/null 2>&1 || py=py
"$py" -c "import huggingface_hub" 2>/dev/null || "$py" -m pip install --user --quiet huggingface_hub
export MODELS_ROOT="${MODELS_ROOT:-D:/models}"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') models_baseline run ===" >> state/models-baseline.log
"$py" tools/models_baseline.py run "$@" >> state/models-baseline.log 2>&1
