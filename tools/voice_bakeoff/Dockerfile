# One image per model, same base: docker build --build-arg PIP="<package==version>" -t voice-bakeoff-<model> tools/voice_bakeoff
# (run.sh does all four). The weights are NOT in the image: they are mounted read-only from D:\models at run time, and the
# container runs with --network none, so nothing is fetched while it speaks. The one exception is F5-TTS's vocoder (Vocos,
# charactr/vocos-mel-24khz, MIT), which D:\models doesn't carry: VOCOS=1 bakes it into the image at build time.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive PIP_NO_CACHE_DIR=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 COQUI_TOS_AGREED=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libsndfile1 sox git build-essential \
    && rm -rf /var/lib/apt/lists/*

ARG PIP
RUN pip install "$PIP" soundfile && pip freeze > /pip-freeze.txt

ARG VOCOS=0
RUN if [ "$VOCOS" = "1" ]; then HF_HUB_OFFLINE=0 python -c "from huggingface_hub import snapshot_download as s; \
    s('charactr/vocos-mel-24khz', revision='0feb3fdd929bcd6649e0e7c5a688cf7dd012ef21', local_dir='/vocos')"; fi

COPY bench.py lines.json build_page.py similarity.py /bench/
WORKDIR /bench
ENTRYPOINT ["python", "/bench/bench.py"]
