"""The car reader (core/car/car.py): it keeps TeslaMate's latest values, typed, per car; it ignores what isn't the car's; it says
ready only once MQTT is connected AND a car is signed in, and says why otherwise; and the Tesla stack joins skippy-core only by
core.env, pinned, with its doors on 127.0.0.1 (tailnet only through install/tesla.sh)."""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from core_testlib import CORE, load

sys.path.insert(0, str(CORE / "car"))
car = load("car/car.py", "core_car")
dep = load("deployer/deployer.py", "core_deployer")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_values_are_kept_per_car_and_typed():
    clock = Clock()
    cars = car.Cars(clock=clock)
    for name, raw in (("display_name", "T3-P0"), ("state", "asleep"), ("battery_level", "78"), ("est_battery_range_km", "301.4"),
                      ("plugged_in", "true"), ("locked", "false"), ("inside_temp", ""), ("charge_limit_soc", "nil")):
        assert cars.topic(f"teslamate/cars/1/{name}", name == "state" and b"asleep" or raw.encode())
    clock.t += 12
    (c,) = cars.view()
    assert c["id"] == 1 and c["name"] == "T3-P0" and c["state"] == "asleep"
    assert c["battery_level"] == 78 and c["est_battery_range_km"] == 301.4
    assert c["plugged_in"] is True and c["locked"] is False
    assert c["inside_temp"] is None and c["charge_limit_soc"] is None        # empty and nil read as unknown, never 0
    assert c["updated_s"] == 12


def test_what_is_not_a_car_value_is_ignored():
    cars = car.Cars()
    assert not cars.topic("teslamate/cars/1/some_new_thing", b"x")           # only the names we know
    assert not cars.topic("teslamate/cars/x/battery_level", b"5")
    assert not cars.topic("teslamate/geofences/home", b"1")
    assert not cars.topic("other/cars/1/battery_level", b"5")
    assert cars.view() == []
    assert car.parse("battery_level", "lots") is None


def test_health_says_why_it_is_not_ready(monkeypatch):
    cars = car.Cars()
    monkeypatch.setattr(car, "CARS", cars)
    monkeypatch.setattr(car, "READY", car.Readiness("car"))
    got = car.health()
    assert not got["ready"] and "MQTT" in got["detail"]
    cars.connected = True
    got = car.health()
    assert not got["ready"] and "sign in to Tesla" in got["detail"] and got["cars"] == 0
    cars.topic("teslamate/cars/1/battery_level", b"50")
    got = car.health()
    assert got["ready"] and got["cars"] == 1 and got["spec"] == 1


def test_the_http_side_answers_car_and_health(monkeypatch):
    cars = car.Cars()
    cars.connected = True
    cars.topic("teslamate/cars/1/state", b"charging")
    monkeypatch.setattr(car, "CARS", cars)
    server = ThreadingHTTPServer(("127.0.0.1", 0), car.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        body = json.loads(urllib.request.urlopen(f"{base}/car", timeout=5).read())
        assert body["mqtt"] is True and body["cars"][0]["state"] == "charging"
        assert json.loads(urllib.request.urlopen(f"{base}/health", timeout=5).read())["ready"] is True
        cars._cars.clear()
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}/health", timeout=5)
        assert e.value.code == 503                                             # not ready answers 503, with the reason
    finally:
        server.shutdown()


def test_the_tesla_stack_joins_only_when_core_env_says_so(tmp_path, monkeypatch):
    for k in dep.DEFAULTS:
        monkeypatch.delenv(k, raising=False)
    f = tmp_path / "core.env"
    f.write_text("CORE_GPU=0\n")
    assert "core/compose.tesla.yml" not in dep.Deployer(dep.load_settings(str(f)), log=lambda m: None).compose_files()
    f.write_text("CORE_GPU=0\nCORE_TESLA=1\n")
    d = dep.Deployer(dep.load_settings(str(f)), log=lambda m: None)
    assert d.compose_files() == ["-f", "core/compose.core.yml", "-f", "core/compose.tesla.yml"]
    assert d.env("")["CORE_TESLA"] == "1"                                     # the smoke test checks the car only then


def test_the_tesla_compose_file_is_pinned_and_keeps_its_doors_local():
    text = (CORE / "compose.tesla.yml").read_text(encoding="utf-8")
    for image in ("teslamate/teslamate:4.4.0@sha256:", "postgres:17.6@sha256:", "eclipse-mosquitto:2.0.22@sha256:"):
        assert image in text
    ports = [line.strip() for line in text.splitlines() if line.strip().startswith('- "') and ":" in line and "/" not in line]
    assert ports == ['- "127.0.0.1:4000:4000"', '- "127.0.0.1:7351:7351"']   # nothing on the LAN; mosquitto and Postgres not at all
    assert "${CORE_STATE:-/var/lib/skippy-core}/tesla/db:" in text            # the history is kept with CORE_STATE
    assert "/etc/skippy-core/tesla.env" in text and "ENCRYPTION_KEY=" not in text   # secrets live on the box, never here


def test_the_broker_keeps_the_cars_values_across_a_restart():
    text = (CORE / "compose.tesla.yml").read_text(encoding="utf-8")
    assert "./car/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" in text
    conf = (CORE / "car" / "mosquitto.conf").read_text(encoding="utf-8")
    assert "persistence true" in conf and "persistence_location /mosquitto/data/" in conf
