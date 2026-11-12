# image-studio: the QUICK picture tier, FLUX.2 klein 4B on skippy-pc's GPU (app.py). Its own runtime: PyTorch 2.11 for
# CUDA 12.8 and the pinned diffusers stack (requirements.txt); nothing from any other project. The weights are not in the
# image: they live in a Docker volume (seed_weights.py), because Docker reads D:/models at ~8 MB/s.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HOME=/cache/huggingface HF_HUB_OFFLINE=1
RUN pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128
WORKDIR /studio
COPY requirements.txt /studio/requirements.txt
RUN pip install -r /studio/requirements.txt
COPY app.py /studio/app.py
EXPOSE 8002
ENTRYPOINT ["python", "/studio/app.py"]
