# popups-3d on skippy-core: Hunyuan3D-2 on ROCm PyTorch (AMD's TheRock wheels built for gfx1151, the Strix Halo iGPU; ROCm
# lives inside the wheels, nothing on the host but the in-kernel amdgpu driver) and Wan 2.2 through stable-diffusion.cpp's
# Vulkan build (the picture studio's route), behind a small Flask wrapper (app.py). Everything is pinned: the wheels by version,
# Hunyuan3D-2 by commit, sd.cpp by tag and sha256.
#
# Two stages: the first has ROCm's compilers (rocm[devel], ~15 GB) to build Hunyuan3D-2's rasterizer for gfx1151 (torch's
# hipify turns its CUDA source into HIP), then drops them; the second gets the venv without them.
ARG ROCM_INDEX=https://rocm.nightlies.amd.com/v2/gfx1151/
ARG ROCM_VERSION=7.13.0a20260513

FROM ubuntu:24.04 AS build
ARG ROCM_INDEX
ARG ROCM_VERSION
ARG HY3D_COMMIT=f8db63096c8282cb27354314d896feba5ba6ff8a
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git build-essential python3 python3-dev \
      python3-venv libatomic1 libgomp1 && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir --index-url "${ROCM_INDEX}" \
      "torch==2.10.0+rocm${ROCM_VERSION}" "torchvision==0.25.0+rocm${ROCM_VERSION}" "rocm[libraries,devel]==${ROCM_VERSION}"
COPY requirements.txt /tmp/requirements.txt
RUN /venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
RUN git clone https://github.com/Tencent-Hunyuan/Hunyuan3D-2 /opt/hy3d && cd /opt/hy3d && git checkout "${HY3D_COMMIT}" \
 && rm -rf .git assets/example_* && cd hy3dgen/texgen/custom_rasterizer \
 && ROCM_HOME="$(/venv/bin/rocm-sdk path --root)" PYTORCH_ROCM_ARCH=gfx1151 PATH="/venv/bin:${PATH}" \
      /venv/bin/pip install --no-cache-dir --no-build-isolation . \
 && cd ../differentiable_renderer && c++ -O3 -shared -std=c++11 -fPIC $(/venv/bin/python -m pybind11 --includes) \
      mesh_processor.cpp -o "mesh_processor$(python3-config --extension-suffix)" \
 && /venv/bin/pip uninstall -y rocm-sdk-devel && /venv/bin/pip cache purge || true

FROM ubuntu:24.04
ARG SD_TAG=master-948-228c707
ARG SD_ZIP=sd-master-228c707-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip
ARG SD_SHA256=2948aadae5d32250b883ce0f3ed36c8c45029d3f856be2fc3096f11a129e723d
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip python3 libatomic1 libgomp1 \
      libgl1 libopengl0 libglib2.0-0 libvulkan1 mesa-vulkan-drivers ffmpeg \
 && curl -fsSL -o /tmp/sd.zip "https://github.com/leejet/stable-diffusion.cpp/releases/download/${SD_TAG}/${SD_ZIP}" \
 && echo "${SD_SHA256}  /tmp/sd.zip" | sha256sum -c - \
 && mkdir -p /opt/sd && unzip -q /tmp/sd.zip -d /opt/sd && rm /tmp/sd.zip && chmod +x /opt/sd/sd-cli \
 && apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY --from=build /venv /venv
COPY --from=build /opt/hy3d /opt/hy3d
# SD_LIB: sd-cli's own libraries, given to it alone (app.py), so they never shadow ROCm's inside the Python worker.
# AOTriton: flash attention on gfx1151 (the shape DiT goes from 15 s a step to 0.4). HY3DGEN_MODELS: worker.py links the
# archive's <org>__<repo> folders there under the names hy3dgen looks for.
ENV SD_LIB=/opt/sd PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HOME=/tmp \
    TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1 HY3DGEN_MODELS=/tmp/hy3d-models \
    U2NET_HOME=/models/u2net WORKER_PYTHON=/venv/bin/python
WORKDIR /popups
COPY app.py worker.py /popups/
ENTRYPOINT ["/venv/bin/python", "/popups/app.py"]
