# Mentat: sigma-ground's physics tools as a public JSON API (mentat/app.py), plus
# sigma-ground's Weapon vs. Armor game solver (mentat/wva.py; games/weapon_vs_armor is in the tarball).
# Built by skippy-core's deployer (core/cd/cd.py) from the repo root:
#   docker build -f mentat/Dockerfile --build-arg SIGMA_GROUND_REF=<sha> .
FROM python:3.12-slim

# sigma-ground at a pinned commit, from the house's own git: skippy-core's deployer (core/cd/cd.py, vendor()) puts it in
# .vendor/sigma-ground/ of the build context (`git archive` of the pin). The core library is pure Python; the MCP layer's
# dependencies are in requirements.txt.
ARG SIGMA_GROUND_REF=d92ab13dff8ce3a377840266e5d936f0703eacb3
COPY .vendor/sigma-ground/ /opt/sigma-ground/

WORKDIR /app
COPY mentat/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mentat/app.py mentat/wva.py mentat/research.py mentat/router_stage.py mentat/ready.py ./
COPY mentat/app.py mentat/wva.py mentat/research.py mentat/cascade_stage.py mentat/position_stage.py mentat/fold_stage.py mentat/ready.py ./

ENV PYTHONPATH=/opt/sigma-ground PYTHONUNBUFFERED=1 SIGMA_GROUND_REF=${SIGMA_GROUND_REF}
# Runs as a non-root user with nothing writable but /tmp.
RUN useradd --create-home mentat
USER mentat
CMD exec gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 1 --threads 4 --timeout 60 app:app
