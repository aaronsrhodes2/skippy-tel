#!/usr/bin/env bash
# Runs INSIDE the training container (train.sh starts it): data, synthetic clips, his own recordings, augmentation, training, the model.
# /kit is this folder (read-only), /work is state/wakeword. Every step resumes: run train.sh again after a failure.
set -euo pipefail
TRAIN=/opt/openwakeword/openwakeword/train.py
python -c "import openwakeword.utils as u; u.download_models(model_names=['__features_only__'])"   # the shared melspectrogram + embedding models
python /kit/data.py
python "$TRAIN" --training_config /kit/skippy.yml --generate_clips

# The Captain's own voice (record.py), mixed in with the 50,000 synthetic voices: each real clip 20 times (augmentation makes every copy different)
POS=/work/train/skippy/positive_train
if compgen -G "/work/real/positive/*.wav" > /dev/null; then
  n=0
  for f in /work/real/positive/*.wav; do
    for i in $(seq 1 20); do cp "$f" "$POS/real_${i}_$(basename "$f")"; done
    n=$((n + 1))
  done
  echo "mixed in $n of his own recordings"
fi

# and what must NOT wake it, in his voice through the pod (Skipper, skip it, Computer ...): trains as adversarial negatives
NEG=/work/train/skippy/negative_train
if compgen -G "/work/real/negative/*.wav" > /dev/null; then
  mkdir -p "$NEG"
  for f in /work/real/negative/*.wav; do
    for i in $(seq 1 20); do cp "$f" "$NEG/real_${i}_$(basename "$f")"; done
  done
  echo "mixed in his own negatives"
fi

python "$TRAIN" --training_config /kit/skippy.yml --augment_clips
python "$TRAIN" --training_config /kit/skippy.yml --train_model
cp /work/train/skippy.onnx /work/skippy.onnx
[ -f /work/train/skippy.tflite ] && cp /work/train/skippy.tflite /work/skippy.tflite || echo "(no .tflite: a Pi satellite can use the .onnx)"
echo "done: state/wakeword/skippy.onnx"
