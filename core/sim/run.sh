#!/usr/bin/env bash
# Run the SkippyCore simulator on this machine (any host with Docker; `make core-sim`).
#   MODELS_ROOT=/path/to/models bash core/sim/run.sh
# Downloads the stand-in models into MODELS_ROOT first if they are missing (~1.6 GB, core/llm/models.py fetch).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODELS_ROOT="${MODELS_ROOT:-$ROOT/state/core-models}"
mkdir -p "$MODELS_ROOT"
python3 "$ROOT/core/llm/models.py" check --root "$MODELS_ROOT" --full "" >/dev/null 2>&1 \
  || python3 "$ROOT/core/llm/models.py" fetch --root "$MODELS_ROOT" --full ""
docker build -q -t skippycore-sim "$ROOT/core/sim" >/dev/null
docker run --rm --privileged \
  -v "$ROOT":/src:ro -v "$MODELS_ROOT":/srv/models:ro \
  -v skippycore-sim-docker:/var/lib/docker \
  skippycore-sim
