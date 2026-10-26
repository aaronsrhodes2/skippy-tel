# Built by skippy-core's deployer (core/cd/cd.py) from the REPO ROOT, so the image can
# include files shared with other parts of the repo:
#   docker build -f portal/Dockerfile .
FROM python:3.12-slim

WORKDIR /app
COPY portal/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Placed BEFORE the portal's own files: it depends only on the pinned ref, so with the layer cache (type=gha in
# core/cd/deploy.json) it is reused on almost every build instead of being re-downloaded after each code change.
# Experiments (portal/experiments.py): sigma-ground's Radiance viewer and its
# committed benchmark results, at the same pinned commit as the Mentat service.
ARG SIGMA_GROUND_REF=489ec0c1254e20c482fd9ca8351d0ab6d94f7920
ADD https://github.com/aaronsrhodes2/sigma-ground/archive/${SIGMA_GROUND_REF}.tar.gz /tmp/sg.tar.gz
RUN mkdir -p /tmp/sg experiments_static/benchmark \
    && tar -xzf /tmp/sg.tar.gz -C /tmp/sg --strip-components=1 \
    && cp -r /tmp/sg/sigma_ground/radiance/web experiments_static/radiance \
    && rm -f experiments_static/radiance/*.py \
    && cp /tmp/sg/sigma_ground/mcp/benchmark/results/*_scored.json experiments_static/benchmark/ \
    && rm -rf /tmp/sg /tmp/sg.tar.gz

# Star Control Zero (portal/starcontrol.py): the game's web build, a GitHub Release of star-control-precursors, at
# the tag and checksum pinned in core/cd/deploy.json. Also before the portal's own files, and it copies in only the one
# script it runs, so this half-gigabyte layer is rebuilt only when the pin (or that script) changes. One RUN:
# download, verify, unpack and delete, so the archive is never a layer of its own.
ARG STARCONTROL_REF=
ARG STARCONTROL_SHA256=
COPY portal/starcontrol_fetch.py /tmp/starcontrol_fetch.py
RUN python /tmp/starcontrol_fetch.py "${STARCONTROL_REF}" "${STARCONTROL_SHA256}" starcontrol_static     && rm /tmp/starcontrol_fetch.py

COPY portal/ .
# Shared with the rest of the repo — one source of truth, copied in at build:
COPY services/flask-api/app/skippy_persona.md ./skippy_persona.md
COPY infra/gcp-games/discord-notify.py ./discord_notify.py
# The pop-up registry (popups.py, docs/popups.md): only the registry files are in the build context (.dockerignore)
COPY services/ ./popups_src/services/

ENV PYTHONUNBUFFERED=1
# The build's identity, for GET /version and the footer stamp. Last on purpose: these change on every build, so
# anything placed after them is never served from the layer cache. Baked into the image (not set at deploy) so a
# rolled-back revision reports the build it really is.
ARG GIT_SHA=
ARG BUILD_TIME=
ENV GIT_SHA=$GIT_SHA BUILD_TIME=$BUILD_TIME
# Long timeout: a restart waits for the VM to stop (worlds saved) and start. 32 threads, the same as Cloud Run's --concurrency
# (core/cd/deploy.json): chat turns hold a thread for minutes (model rounds, Mentat waits), and with 8 the reporter's reports and pushes
# queued behind them until the portal thought home had gone silent.
CMD exec gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 1 --threads 32 --timeout 900 app:app
