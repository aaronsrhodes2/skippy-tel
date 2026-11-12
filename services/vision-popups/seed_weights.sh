#!/usr/bin/env bash
# Copy the pinned weights vision-popups uses from the models archive (D:/models/huggingface) into its Docker volume, streamed
# as tar from the host: Docker reads D: at ~8 MB/s and starves under load, a pipe doesn't. Idempotent: a folder already seeded
# (models.py's marker) is skipped. The service seeds a missing folder itself on first use, slowly, so this is the fast path.
#   bash services/vision-popups/seed_weights.sh [folder ...]
set -euo pipefail
export MSYS_NO_PATHCONV=1
ARCHIVE="${MODELS_ROOT:-/d/models}/huggingface"
VOLUME="${VISION_POPUPS_VOLUME:-skippy-popups-vision_vision-popups-weights}"
IMAGE="${VISION_POPUPS_IMAGE:-skippy-popups-vision-vision-popups}"
here="$(cd "$(dirname "$0")" && pwd)"
folders=("$@")
if [ ${#folders[@]} -eq 0 ]; then
  mapfile -t folders < <(grep -o 'Model("[^"]*", "[^"]*"' "$here/models.py" | sed 's/.*, "\(.*\)"/\1/')
fi
docker volume create "$VOLUME" >/dev/null
for f in "${folders[@]}"; do
  if docker run --rm -v "$VOLUME:/weights" --entrypoint test "$IMAGE" -f "/weights/$f/.vision-popups-seeded"; then
    echo "$f: already seeded"; continue
  fi
  [ -d "$ARCHIVE/$f" ] || { echo "$f: not in the archive (python tools/models_archive.py hf <repo>)"; continue; }
  start=$(date +%s)
  tar -C "$ARCHIVE" --exclude="$f/.cache" --exclude="$f/assets" -cf - "$f" \
    | docker run --rm -i -v "$VOLUME:/weights" --entrypoint sh "$IMAGE" -c "tar -C /weights -xf - && date +%s > /weights/$f/.vision-popups-seeded"
  echo "$f: seeded in $(( $(date +%s) - start ))s"
done
