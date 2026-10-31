# The car as a guard (fleet.py): Sentry's state from our Fleet Telemetry server, and signed Fleet API commands through Tesla's own
# tesla-http-proxy, built from its pinned release (the same one car-ble's tesla-control comes from). Its door is 7353.
FROM golang:1.23 AS build
ARG VEHICLE_COMMAND=v0.4.1
RUN git clone -q --depth 1 --branch ${VEHICLE_COMMAND} https://github.com/teslamotors/vehicle-command /src \
 && cd /src && CGO_ENABLED=0 go build -o /out/tesla-http-proxy ./cmd/tesla-http-proxy

FROM python:3.12-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends openssl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=build /out/tesla-http-proxy /usr/local/bin/tesla-http-proxy
COPY ready.py fleet.py ./
EXPOSE 7353
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7353/sentry', timeout=2)"
CMD ["python", "-u", "fleet.py"]
