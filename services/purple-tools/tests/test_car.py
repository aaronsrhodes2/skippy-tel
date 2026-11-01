"""purple Skippy's car_charge: it sends only start / stop / limit to skippy-core's Fleet API door (core/car-fleet), says the car's refusal in its own words,
and reads the state back from TeslaMate's reader."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402

CAR = {"cars": [{"name": "T3-P0", "battery_level": 66, "charge_limit_soc": 80, "plugged_in": True, "charging_state": "Charging", "charger_power": 7}]}


def test_start_goes_to_the_charging_door_and_reads_the_car_back():
    sent = {}

    def post(url, body, timeout=0):
        sent.update(url=url, body=body)
        return {"ok": True, "said": "charging started"}
    out = tools.car_charge({"action": "start"}, post=post, get=lambda url, t=0: CAR)
    assert sent == {"url": tools.CAR_FLEET_URL + "/charge", "body": {"action": "start"}}
    assert out.startswith("charging started. T3-P0: battery 66%, limit 80%, plugged in, charging state Charging, 7 kW")


def test_limit_carries_the_percent_and_status_sends_nothing():
    sent = []
    post = lambda url, body, timeout=0: sent.append(body) or {"ok": True, "said": "charge limit set to 90%"}  # noqa: E731
    tools.car_charge({"action": "limit", "percent": 90}, post=post, get=lambda url, t=0: CAR)
    assert sent == [{"action": "limit", "percent": 90}]
    assert "T3-P0" in tools.car_charge({"action": "status"}, post=lambda *a, **k: pytest.fail("status must not send"), get=lambda url, t=0: CAR)


def test_a_refusal_or_an_unknown_action_is_an_error_in_words():
    with pytest.raises(ValueError, match="isn't plugged in"):
        tools.car_charge({"action": "start"}, post=lambda *a, **k: {"ok": False, "error": "the car isn't plugged in"}, get=lambda url, t=0: CAR)
    with pytest.raises(ValueError):
        tools.car_charge({"action": "unlock"})
    text, err = tools.call("car_charge", {"action": "honk"})
    assert err and "start, stop, limit or status" in text


def test_car_sentry_arms_through_the_guard_and_says_what_it_saw():
    sent = []
    view = {"state": "armed", "events": [{"at": 1_700_000_000, "said": "someone came close"}], "telemetry": {"heard_s": 4}}
    out = tools.car_sentry({"action": "arm"}, post=lambda url, body, timeout=0: sent.append((url, body)) or {"ok": True, "said": "Sentry Mode on"},
                           get=lambda url, t=0: view)
    assert sent == [(f"{tools.CAR_FLEET_URL}/sentry", {"action": "arm"})]
    assert out.startswith("Sentry Mode on. Sentry is armed") and "someone came close" in out
    assert "hasn't streamed" in tools.car_sentry({"action": "status"}, post=lambda *a, **k: pytest.fail("status must not send"),
                                                 get=lambda url, t=0: {"state": "unknown", "events": [], "telemetry": {}})
    with pytest.raises(ValueError):
        tools.car_sentry({"action": "honk"})
