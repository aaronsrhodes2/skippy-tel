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


def test_teslamate_signed_out_is_not_ready_and_says_so(monkeypatch):
    monkeypatch.setattr(reporter, "CAR", "http://skippy-core:7351")
    answer(monkeypatch, {"cars": [{"id": 1}], "mqtt": True, "sign_in": "http://skippy-core:4000", "signed_in": False})
    got = reporter.car_capability()
    assert got["ready"] is False and got["signed_in"] is False and got["cars"]
    answer(monkeypatch, {"cars": [{"id": 1}], "mqtt": True, "signed_in": "yes"})   # anything but a bool: unknown, not a reason to fail
    got = reporter.car_capability()
    assert got["ready"] is True and got["signed_in"] is None


def test_down_or_unset_is_not_ready(monkeypatch):
    monkeypatch.setattr(reporter, "CAR", "http://skippy-core:7351")
    answer(monkeypatch, None)
    assert reporter.car_capability() == {"ready": False}
    monkeypatch.setattr(reporter, "CAR", "")
    asked = answer(monkeypatch, {"cars": [{"id": 1}], "mqtt": True})
    assert reporter.car_capability() == {"ready": False} and asked == []


def test_the_guards_sentry_events_ride_along_trimmed(monkeypatch):
    monkeypatch.setattr(reporter, "CAR", "http://skippy-core:7351")
    monkeypatch.setattr(reporter, "CAR_FLEET", "http://skippy-core:7353")
    events = [{"id": f"{i}-1", "at": 100 + i, "state": "aware", "said": "someone came close"} for i in range(15)]
    replies = {"http://skippy-core:7351/car": {"cars": [{"id": 1}], "mqtt": True},
               "http://skippy-core:7353/sentry": {"state": "aware", "since": 114, "events": events, "telemetry": {"heard_s": 3, "online": True}}}
    monkeypatch.setattr(reporter, "fetch", lambda url, **kw: replies.get(url))
    got = reporter.car_capability()["sentry"]
    assert got["state"] == "aware" and got["online"] is True and len(got["events"]) == reporter.SENTRY_EVENTS
    assert got["events"][-1]["id"] == "14-1"
    replies.pop("http://skippy-core:7353/sentry")
    assert "sentry" not in reporter.car_capability()                 # the guard down: the car's state still goes
