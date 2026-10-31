#!/usr/bin/env bash
# Runs INSIDE the training container (train.sh starts it): data, synthetic clips, his own recordings, augmentation, training, the model.
# /kit is this folder (read-only), /work is state/wakeword. Every step resumes: run train.sh again after a failure.
set -euo pipefail
TRAIN=/opt/openwakeword/openwakeword/train.py
python -c "import openwakeword.utils as u; u.download_models(model_names=['__features_only__'])"   # the shared melspectrogram + embedding models
python /kit/data.py
python "$TRAIN" --training_config /kit/skippy.yml --generate_clips

# The Captain's own voice through the pod (wakerec.py), mixed in with the 50,000 synthetic voices: each real clip 20 times at x2..x5 (the hub gains the pod's
# quiet microphone x3 before the model hears it), and augmentation makes every copy different. His negatives (Skipper, skip it, Computer ...) the same.
STAMP=$(cat /work/real/positive/*.wav /work/real/negative/*.wav 2>/dev/null | md5sum | cut -d' ' -f1)
if [ "$STAMP" != "$(cat /fast/train/real.stamp 2>/dev/null)" ]; then      # his clips changed (or first run): mix them in again, recompute the features
  rm -f /fast/train/skippy/positive_features_train.npy /fast/train/skippy/negative_features_train.npy
  find /fast/train/skippy/positive_train /fast/train/skippy/negative_train -name 'real_*' -delete 2>/dev/null || true
  if compgen -G "/work/real/positive/*.wav" > /dev/null; then python /kit/mix_real.py /work/real/positive /fast/train/skippy/positive_train; fi
  if compgen -G "/work/real/negative/*.wav" > /dev/null; then python /kit/mix_real.py /work/real/negative /fast/train/skippy/negative_train; fi
  echo "$STAMP" > /fast/train/real.stamp
fi

python "$TRAIN" --training_config /kit/skippy.yml --augment_clips
python "$TRAIN" --training_config /kit/skippy.yml --train_model
cp /fast/train/skippy.onnx /work/skippy.onnx
[ -f /fast/train/skippy.tflite ] && cp /fast/train/skippy.tflite /work/skippy.tflite || echo "(no .tflite: a Pi satellite can use the .onnx)"
echo "done: state/wakeword/skippy.onnx"
