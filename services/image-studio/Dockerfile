# image-studio: SDXL on skippy-pc's GPU (app.py). Built on the Stable Diffusion runtime the Remnant project already built
# on this PC (torch 2.1.2 + CUDA 12.1, diffusers 0.27.2, Flask): no second multi-GB CUDA/PyTorch download. That image is
# remnant-silly's docker/flask-sd (build it there first if it's missing: `docker compose build flask-sd` in ../remnant-silly).
FROM remnant-silly-flask-sd:latest
WORKDIR /studio
COPY app.py /studio/app.py
ENV HF_HOME=/cache/huggingface PYTHONUNBUFFERED=1
EXPOSE 8002
ENTRYPOINT ["python", "/studio/app.py"]
