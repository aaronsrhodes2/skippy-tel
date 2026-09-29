# music-studio: ACE-Step 1.5's own REST server on skippy-pc's GPU.
#
# Called only by the home reporter, over the compose network (no published port).
# The weights are mounted read-only from the models archive
# (D:\models\huggingface\ACE-Step__Ace-Step1.5 -> /weights). Its server startup looks in
# /app/checkpoints (ACESTEP_CHECKPOINTS_DIR is honoured by only some code paths) and REWRITES the
# model's .py files there with the copies bundled at the pinned commit, so the entrypoint builds
# /app/checkpoints as a writable view: big files are symlinks into the read-only weights (they
# stay immutable), small ones (config, .py) are copies. Offline is
# ENFORCED by the network (compose puts this container on an `internal` network with no route out),
# because HF_HUB_OFFLINE alone did not stop it: on an empty folder it downloaded its own copies,
# with a ModelScope fallback. So what runs is exactly what was reviewed:
#   - code: ACE-Step-1.5 pinned to one commit, dependencies from its uv.lock (--frozen)
#   - model code: acestep/models/** at the pinned commit (that is what actually runs: it overwrites
#     the checkpoint's .py; read 2026-09-29: torch/numpy/einops/loguru + Apple-MLX only; no network,
#     subprocess, socket or pickle), and a silence_latent.pt whose pickle imports only
#     OrderedDict/FloatStorage/_rebuild_tensor_v2 (a plain tensor)
# It loads the models on the first song (ACESTEP_NO_INIT) and parks them in CPU RAM after
# (ACESTEP_OFFLOAD_TO_CPU), so it doesn't hold the 16 GB card while Ollama is chatting.
ARG CUDA_VERSION=12.8.1
FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common git curl ca-certificates libsndfile1 ffmpeg \
        build-essential libffi-dev libssl-dev \
    && add-apt-repository -y ppa:deadsnakes/ppa && apt-get update \
    && apt-get install -y --no-install-recommends python3.11 python3.11-dev python3.11-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /uvx /bin/

# Bump this (after reading the diff) to update ACE-Step.
ARG ACESTEP_REF=ca1e85fe9430179831e6bc6be790c332190a3866
RUN git clone https://github.com/ACE-Step/ACE-Step-1.5.git /app \
    && git -C /app checkout --quiet "$ACESTEP_REF" && rm -rf /app/.git
WORKDIR /app
RUN uv sync --frozen --no-dev --python python3.11

# /weights is the read-only archive; /app/checkpoints is the writable view ACE-Step expects.
COPY <<'ENTRYPOINT_EOF' /usr/local/bin/entrypoint.sh
#!/bin/sh
set -e
cd /weights
find . -path ./.cache -prune -o -type f -print | while read -r f; do
  mkdir -p "/app/checkpoints/$(dirname "$f")"
  if [ "$(stat -c %s "$f")" -gt 50000000 ]; then ln -sf "/weights/$f" "/app/checkpoints/$f"
  else cp -f "$f" "/app/checkpoints/$f"; fi
done
cd /app          # the server (and uv, which looks for the project here) must start from /app
exec "$@"
ENTRYPOINT_EOF
# a Windows checkout has CRLF line endings; a CR after #!/bin/sh makes the script un-runnable
RUN sed -i 's/\x0d$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh

ENV ACESTEP_API_HOST=0.0.0.0 \
    ACESTEP_API_PORT=8001 \
    ACESTEP_CHECKPOINTS_DIR=/app/checkpoints \
    ACESTEP_CONFIG_PATH=acestep-v15-turbo \
    ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-1.7B \
    ACESTEP_LM_BACKEND=pt \
    ACESTEP_INIT_LLM=true \
    ACESTEP_NO_INIT=true \
    ACESTEP_OFFLOAD_TO_CPU=true \
    ACESTEP_LM_OFFLOAD_TO_CPU=true \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -sf http://localhost:8001/health > /dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uv", "run", "--no-sync", "python", "-m", "acestep.api_server"]
