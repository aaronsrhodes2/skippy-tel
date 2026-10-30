#!/usr/bin/env bash
# Copy music-studio's weights from the models archive (D:/models) into its cache volume (/app/.cache/weights in the container), once.
#
# Docker Desktop reads D: at ~8 MB/s; nothing stays warm any more (music_idle.py ends the server after each song), so every song
# would reload ~10 GB from D:. Here the HOST reads D: and streams a tar into the volume (ext4 on the C: SSD); the entrypoint links
# these copies over the D:/models mount. acestep-v15-sft (the quality DiT) is only here, not in the mounted Ace-Step1.5 folder.
#
# Idempotent: a folder whose byte count already matches is skipped.
#     bash services/music-studio/seed_weights.sh            # MODELS_ROOT=/d/models, VOLUME=skippytel_music-studio-cache
set -euo pipefail
export MSYS_NO_PATHCONV=1                       # Git Bash must not rewrite the container's /w into a Windows path

ROOT="${MODELS_ROOT:-/d/models}/huggingface"
VOLUME="${VOLUME:-skippytel_music-studio-cache}"
HELPER="${HELPER_IMAGE:-alpine:3.20}"
# "<archive folder>/<sub folder>:<name in the checkpoints dir>"
ITEMS=(
  "ACE-Step__acestep-v15-sft/.:acestep-v15-sft"
  "ACE-Step__Ace-Step1.5/acestep-5Hz-lm-1.7B:acestep-5Hz-lm-1.7B"
  "ACE-Step__Ace-Step1.5/Qwen3-Embedding-0.6B:Qwen3-Embedding-0.6B"
  "ACE-Step__Ace-Step1.5/vae:vae"
)

docker volume create "$VOLUME" >/dev/null
for item in "${ITEMS[@]}"; do
  src="$ROOT/${item%%:*}"
  name="${item##*:}"
  if [ ! -d "$src" ]; then
    echo "skip  $name (not in the archive)"
    continue
  fi
  want="$(find "$src/" -path '*/.cache' -prune -o -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}')"
  have="$(docker run --rm -v "$VOLUME:/w" "$HELPER" sh -c "cat /w/weights/$name/.seeded 2>/dev/null || echo 0")"
  if [ "$want" = "$have" ]; then
    echo "ok    $name ($((want / 1048576)) MB)"
    continue
  fi
  echo "copy  $name ($((want / 1048576)) MB)..."
  started=$SECONDS
  tar -C "$src" --exclude=.cache -cf - . \
    | docker run --rm -i -v "$VOLUME:/w" "$HELPER" sh -c "rm -rf /w/weights/$name.part && mkdir -p /w/weights/$name.part && \
        tar -C /w/weights/$name.part -xf - && rm -rf /w/weights/$name && mv /w/weights/$name.part /w/weights/$name && \
        echo $want > /w/weights/$name/.seeded"
  echo "      done in $((SECONDS - started)) s"
done
