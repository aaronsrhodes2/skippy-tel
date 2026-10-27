"""The Captain's car: the reporter carries what skippy-core's car reader holds (TeslaMate's last values), and calls it ready only
when a car is signed in. A reader that is down, or not set up, is simply not ready."""
import reporter


def answer(monkeypatch, reply):
    asked = []

    def fake(url, method="GET", data=None, timeout=4.0, raw=False):
        asked.append(url)
        return reply
    monkeypatch.setattr(reporter, "fetch", fake)
    return asked


def test_a_signed_in_car_is_ready_and_carried(monkeypatch):
    monkeypatch.setattr(reporter, "CAR", "http://skippy-core:7351")
    cars = [{"id": i, "name": f"car {i}", "battery_level": 70} for i in range(1, 6)]
    asked = answer(monkeypatch, {"cars": cars, "mqtt": True, "sign_in": "http://skippy-core:4000"})
    got = reporter.car_capability()
    assert asked == ["http://skippy-core:7351/car"]
    assert got["ready"] is True and got["mqtt"] is True and len(got["cars"]) == reporter.CAR_MAX
    assert got["sign_in"] == "http://skippy-core:4000"


def test_no_car_signed_in_is_not_ready_but_says_teslamate_runs(monkeypatch):
    monkeypatch.setattr(reporter, "CAR", "http://skippy-core:7351")
    answer(monkeypatch, {"cars": [], "mqtt": True, "sign_in": "http://skippy-core:4000"})
    got = reporter.car_capability()
    assert got["ready"] is False and got["mqtt"] is True and got["cars"] == []


def test_down_or_unset_is_not_ready(monkeypatch):
    monkeypatch.setattr(reporter, "CAR", "http://skippy-core:7351")
    answer(monkeypatch, None)
    assert reporter.car_capability() == {"ready": False}
    monkeypatch.setattr(reporter, "CAR", "")
    asked = answer(monkeypatch, {"cars": [{"id": 1}], "mqtt": True})
    assert reporter.car_capability() == {"ready": False} and asked == []
