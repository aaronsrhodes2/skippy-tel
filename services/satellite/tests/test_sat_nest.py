"""The Nest through Google's Device Access (nest.py): read, set, Eco and the unit, against canned SDM answers; and through the house tools."""
import urllib.error

import pytest

import google_home
import house
import nest

T = "sdm.devices.traits."


class FakeGoogle:
    nest_project = "proj"

    def __init__(self, connected=True):
        self.connected, self.asked = connected, 0

    def access_token(self):
        self.asked += 1
        if not self.connected:
            raise google_home.NotConnected("not connected: press Connect Google for Skippy in Admin")
        return "tok"


def thermostat(mode="HEAT", eco="OFF", heat=20.0, cool=None, scale="FAHRENHEIT", ambient=21.0, name=""):
    sp = {"heatCelsius": heat} if cool is None else {"heatCelsius": heat, "coolCelsius": cool}
    return {"name": "enterprises/proj/devices/DEV1", "type": "sdm.devices.types.THERMOSTAT",
            "traits": {T + "Info": {"customName": name}, T + "Temperature": {"ambientTemperatureCelsius": ambient},
                       T + "Humidity": {"ambientHumidityPercent": 40}, T + "Settings": {"temperatureScale": scale},
                       T + "ThermostatMode": {"mode": mode, "availableModes": ["HEAT", "COOL", "HEATCOOL", "OFF"]},
                       T + "ThermostatEco": {"mode": eco, "heatCelsius": 15.0, "coolCelsius": 27.0},
                       T + "ThermostatHvac": {"status": "HEATING"}, T + "ThermostatTemperatureSetpoint": sp},
            "parentRelations": [{"parent": "x", "displayName": "Hallway"}]}


class FakeSDM:
    def __init__(self, device):
        self.device, self.calls = device, []

    def __call__(self, method, url, token, body=None):
        self.calls.append((method, url.split("/enterprises/")[1], body))
        assert token == "tok"
        if method == "GET":
            return {"devices": [self.device]}
        cmd, p = body["command"].rsplit(".", 1)[-1], body["params"]
        tr = self.device["traits"]
        if body["command"].endswith("ThermostatEco.SetMode"):
            tr[T + "ThermostatEco"]["mode"] = p["mode"]
        elif cmd == "SetHeat":
            tr[T + "ThermostatTemperatureSetpoint"]["heatCelsius"] = p["heatCelsius"]
        elif body["command"].endswith("ThermostatMode.SetMode"):
            tr[T + "ThermostatMode"]["mode"] = p["mode"]
        return {}


def make(device=None, google=None):
    sdm = FakeSDM(device or thermostat())
    return nest.Nest(google or FakeGoogle(), call=sdm, clock=lambda: 0.0), sdm


def test_a_thermostat_reads_in_its_own_unit_named_by_its_room():
    n, sdm = make()
    (row,) = n.states()
    assert row["entity_id"] == "climate.hallway_thermostat" and row["state"] == "heat"
    a = row["attributes"]
    assert (a["current_temperature"], a["temperature"], a["unit_of_measurement"]) == (70, 68, "°F")
    assert sdm.calls[0][:2] == ("GET", "proj/devices")


def test_setting_70_sets_the_heat_in_celsius_and_turns_eco_off_first():
    n, sdm = make(thermostat(eco="MANUAL_ECO"))
    assert n.states()[0]["attributes"]["preset_mode"] == "eco"
    n._service("climate", "set_temperature", {"entity_id": "climate.hallway_thermostat", "temperature": 70})
    cmds = [(c[2]["command"].split("commands.")[1], c[2]["params"]) for c in sdm.calls if c[0] == "POST"]
    assert cmds == [("ThermostatEco.SetMode", {"mode": "OFF"}), ("ThermostatTemperatureSetpoint.SetHeat", {"heatCelsius": 21.11})]
    assert n.states()[0]["attributes"]["temperature"] == 70                      # read back


def test_through_the_house_tools_what_he_says_works():
    n, sdm = make()
    assert "68" in house.run_tool(n, "home_state", {"name": "thermostat"})
    assert house.run_tool(n, "home_control", {"name": "thermostat", "action": "set_temperature", "value": 71}).startswith("Done")
    house.run_tool(n, "home_control", {"name": "thermostat", "action": "off"})
    house.run_tool(n, "home_control", {"name": "thermostat", "action": "cool"})
    modes = [c[2]["params"]["mode"] for c in sdm.calls if c[0] == "POST" and c[2]["command"].endswith("ThermostatMode.SetMode")]
    assert modes == ["OFF", "COOL"]


def test_an_off_thermostat_is_not_given_a_set_point_and_out_of_range_is_refused():
    n, _ = make(thermostat(mode="OFF"))
    with pytest.raises(house.HomeError, match="off"):
        n._service("climate", "set_temperature", {"entity_id": "climate.hallway_thermostat", "temperature": 70})
    n, _ = make()
    n.states()
    with pytest.raises(house.HomeError, match="outside"):
        n._service("climate", "set_temperature", {"entity_id": "climate.hallway_thermostat", "temperature": 95})


def test_not_connected_says_how_and_waits_before_asking_again():
    g = FakeGoogle(connected=False)
    n, _ = make(google=g)
    with pytest.raises(house.HomeError, match="Connect Google for Skippy"):
        n.states()
    with pytest.raises(house.HomeError):
        n.states()
    assert g.asked == 1


def test_a_403_from_google_means_the_thermostat_was_not_ticked_and_the_old_token_is_dropped():
    def refuse(method, url, token, body=None):
        raise urllib.error.HTTPError(url, 403, "denied", {}, None)
    g = FakeGoogle()
    g._access = ("an hour-old token without sdm.service", 9e9)
    n = nest.Nest(g, call=refuse, clock=lambda: 0.0)
    with pytest.raises(house.HomeError, match="tick the thermostat"):
        n.states()
    assert g._access is None                                 # the next try borrows a fresh one, with the thermostat in it


def test_one_backend_down_never_hides_the_others():
    n, _ = make(google=FakeGoogle(connected=False))

    class Lamp(house.House):
        def states(self, fresh=False):
            return [{"entity_id": "light.desk", "state": "on", "attributes": {"friendly_name": "Desk"}}]

        def areas(self):
            return {}

        def invalidate(self):
            pass
    both = house.Combined([Lamp(), n])
    assert [s["entity_id"] for s in both.states()] == ["light.desk"]


def test_bedtime_sets_the_night_point_when_he_chose_one_else_says_where_it_stands():
    n, sdm = make()
    assert n.bedtime() == "The thermostat is set to 68, and it's 70 in here."
    assert "set the thermostat to 66 for the night" in n.bedtime(66)
    assert n.states(fresh=True)[0]["attributes"]["temperature"] == 66


def test_the_project_comes_from_the_portal_with_the_token(tmp_path):
    g = google_home.Google({"GOOGLE_HOME_TOKEN": str(tmp_path / "n"), "GOOGLE_HOME_CLIENT": str(tmp_path / "c"), "SKIPPY_HOME_TOKEN": "h"},
                           get_json=lambda url, h: {"connected": True, "access_token": "tok", "expires_in": 3500, "nest_project": "proj-from-portal"},
                           log=lambda m: None)
    calls = []
    n = nest.Nest(g, call=lambda m, url, t, b=None: calls.append(url) or {"devices": []}, clock=lambda: 0.0)
    n.states()
    assert calls == [f"{nest.SDM}/enterprises/proj-from-portal/devices"]


@pytest.mark.parametrize("heard,want", [
    ("Skippy, set the thermostat to 70", ("home_control", {"name": "thermostat", "action": "set_temperature", "value": 70})),
    ("turn the heat up to 68 please", ("home_control", {"name": "thermostat", "action": "set_temperature", "value": 68})),
    ("turn the heat off", ("home_control", {"name": "thermostat", "action": "off"})),
    ("turn off the heater", ("home_control", {"name": "thermostat", "action": "off"})),
    ("turn on the heat", ("home_control", {"name": "thermostat", "action": "heat"})),
    ("what is the thermostat set to and how warm is it in here", ("home_state", {"name": "thermostat"})),
    ("how warm is it in here?", ("home_state", {"name": "thermostat"})),
    ("is the heat on", ("home_state", {"name": "thermostat"})),
    ("how warm is it outside tomorrow", None),
    ("turn off the kitchen lights", None),
    ("set an alarm for 7", None),
])
def test_plain_thermostat_requests_go_straight_to_the_house_tool(heard, want):
    assert house.thermostat_route(heard) == want
