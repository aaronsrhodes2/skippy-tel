# image-studio on skippy-core: stable-diffusion.cpp's Vulkan build (the same GPU route as the box's llama.cpp: Mesa RADV,
# no ROCm) behind a small Flask wrapper (app.py). The release is pinned by tag and sha256.
FROM ubuntu:24.04
ARG SD_TAG=master-948-228c707
ARG SD_ZIP=sd-master-228c707-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip
ARG SD_SHA256=2948aadae5d32250b883ce0f3ed36c8c45029d3f856be2fc3096f11a129e723d
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip libvulkan1 mesa-vulkan-drivers \
      libgomp1 python3 python3-flask \
 && curl -fsSL -o /tmp/sd.zip "https://github.com/leejet/stable-diffusion.cpp/releases/download/${SD_TAG}/${SD_ZIP}" \
 && echo "${SD_SHA256}  /tmp/sd.zip" | sha256sum -c - \
 && mkdir -p /opt/sd && unzip -q /tmp/sd.zip -d /opt/sd && rm /tmp/sd.zip && chmod +x /opt/sd/sd-cli \
 && apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
ENV LD_LIBRARY_PATH=/opt/sd PYTHONUNBUFFERED=1
WORKDIR /studio
COPY app.py /studio/app.py
ENTRYPOINT ["python3", "/studio/app.py"]
