# tts-studio: Skippy chat's local voices (app.py): Kokoro-82M (the stock voice) and F5-TTS v1 (the Skippy clone) on skippy-pc's GPU.
# Same base and apt layer as tools/voice_bakeoff/Dockerfile (the bake-off that picked F5), so Docker reuses those layers. The
# weights are NOT in the image: compose mounts them read-only from D:/models, and the container runs on an internal network with
# HF_HUB_OFFLINE. Two small things D:/models doesn't carry are baked in at build time: F5's vocoder (Vocos, MIT, pinned revision)
# and spaCy's small English model (Kokoro's G2P, misaki, would otherwise try to download it on the first line).
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 COQUI_TOS_AGREED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 sox git build-essential \
    && rm -rf /var/lib/apt/lists/*

# Resolved 2026-10-23 (the image's /pip-freeze.txt has all): torch 2.6.0+cu124, transformers 5.20.2, misaki 0.9.4, spacy 3.8.16,
# soundfile 0.14.0 (libsndfile 1.2.2: its MP3 writer encodes the chunks).
RUN pip install f5-tts==1.1.22 kokoro==0.9.4 flask==3.1.3 soundfile \
    && python -m spacy download en_core_web_sm && pip freeze > /pip-freeze.txt
RUN HF_HUB_OFFLINE=0 python -c "from huggingface_hub import snapshot_download as s; \
    s('charactr/vocos-mel-24khz', revision='0feb3fdd929bcd6649e0e7c5a688cf7dd012ef21', local_dir='/vocos')"

WORKDIR /studio
COPY app.py spoken_numbers.py /studio/
ENV PYTHONUNBUFFERED=1 HF_HOME=/tmp/hf
EXPOSE 8003
ENTRYPOINT ["python", "/studio/app.py"]
