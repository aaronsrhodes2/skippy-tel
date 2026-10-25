# audio-popups: the Captain's production helpers (app.py) on skippy-pc's GPU: Stable Audio Open Small + 1.0, MusicGen
# stereo-melody, Demucs v4 and Basic Pitch. Python 3.10 because stable-audio-tools 0.0.20 requires it (and pins torch 2.7.1).
# The weights are NOT in the image: compose mounts the audio-popups-weights volume read-only (seeded from D:/models by
# seed_weights.sh), and the container runs with HF_HUB_OFFLINE.
FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONUNBUFFERED=1 HF_HOME=/tmp/hf
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install torch==2.7.1 torchaudio==2.7.1 --extra-index-url https://download.pytorch.org/whl/cu126
COPY requirements.txt /studio/requirements.txt
# basic-pitch without its deps: on Linux it would pull tflite-runtime / TensorFlow; we run its ONNX model on onnxruntime.
RUN pip install -r /studio/requirements.txt && pip install --no-deps basic-pitch==0.4.0 && pip freeze > /pip-freeze.txt

WORKDIR /studio
COPY app.py /studio/app.py
EXPOSE 47440
ENTRYPOINT ["python", "/studio/app.py"]
