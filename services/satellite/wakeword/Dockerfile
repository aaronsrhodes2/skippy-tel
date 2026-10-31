# The kit that trains Skippy's wake word on skippy-pc's GPU: openWakeWord's own trainer, with the versions it was written against.
# Built and run by train.sh; nothing here is part of the listener (that only needs the trained .onnx, see ../wakeword.py).
FROM pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends git wget ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*

# openWakeWord at a pinned commit (its train.py is the trainer), installed without its runtime extras (speexdsp-ns needs a compiler)
ARG OWW_COMMIT=368c03716d1e92591906a84949bc477f3a834455
RUN git clone https://github.com/dscripka/openWakeWord /opt/openwakeword && git -C /opt/openwakeword checkout $OWW_COMMIT \
 && pip install --no-cache-dir --no-deps -e /opt/openwakeword

# piper-sample-generator v2.0.0: the last release with the generate_samples.py module train.py imports; its LibriTTS-R voice (900+ speakers)
ARG PSG_COMMIT=195e3bd967d54589c2137c9de2b22ad526ba6b6f
RUN git clone https://github.com/rhasspy/piper-sample-generator /opt/piper-sample-generator && git -C /opt/piper-sample-generator checkout $PSG_COMMIT \
 && wget -q -O /opt/piper-sample-generator/models/en_US-libritts_r-medium.pt \
    https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt

# the training dependencies, as openWakeWord's automatic_model_training notebook pins them
RUN pip install --no-cache-dir "numpy<2" onnxruntime==1.16.3 onnx==1.14.1 tqdm scipy scikit-learn requests pyyaml \
    piper-phonemize==1.1.0 webrtcvad mutagen==1.47.0 torchinfo==1.8.0 torchmetrics==1.2.0 speechbrain==0.5.14 \
    audiomentations==0.33.0 torch-audiomentations==0.11.0 acoustics==0.2.6 pronouncing==0.2.0 datasets==2.14.6 \
    deep-phonemizer==0.0.19 tensorflow-cpu==2.8.1 tensorflow_probability==0.16.0 onnx_tf==1.10.0 protobuf==3.20.3

WORKDIR /work
