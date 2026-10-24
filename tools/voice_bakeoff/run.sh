#!/usr/bin/env bash
# Voice bake-off on skippy-pc's GPU: build each model's image, then render lines.json with each, one at a time.
#   bash tools/voice_bakeoff/run.sh [build|bench|all] [model ...]      (models: qwen3 chatterbox f5 xtts; default all)
# Needs state/voice-bakeoff/ref/{ref.wav,ref.txt} (the reference: see docs/voice-bakeoff.md). Output: state/voice-bakeoff/out/<model>/.
set -euo pipefail
export MSYS_NO_PATHCONV=1
HERE="$(cygpath -m "$(cd "$(dirname "$0")" && pwd)")"
VB="${VB:-D:/Aaron/development/SkippyTel/state/voice-bakeoff}"
W=D:/models/huggingface
declare -A PIP=([qwen3]="qwen-tts==0.1.1" [chatterbox]="chatterbox-tts==0.1.7" [f5]="f5-tts==1.1.22" [xtts]="coqui-tts==0.28.0")
declare -A WEIGHTS=([qwen3]="$W/Qwen__Qwen3-TTS-12Hz-1.7B-Base" [chatterbox]="$W/ResembleAI__chatterbox" [f5]="$W/SWivid__F5-TTS" [xtts]="$W/coqui__XTTS-v2")
what="${1:-all}"; shift || true
models=("${@:-qwen3 chatterbox f5 xtts}"); models=(${models[*]})

if [[ $what == build || $what == all ]]; then
  for m in "${models[@]}"; do
    vocos=0; [[ $m == f5 ]] && vocos=1
    docker build --build-arg PIP="${PIP[$m]}" --build-arg VOCOS=$vocos -t "voice-bakeoff-$m" "$HERE"
  done
fi
if [[ $what == bench || $what == all ]]; then
  for m in "${models[@]}"; do
    docker run --rm --gpus all --network none -v "${WEIGHTS[$m]}:/w:ro" -v "$VB:/vb" "voice-bakeoff-$m" "$m" 2>&1 | tee "$VB/bench-$m.log"
  done
fi
