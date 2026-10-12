"""Home Assistant through its REST API: spoken names become entities, the guard rails hold, and nothing is changed by guessing."""
import json

import pytest
import requests

import hass as hass_mod
from hass import Hass, HassError, run_tool


def ent(eid, name, state="off", **attrs):
    return {"entity_id": eid, "state": state, "attributes": {"friendly_name": name, **attrs}}


STATES = [
    ent("light.living_room_lamp", "Living Room Lamp", "on", brightness=128),
    ent("light.kitchen", "Kitchen Light"),
    ent("light.bedroom_lamp", "Bedroom Lamp"),
    ent("light.desk_lamp", "Desk Lamp"),
    ent("light.office_lamp", "Desk Lamp"),
    ent("switch.coffee_maker", "Coffee Maker"),
    ent("climate.hallway", "Hallway Thermostat", "heat", current_temperature=19, temperature=21),
    ent("media_player.lounge", "Lounge Speaker", "idle"),
    ent("scene.movie_night", "Movie Night", "scening"),
    ent("lock.front_door", "Front Door Lock", "locked"),
    ent("cover.garage_door", "Garage Door", "closed", device_class="garage"),
    ent("cover.blinds", "Blinds", "open", device_class="blind"),
    ent("alarm_control_panel.house", "House Alarm", "armed_home"),
    ent("sensor.outside_temperature", "Outside Temperature", "7.5", unit_of_measurement="°C"),
    ent("automation.secret_stuff", "Secret Stuff", "on"),
]
AREAS = {"light.living_room_lamp": "Living Room", "light.kitchen": "Kitchen", "light.bedroom_lamp": "Bedroom",
         "light.desk_lamp": "Study", "light.office_lamp": "Office", "media_player.lounge": "Living Room"}


class Resp:
    def __init__(self, status=200, js=None, text=""):
        self.status_code, self._js, self.text = status, js, text or (json.dumps(js) if js is not None else "")

    def json(self):
        return self._js


class FakeHa:
    def __init__(self, token="good", down=False, areas=True):
        self.token, self.down, self.calls, self.areas = token, down, [], areas

    def _auth(self, headers):
        if self.down:
            raise requests.ConnectionError("down")
        return headers.get("Authorization") == f"Bearer {self.token}"

    def get(self, url, headers=None, timeout=None, **kw):
        if not self._auth(headers):
            return Resp(401)
        if url.endswith("/api/states"):
            return Resp(200, STATES)
        if url.endswith("/api/config"):
            return Resp(200, {"version": "2026.9.1", "location_name": "Home"})
        return Resp(404)

    def post(self, url, headers=None, timeout=None, json=None, **kw):
        if not self._auth(headers):
            return Resp(401)
        if url.endswith("/api/template"):
            return Resp(200, text=__import__("json").dumps(AREAS)) if self.areas else Resp(400)
        self.calls.append((url.split("/api/services/")[1], json))
        return Resp(200, [])


def make(**kw):
    sensitive = kw.pop("sensitive", False)
    ha = FakeHa(**kw)
    return Hass("http://ha.local:8123/", "good", get=ha.get, post=ha.post, sensitive=sensitive), ha


def test_it_pings_and_every_failure_reads_as_a_sentence_never_an_exception():
    h, _ = make()
    assert h.ping() == {"ok": True, "version": "2026.9.1", "location": "Home", "entities": len(STATES)}
    bad = Hass("http://ha.local:8123", "wrong", get=FakeHa().get, post=FakeHa().post)
    assert bad.ping()["ok"] is False and "token" in bad.ping()["error"]
    down, _ = make(down=True)
    assert down.ping()["ok"] is False
    with pytest.raises(HassError, match="cannot reach"):
        down.states()


def test_a_spoken_name_turns_a_light_on_and_only_that_light():
    h, ha = make()
    assert h.control("the kitchen light", "on").startswith("Done: Kitchen Light")
    assert ha.calls == [("light/turn_on", {"entity_id": "light.kitchen"})]
    assert h.control("living room lamp", "set brightness", 40).startswith("Done")
    assert ha.calls[-1] == ("light/turn_on", {"entity_id": "light.living_room_lamp", "brightness_pct": 40})
    assert h.control("coffee maker", "off").startswith("Done") and ha.calls[-1][0] == "switch/turn_off"
    assert h.control("hallway thermostat", "set temperature", 22).startswith("Done") and ha.calls[-1][1]["temperature"] == 22
    assert h.control("lounge speaker", "set volume", 30).startswith("Done") and ha.calls[-1][1]["volume_level"] == 0.3
    assert h.control("movie night", "activate").startswith("Done") and ha.calls[-1][0] == "scene/turn_on"
    assert h.control("blinds", "close").startswith("Done") and ha.calls[-1][0] == "cover/close_cover"


def test_an_area_word_picks_the_room_and_an_ambiguous_name_changes_nothing():
    h, ha = make()
    n = len(ha.calls)
    said = h.control("desk lamp", "on")                                  # two things are called Desk Lamp
    assert "Desk Lamp" in said and "do not guess" in said and len(ha.calls) == n
    assert h.control("lamp in the study", "on").startswith("Done: Desk Lamp") and ha.calls[-1][1]["entity_id"] == "light.desk_lamp"
    assert h.control("bedroom lamp", "off").startswith("Done") and ha.calls[-1][1]["entity_id"] == "light.bedroom_lamp"
    assert "cannot find anything" in h.control("the flux capacitor", "on") and len(ha.calls) == n + 2


def test_areas_being_unavailable_does_not_break_names():
    h, ha = make(areas=False)
    assert h.control("kitchen light", "on").startswith("Done")


def test_locks_alarms_and_garage_doors_are_untouchable_by_default():
    h, ha = make()
    for name, action in (("front door lock", "unlock"), ("house alarm", "off"), ("garage door", "open"), ("secret stuff", "off")):
        said = h.control(name, action)
        assert "cannot find anything" in said, (name, said)
    assert ha.calls == []
    assert "Front Door" not in h.devices() and "Garage" not in h.devices() and "Secret" not in h.devices()


def test_with_sensitive_on_each_still_needs_a_spoken_yes():
    h, ha = make(sensitive=True)
    said = h.control("front door lock", "unlock")
    assert "confirm" in said and ha.calls == []
    assert h.control("front door lock", "unlock", confirm=True).startswith("Done") and ha.calls == [("lock/unlock", {"entity_id": "lock.front_door"})]
    assert h.control("garage door", "open", confirm=True).startswith("Done") and ha.calls[-1][0] == "cover/open_cover"
    assert "cannot" in h.control("house alarm", "off", confirm=True) and len(ha.calls) == 2         # an alarm panel is never driven from here


def test_sensors_can_be_read_but_never_changed_and_states_are_said_plainly():
    h, ha = make()
    assert h.state_of("outside temperature") == "Outside Temperature is 7.5 °C."
    assert "can only be read" in h.control("outside temperature", "on")
    assert "currently 19 degrees, set to 21" in h.state_of("hallway thermostat")
    assert "at 50 percent" in h.state_of("living room lamp")
    assert ha.calls == []


def test_a_nonsense_action_or_value_does_nothing():
    h, ha = make()
    for action, value in (("explode", None), ("set brightness", "bright"), ("set volume", 5), ("open", None)):
        assert "cannot" in h.control("kitchen light", action, value), action
    assert ha.calls == []


def test_devices_lists_by_room_and_kind_and_caps_the_list():
    h, _ = make()
    assert "Kitchen Light (off)" in h.devices(area="kitchen") and "Bedroom" not in h.devices(area="kitchen")
    assert "Coffee Maker" in h.devices(domain="switch") and "Kitchen Light" not in h.devices(domain="switch")
    assert h.devices(area="attic") == "Nothing matches in the house."


def test_the_tools_never_raise_and_never_leak_the_token():
    h, _ = make(down=True)
    for name, args in (("home_devices", {}), ("home_state", {"name": "kitchen"}), ("home_control", {"name": "kitchen", "action": "on"})):
        out = run_tool(h, name, args)
        assert "cannot reach Home Assistant" in out and "good" not in out
    assert run_tool(h, "home_nuke", {}) == "I have no such tool." and run_tool(h, "home_state", None) .startswith(("I cannot", "Home"))
    assert {t["name"] for t in hass_mod.TOOLS} == {"home_devices", "home_state", "home_control"}      # no raw service call, no template, no entity writes
