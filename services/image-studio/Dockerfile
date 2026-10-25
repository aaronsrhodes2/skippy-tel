# image-studio: SDXL on skippy-pc's GPU (app.py). Its own runtime: PyTorch 2.1.2 + CUDA 12.1 and the pinned diffusers stack
# (requirements.txt); nothing from any other project.
FROM pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HOME=/cache/huggingface
WORKDIR /studio
COPY requirements.txt /studio/requirements.txt
RUN pip install -r /studio/requirements.txt
COPY app.py /studio/app.py
EXPOSE 8002
ENTRYPOINT ["python", "/studio/app.py"]
