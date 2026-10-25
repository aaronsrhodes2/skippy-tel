# diarize (diarize.py): sherpa-onnx speaker diarization on the CPU, then each turn through the resident whisper container. The two
# ONNX models (pyannote segmentation-3.0, WeSpeaker ResNet34; ~33 MB) are mounted read-only from D:/models/sherpa-onnx (compose.yml).
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
    && pip install sherpa-onnx==2.0.0 numpy==2.2.6 && pip freeze > /pip-freeze.txt
WORKDIR /app
COPY diarize.py /app/
EXPOSE 47431
ENTRYPOINT ["python", "/app/diarize.py"]
