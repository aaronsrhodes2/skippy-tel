# Mentat: sigma-ground's physics tools as a public JSON API (mentat/app.py), plus
# sigma-ground's Weapon vs. Armor game solver (mentat/wva.py; games/weapon_vs_armor is in the tarball).
# Built by .github/workflows/deploy.yml from the repo root:
#   docker build -f mentat/Dockerfile --build-arg SIGMA_GROUND_REF=<sha> .
FROM python:3.12-slim

# sigma-ground at a pinned commit (public repo, MIT). The core library is
# pure Python; the MCP layer's dependencies are in requirements.txt.
ARG SIGMA_GROUND_REF=25466d47752d1edec1f5d340ae2f59bd87ca3b6e
ADD https://github.com/aaronsrhodes2/sigma-ground/archive/${SIGMA_GROUND_REF}.tar.gz /tmp/sg.tar.gz
RUN mkdir -p /opt/sigma-ground && tar -xzf /tmp/sg.tar.gz -C /opt/sigma-ground --strip-components=1 \
    && rm /tmp/sg.tar.gz

WORKDIR /app
COPY mentat/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mentat/app.py mentat/wva.py mentat/research.py mentat/router_stage.py ./

ENV PYTHONPATH=/opt/sigma-ground PYTHONUNBUFFERED=1 SIGMA_GROUND_REF=${SIGMA_GROUND_REF}
# Runs as a non-root user with nothing writable but /tmp.
RUN useradd --create-home mentat
USER mentat
CMD exec gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 1 --threads 4 --timeout 60 app:app
