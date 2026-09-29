# Built by .github/workflows/deploy.yml from the REPO ROOT, so the image can
# include files shared with other parts of the repo:
#   docker build -f portal/Dockerfile .
FROM python:3.12-slim

WORKDIR /app
COPY portal/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY portal/ .
# Shared with the rest of the repo — one source of truth, copied in at build:
COPY services/flask-api/app/skippy_persona.md ./skippy_persona.md
COPY infra/gcp-games/discord-notify.py ./discord_notify.py

# Experiments (portal/experiments.py): sigma-ground's Radiance viewer and its
# committed benchmark results, at the same pinned commit as the Mentat service.
ARG SIGMA_GROUND_REF=a763071b4beb87129f2672624e989a39ef7a1199
ADD https://github.com/aaronsrhodes2/sigma-ground/archive/${SIGMA_GROUND_REF}.tar.gz /tmp/sg.tar.gz
RUN mkdir -p /tmp/sg experiments_static/benchmark \
    && tar -xzf /tmp/sg.tar.gz -C /tmp/sg --strip-components=1 \
    && cp -r /tmp/sg/sigma_ground/radiance/web experiments_static/radiance \
    && rm -f experiments_static/radiance/*.py \
    && cp /tmp/sg/sigma_ground/mcp/benchmark/results/*_scored.json experiments_static/benchmark/ \
    && rm -rf /tmp/sg /tmp/sg.tar.gz

ENV PYTHONUNBUFFERED=1
# Long timeout: a restart waits for the VM to stop (worlds saved) and start.
CMD exec gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 1 --threads 8 --timeout 900 app:app
