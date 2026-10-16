# The SkippyCore simulator: the box's OS (Ubuntu 26.04) with its own Docker inside, so the deployer, compose file and
# smoke test run exactly as they will on the Bosgame, just with stand-in models on CPU. core/sim/run.sh builds and runs it
# (privileged: it runs dockerd). The repo is mounted read-only at /src, the stand-in models at /srv/models.
FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends docker.io docker-compose-v2 git python3 ca-certificates curl iptables tar \
 && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh scenario.sh /sim/
RUN chmod +x /sim/*.sh
ENTRYPOINT ["/sim/entrypoint.sh"]
