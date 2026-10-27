#!/usr/bin/env python3
"""The Captain's car (a Tesla Model 3) as JSON, for skippy-pc's home reporter and so the portal: GET /car, GET /health.

TeslaMate (core/compose.tesla.yml) does the talking to Tesla and logs every drive and charge into its own Postgres; it also
publishes the car's latest state to MQTT, retained, one topic per value (`teslamate/cars/<id>/<name>`). This listens to that and
keeps the last value of each, so asking never wakes the car and never costs a Tesla API call. Read only: nothing here sends the car
a command (that is the Fleet API, saved for later: docs/tesla.md).

    GET /car      {"cars": [{"id": 1, "name": ..., "state": "asleep", "battery_level": 78, ..., "updated_s": 12}], "mqtt": true}
    GET /health   the readiness contract (ready.py): ready once MQTT is connected and TeslaMate has published a car
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ready import Readiness

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
NAMESPACE = os.environ.get("MQTT_NAMESPACE", "").strip("/")
PORT = int(os.environ.get("PORT", "7351"))
SIGN_IN = os.environ.get("TESLAMATE_SIGN_IN", "http://skippy-core:4000")

# What a reader gets, and the type each value is read as (TeslaMate sends every value as text). Anything else on the topic is ignored.
NUM = ("battery_level", "usable_battery_level", "est_battery_range_km", "rated_battery_range_km", "ideal_battery_range_km",
       "charge_limit_soc", "charger_power", "charger_voltage", "charger_actual_current", "charge_energy_added",
       "time_to_full_charge", "odometer", "inside_temp", "outside_temp", "speed", "power", "latitude", "longitude", "heading",
       "tpms_pressure_fl", "tpms_pressure_fr", "tpms_pressure_rl", "tpms_pressure_rr")
BOOL = ("healthy", "plugged_in", "charge_port_door_open", "locked", "sentry_mode", "windows_open", "doors_open", "trunk_open",
        "frunk_open", "is_user_present", "is_climate_on", "is_preconditioning", "update_available")
TEXT = ("display_name", "state", "since", "model", "trim_badging", "exterior_color", "version", "update_version", "geofence",
        "shift_state", "charging_state", "scheduled_charging_start_time")


def parse(name: str, raw: str):
    raw = (raw or "").strip()
    if raw == "" or raw.lower() in ("nil", "null"):
        return None
    if name in NUM:
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return None
    if name in BOOL:
        return raw.lower() == "true"
    return raw[:120]


class Cars:
    """The latest value of each topic, per car. Fed by MQTT messages; read by the HTTP side."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._cars: dict[int, dict] = {}
        self._at: dict[int, float] = {}
        self.connected = False

    def topic(self, topic: str, payload: bytes | str) -> bool:
        parts = topic.strip("/").split("/")
        if NAMESPACE:
            if parts[:1] != [NAMESPACE]:
                return False
            parts = parts[1:]
        if len(parts) != 4 or parts[0] != "teslamate" or parts[1] != "cars" or not parts[2].isdigit():
            return False
        name = parts[3]
        if name not in NUM + BOOL + TEXT:
            return False
        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
        with self._lock:
            self._cars.setdefault(int(parts[2]), {})[name] = parse(name, text)
            self._at[int(parts[2])] = self.clock()
        return True

    def view(self) -> list[dict]:
        now = self.clock()
        with self._lock:
            out = []
            for cid in sorted(self._cars):
                values = self._cars[cid]
                car = {"id": cid, "name": values.get("display_name") or f"Car {cid}", "updated_s": int(now - self._at[cid])}
                car.update({k: values.get(k) for k in NUM + BOOL + TEXT if k != "display_name"})
                out.append(car)
            return out


CARS = Cars()
READY = Readiness("car")


def health() -> dict:
    cars = CARS.view()
    mqtt = {"ready": CARS.connected, "detail": "" if CARS.connected else f"no connection to MQTT at {MQTT_HOST}:{MQTT_PORT}"}
    if not CARS.connected:
        return READY.report(ready=False, detail=mqtt["detail"], deps={"mqtt": mqtt}, cars=0)
    if not cars:
        return READY.report(ready=False, detail=f"TeslaMate has no car yet: sign in to Tesla at {SIGN_IN}", deps={"mqtt": mqtt}, cars=0)
    return READY.report(ready=True, deps={"mqtt": mqtt}, cars=len(cars))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/health":
            got = health()
            self._send(200 if got["ready"] else 503, got)
        elif path == "/car":
            self._send(200, {"cars": CARS.view(), "mqtt": CARS.connected, "sign_in": SIGN_IN})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *args):   # quiet: the reporter asks every 15 s
        pass


def listen() -> None:
    import paho.mqtt.client as mqtt   # here, so the tests (and CI) don't need paho

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="skippy-car")
    topic = (f"{NAMESPACE}/" if NAMESPACE else "") + "teslamate/cars/+/+"

    def on_connect(c, userdata, flags, reason, props=None):
        CARS.connected = not reason.is_failure
        if CARS.connected:
            c.subscribe(topic, qos=0)

    def on_disconnect(c, userdata, flags, reason, props=None):
        CARS.connected = False

    def on_message(c, userdata, msg):
        if CARS.topic(msg.topic, msg.payload):
            READY.progress()

    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
    client.reconnect_delay_set(1, 30)
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
            client.loop_forever(retry_first_connection=True)
        except OSError:
            CARS.connected = False
            time.sleep(5)


def main() -> None:
    threading.Thread(target=listen, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
