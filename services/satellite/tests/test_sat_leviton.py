"""Leviton through the My Leviton cloud (leviton.py), against a fake decora_wifi: a session that logs in, and switches whose `data` is what the
real API returns (name, model, power "ON"/"OFF", brightness 0-100, canSetLevel, connected), with update_attributes and refresh."""
import pytest

from house import Combined, HomeError, House
from leviton import Leviton


class FakeSwitch:
    def __init__(self, name, power="OFF", brightness=50, dims=True, connected=True, stuck=False):
        self.data = {"name": name, "model": "D2MSD" if dims else "D215S", "power": power, "brightness": brightness,
                     "canSetLevel": dims, "connected": connected}
        self.stuck, self.puts = stuck, []

    def update_attributes(self, attribs):
        self.puts.append(dict(attribs))
        if not self.stuck:
            self.data.update(attribs)

    def refresh(self):
        return self


class FakeSession:
    def __init__(self, ok=True):
        self.ok, self.logins = ok, 0

    def login(self, email, password):
        self.logins += 1
        return self.ok


def make(switches, ok=True, fail_lists=0):
    session = FakeSession(ok)
    calls = {"n": 0}

    def listing(s):
        calls["n"] += 1
        if calls["n"] <= fail_lists:
            raise RuntimeError("401 session expired")
        return switches
    t = [0.0]
    lev = Leviton("me@example.com", "pw", session=lambda: session, switches=listing, clock=lambda: t[0], sleep=lambda s: None)
    return lev, session, t


def test_dimmers_are_lights_plain_switches_are_switches_and_offline_ones_are_left_out():
    lev, _, _ = make([FakeSwitch("kitchen under 1", "ON", 40), FakeSwitch("Bedroom", dims=False),
                      FakeSwitch("bathroom overhead", connected=False)])
    rows = {r["entity_id"]: r for r in lev.states()}
    assert set(rows) == {"light.kitchen_under_1", "switch.bedroom"}
    assert rows["light.kitchen_under_1"]["state"] == "on" and rows["light.kitchen_under_1"]["attributes"]["brightness"] == 102
    assert "brightness" not in rows["switch.bedroom"]["attributes"]


def test_on_off_and_brightness_are_said_then_read_back():
    sw = FakeSwitch("security Light")
    lev, _, _ = make([sw])
    assert lev.control("security light", "on").startswith("Done")
    assert sw.data["power"] == "ON" and lev.last["attempts"] == 1
    lev.control("security light", "set_brightness", 30)
    assert sw.puts[-1] == {"power": "ON", "brightness": 30}
    lev.control("security light", "toggle")
    assert sw.data["power"] == "OFF"


def test_a_switch_that_does_not_change_is_told_twice_then_reported():
    sw = FakeSwitch("deck sliding door", stuck=True)
    lev, _, _ = make([sw])
    with pytest.raises(HomeError, match="did not change"):
        lev.control("deck sliding door", "on")
    assert len(sw.puts) == 2


def test_an_expired_session_logs_in_again_once():
    lev, session, _ = make([FakeSwitch("track stove light")], fail_lists=1)
    assert [r["entity_id"] for r in lev.states()] == ["light.track_stove_light"] and session.logins == 2


def test_a_refused_login_is_a_sentence_never_the_password():
    lev, _, _ = make([], ok=False)
    with pytest.raises(HomeError) as e:
        lev.states()
    assert "refused" in str(e.value) and "pw" not in str(e.value)


def test_a_local_backend_with_the_same_name_wins_in_the_combined_house():
    class Local(House):
        def states(self, fresh=False):
            return [{"entity_id": "light.kitchen_under_1", "state": "off", "attributes": {"friendly_name": "kitchen under 1"}}]

        def _service(self, domain, service, data):
            self.called = service
    local = Local()
    lev, _, _ = make([FakeSwitch("kitchen under 1")])
    house = Combined([local, lev])
    house.control("kitchen under 1", "on")
    assert local.called == "turn_on"
