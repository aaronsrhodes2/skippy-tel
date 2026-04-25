# Hey Buddy — InsightFace face recognition service.
# CUDA + cuDNN base because onnxruntime-gpu needs both.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev \
        build-essential \
        libglib2.0-0 libgl1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY server.py enroll.py ./

# /data holds the SQLite face DB (mount a named volume for persistence).
# /root/.insightface holds the buffalo_l model bundle (~325MB, mount cache vol).
RUN mkdir -p /data /root/.insightface

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
