# Built by skippy-core's deployer (core/cd/cd.py) from the REPO ROOT, so the image can
# include files shared with other parts of the repo:
#   docker build -f portal/Dockerfile .
#
# Nothing here is fetched from the internet: skippy-core's deployer (core/cd/cd.py, vendor()) puts what the build needs in .vendor/
# of the build context, from the house's own git and trunk. A build by hand needs .vendor/sigma-ground/ (sigma-ground at the pin:
# `git archive`) and .vendor/scz-web.tar.gz. Each is unpacked in a STAGE of its own and only the result is copied into the image,
# so the half-gigabyte archive is never a layer of the image (this works with the classic builder skippy-core's Docker uses).

# Experiments (portal/experiments.py): sigma-ground's Radiance viewer and its committed benchmark results, at the same pinned
# commit as the Mentat service.
FROM python:3.12-slim AS sigma
ARG SIGMA_GROUND_REF=489ec0c1254e20c482fd9ca8351d0ab6d94f7920
COPY .vendor/sigma-ground/ /vendor/sg/
RUN mkdir -p /out/benchmark \
    && cp -r /vendor/sg/sigma_ground/radiance/web /out/radiance \
    && rm -f /out/radiance/*.py \
    && cp /vendor/sg/sigma_ground/mcp/benchmark/results/*_scored.json /out/benchmark/

# Star Control Zero (portal/starcontrol.py): the game's web build, the tarball of star-control-precursors' tools/build_web.py
# --tarball kept in the trunk (the artifacts folder, star-control/<tag>/scz-web.tar.gz), at the tag and checksum pinned in
# core/cd/deploy.json: checked against the pin and unpacked here.
FROM python:3.12-slim AS game
ARG STARCONTROL_REF=
ARG STARCONTROL_SHA256=
COPY portal/starcontrol_fetch.py /tmp/starcontrol_fetch.py
COPY .vendor/ /vendor/
RUN mkdir -p /out/starcontrol_static \
    && python /tmp/starcontrol_fetch.py "${STARCONTROL_REF}" "${STARCONTROL_SHA256}" /out/starcontrol_static

FROM python:3.12-slim

WORKDIR /app
COPY portal/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Placed BEFORE the portal's own files: these depend only on the pins, so with the layer cache they are reused on almost every
# build instead of being redone after each code change.
COPY --from=sigma /out/ ./experiments_static/
COPY --from=game /out/starcontrol_static ./starcontrol_static

COPY portal/ .
# Shared with the rest of the repo — one source of truth, copied in at build:
COPY services/flask-api/app/skippy_persona.md ./skippy_persona.md
COPY infra/gcp-games/discord-notify.py ./discord_notify.py
# The pop-up registry (popups.py, docs/popups.md): core/cd/cd.py vendor_popups lays the registry files out in .vendor/popups/services/ (not through
# .dockerignore exceptions: the classic builder on skippy-core dropped every family's popups.json that way)
COPY .vendor/popups/ ./popups_src/

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
