# The car's charging over Bluetooth (ble.py): Tesla's tesla-control, built from its pinned release, behind a tiny HTTP door on 7352.
FROM golang:1.23 AS build
ARG VEHICLE_COMMAND=v0.4.1
RUN git clone -q --depth 1 --branch ${VEHICLE_COMMAND} https://github.com/teslamotors/vehicle-command /src \
 && cd /src && CGO_ENABLED=0 go build -o /out/tesla-control ./cmd/tesla-control

FROM python:3.12-slim
WORKDIR /app
COPY --from=build /out/tesla-control /usr/local/bin/tesla-control
COPY ready.py ble.py ./
EXPOSE 7352
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7352/health', timeout=2)"
CMD ["python", "-u", "ble.py"]
