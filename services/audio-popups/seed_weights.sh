#!/usr/bin/env bash
# Copy audio-popups' weights from the models archive (D:/models) into its Docker volume, once.
#
# Why not mount D:/models directly like image-studio does: Docker Desktop reads D: (the big HDD, shared into its VM) at
# ~8 MB/s, so Stable Audio Open 1.0 alone (4.8 GB) would take ~10 minutes on every cold load. Here the HOST reads D: at disk
# speed and streams a tar into the volume (ext4 on the C: SSD); after that a load is seconds.
#
# Idempotent: a folder whose byte count already matches is skipped. Run it again after adding a model.
#     bash services/audio-popups/seed_weights.sh            # MODELS_ROOT=/d/models, VOLUME=skippytel_audio-popups-weights
set -euo pipefail
export MSYS_NO_PATHCONV=1                       # Git Bash must not rewrite the container's /w into a Windows path

ROOT="${MODELS_ROOT:-/d/models}"
VOLUME="${VOLUME:-skippytel_audio-popups-weights}"
HELPER="${HELPER_IMAGE:-alpine:3.20}"
FOLDERS=(
  huggingface/stabilityai__stable-audio-open-small
  huggingface/stabilityai__stable-audio-open-1.0
  huggingface/google-t5__t5-base
  huggingface/facebook__musicgen-stereo-melody
  huggingface/facebook__musicgen-stereo-melody-large
  direct/facebookresearch__demucs
  direct/spotify__basic-pitch
)

docker volume create "$VOLUME" >/dev/null
for rel in "${FOLDERS[@]}"; do
  src="$ROOT/$rel"
  name="$(basename "$rel")"
  if [ ! -d "$src" ]; then
    echo "skip  $name (not in the archive yet)"
    continue
  fi
  want="$(find "$src" -path '*/.cache' -prune -o -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}')"
  have="$(docker run --rm -v "$VOLUME:/w" "$HELPER" sh -c "cat /w/$name/.seeded 2>/dev/null || echo 0")"
  if [ "$want" = "$have" ]; then
    echo "ok    $name ($((want / 1048576)) MB)"
    continue
  fi
  echo "copy  $name ($((want / 1048576)) MB)..."
  started=$SECONDS
  tar -C "$(dirname "$src")" --exclude=.cache -cf - "$name" \
    | docker run --rm -i -v "$VOLUME:/w" "$HELPER" sh -c "rm -rf /w/$name.part && mkdir -p /w/$name.part && \
        tar -C /w/$name.part -xf - && rm -rf /w/$name && mv /w/$name.part/$name /w/$name && rmdir /w/$name.part && \
        echo $want > /w/$name/.seeded"
  echo "      done in $((SECONDS - started)) s"
done
