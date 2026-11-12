# vision-popups: the vision and AR-development pop-ups on skippy-pc's GPU (app.py, tools.py). Its own runtime: PyTorch 2.14.1 +
# CUDA 13.0 and the pinned stack in requirements.txt; nothing from any other project. Weights come from the models archive
# (models.py), never the network: HF_HUB_OFFLINE.
FROM pytorch/pytorch:2.14.1-cuda13.0-cudnn9-runtime
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_BREAK_SYSTEM_PACKAGES=1 HF_HOME=/cache/huggingface \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TORCH_HOME=/cache/torch
WORKDIR /popups
COPY requirements.txt /popups/requirements.txt
RUN pip install -r /popups/requirements.txt
COPY imagekit.py models.py tools.py app.py bench.py /popups/
EXPOSE 8004
ENTRYPOINT ["python", "/popups/app.py"]
