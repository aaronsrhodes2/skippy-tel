# search_memory (app.py): bge-m3 + bge-reranker-v2-m3 on skippy-pc's card, an index in SkippyTel/state/search-popups. Built from the
# repo root (compose.yml) because it reads the Skippy Memory archive's transcripts with the archive's own parser (memory/); what goes
# into the context is search.Dockerfile.dockerignore's list, not the root .dockerignore. The weights are NOT in the image: they are in
# the Docker volume seed_weights.py fills. Same base as tts-studio, so Docker reuses its layers.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime
ENV PIP_NO_CACHE_DIR=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONUNBUFFERED=1 HF_HOME=/tmp/hf
RUN pip install transformers==5.5.0 tokenizers==0.22.2 safetensors==0.7.0 sentencepiece==0.2.1 accelerate==1.12.0 numpy==2.2.6 \
    && pip freeze > /pip-freeze.txt
WORKDIR /app
COPY memory/__init__.py memory/schema.py memory/store.py /app/memory/
COPY memory/parsers/ /app/memory/parsers/
COPY services/search-popups/app.py services/search-popups/corpus.py services/search-popups/index.py services/search-popups/models.py /app/
EXPOSE 47430
ENTRYPOINT ["python", "/app/app.py"]
