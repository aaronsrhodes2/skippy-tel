"""The garage door (garage.py) against a simulated ratgdo32: a fake aioesphomeapi client with the real library's entity and state classes, the
real House guard rails (hidden unless HASS_SENSITIVE, a spoken yes, never closing on an obstruction), text chats that cannot confirm, the
backend absent without GARAGE_ESPHOME, and dialling again after the ratgdo drops."""
import json
import time

import pytest

aioesphomeapi = pytest.importorskip("aioesphomeapi")
from aioesphomeapi import (BinarySensorInfo, BinarySensorState, ButtonInfo, CoverInfo, CoverOperation, CoverState,  # noqa: E402
                           LightInfo, LightState, LockCommand, LockInfo, LockState)

import ear  # noqa: E402
import garage  # noqa: E402
import house  # noqa: E402
import house_mcp  # noqa: E402

DOOR, LIGHT, MOTION, OBST, LOCK, TOGGLE = 1, 2, 3, 4, 5, 6


class FakeRatgdo:
    """One ratgdo32 as garage.yaml builds it; each connection is a FakeClient on it. `up` False refuses connections."""

    def __init__(self):
        self.up = True
        self.position, self.operation, self.light, self.motion, self.obstructed, self.locked = 0.0, 0, False, False, False, False
        self.commands = []
        self.clients = []

    def entities(self):
        return [CoverInfo(object_id="door", key=DOOR, name="Door", device_class="garage", supports_position=True, supports_stop=True),
                LightInfo(object_id="light", key=LIGHT, name="Light"),
                BinarySensorInfo(object_id="motion", key=MOTION, name="Motion", device_class="motion"),
                BinarySensorInfo(object_id="obstruction", key=OBST, name="Obstruction", device_class="problem"),
                LockInfo(object_id="lock_remotes", key=LOCK, name="Lock remotes"),
                ButtonInfo(object_id="toggle_door", key=TOGGLE, name="Toggle door")]

    def states(self):
        return [CoverState(key=DOOR, position=self.position, current_operation=CoverOperation(self.operation)),
                LightState(key=LIGHT, state=self.light), BinarySensorState(key=MOTION, state=self.motion),
                BinarySensorState(key=OBST, state=self.obstructed),
                _lock_state(self.locked)]

    def push(self):
        for c in self.clients:
            if c.on_states:
                for s in self.states():
                    c.loop.call_soon_threadsafe(c.on_states, s)

    def drop(self):
        for c in list(self.clients):
            c.loop.call_soon_threadsafe(lambda c=c: c.loop.create_task(c.on_stop(False)))
        self.clients.clear()


def _lock_state(locked):
    from aioesphomeapi import LockEntityState  # noqa: PLC0415
    return LockEntityState(key=LOCK, state=LockState.LOCKED if locked else LockState.UNLOCKED)


class FakeClient:
    def __init__(self, box: FakeRatgdo):
        self.box, self.on_states, self.on_stop, self.loop = box, None, None, None

    async def connect(self, on_stop=None, login=False):
        import asyncio  # noqa: PLC0415
        if not self.box.up:
            raise ConnectionRefusedError("ratgdo is off")
        self.on_stop, self.loop = on_stop, asyncio.get_running_loop()
        self.box.clients.append(self)

    async def list_entities_services(self):
        return self.box.entities(), []

    def subscribe_states(self, cb):
        self.on_states = cb
        for s in self.box.states():
            cb(s)

    def cover_command(self, key, position=None, tilt=None, stop=False, **kw):
        self.box.commands.append(("cover", key, position, stop))

    def light_command(self, key, state=None, **kw):
        self.box.commands.append(("light", key, state))

    def lock_command(self, key, command, code=None, **kw):
        self.box.commands.append(("lock", key, command))

    async def disconnect(self):
        pass


def wait_for(cond, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def box():
    return FakeRatgdo()


def make(box, sensitive=True):
    g = garage.Garage("esphome://10.0.0.50", "key", sensitive=sensitive, make_client=lambda: FakeClient(box), log=lambda m: None,
                      connect_wait=3.0, backoff=(0.05, 0.2))
    assert wait_for(lambda: g.connected)
    return g


def test_parse():
    assert garage.parse("esphome://skippy-garage.local") == ("skippy-garage.local", 6053)
    assert garage.parse("esphome://10.0.0.50:6054") == ("10.0.0.50", 6054)
    assert garage.parse("10.0.0.50") == ("10.0.0.50", 6053)
    with pytest.raises(ValueError):
        garage.parse("esphome://")


def test_state(box):
    g = make(box)
    rows = {r["entity_id"]: r for r in g.states()}
    assert set(rows) == set(garage.ROWS)                         # the Toggle door button and the diagnostics are not things in the house
    assert rows["cover.garage_door"]["state"] == "closed"
    assert rows["cover.garage_door"]["attributes"]["device_class"] == "garage"
    assert g.state_of("garage door") == "Garage door is closed."
    box.position = 1.0
    box.push()
    assert wait_for(lambda: g.state_of("garage door") == "Garage door is open.")
    box.position, box.operation = 0.5, 2
    box.push()
    assert wait_for(lambda: g.state_of("garage door") == "Garage door is closing.")
    box.operation = 0
    box.push()
    assert wait_for(lambda: g.state_of("garage door") == "Garage door is open, 50 percent of the way.")
    box.motion = True
    box.push()
    assert wait_for(lambda: g.state_of("garage motion") == "Garage motion is on.")
    assert g.rooms()["cover.garage_door"] == "garage"
    g.close()


def test_hidden_without_sensitive(box):
    g = make(box, sensitive=False)
    assert "Garage door" not in g.devices()
    assert "Garage remotes" not in g.devices()
    assert "Garage light" in g.devices()                         # the light is an ordinary light
    assert "cannot find" in g.control("garage door", "open", confirm=True)
    assert box.commands == []
    g.close()


def test_open_needs_confirm(box):
    g = make(box)
    said = g.control("garage door", "open")
    assert "confirm" in said and box.commands == []
    assert g.control("garage door", "open", confirm=True).startswith("Done: Garage door, open")
    assert box.commands == [("cover", DOOR, 1.0, False)]
    g.control("garage door", "stop", confirm=True)
    assert box.commands[-1] == ("cover", DOOR, None, True)
    g.control("garage door", "set_position", 30, confirm=True)
    assert box.commands[-1] == ("cover", DOOR, 0.3, False)
    g.close()


def test_light_and_remotes_lock(box):
    g = make(box)
    g.control("garage light", "on")                              # a light: no confirm needed
    assert box.commands[-1] == ("light", LIGHT, True)
    assert "confirm" in g.control("garage remotes", "lock")
    g.control("garage remotes", "lock", confirm=True)
    assert box.commands[-1] == ("lock", LOCK, LockCommand.LOCK)
    assert g.state_of("garage remotes") == "Garage remotes is unlocked."
    g.close()


def test_never_closes_on_obstruction(box):
    box.position, box.obstructed = 1.0, True
    g = make(box)
    said = g.control("garage door", "close", confirm=True)
    assert "something is in the way" in said and box.commands == []
    assert "something is in the way" in g.state_of("garage door")
    # the beam is blocked between the last push and the command: the backend's own fresh check still refuses
    box.obstructed = False
    box.push()
    assert wait_for(lambda: not g._obstructed())
    g.values[g.keys["binary_sensor.garage_obstruction"]] = BinarySensorState(key=OBST, state=True)
    assert "something is in the way" in house.run_tool(g, "home_control", {"name": "garage door", "action": "close", "confirm": True})
    assert box.commands == []
    g.values[g.keys["binary_sensor.garage_obstruction"]] = BinarySensorState(key=OBST, state=False)
    assert g.control("garage door", "close", confirm=True).startswith("Done")
    assert box.commands == [("cover", DOOR, 0.0, False)]
    assert g.control("garage door", "open", confirm=True).startswith("Done")       # opening is always allowed: it is how you clear it
    g.close()


def test_text_chat_cannot_open(box):
    g = make(box)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "home_control", "arguments": {"name": "garage door", "action": "open", "confirm": True}}}
    text = house_mcp.handle(msg, lambda: g)["result"]["content"][0]["text"]
    assert "confirm" in text and box.commands == []
    g.close()


def test_absent_without_config(tmp_path, monkeypatch):
    assert ear.build_house({"SKIPPY_HOME_STATE": str(tmp_path)}) is None
    made = []

    class Stub(house.House):
        def __init__(self, url, key, sensitive=False):
            made.append((url, key, sensitive))
    monkeypatch.setattr(garage, "Garage", Stub)
    h = ear.build_house({"SKIPPY_HOME_STATE": str(tmp_path), "GARAGE_ESPHOME": "esphome://10.0.0.50", "GARAGE_ESPHOME_KEY": "k",
                         "HASS_SENSITIVE": "1"})
    assert isinstance(h, Stub) and made == [("esphome://10.0.0.50", "k", True)]


def test_in_combined_house(box):
    g = make(box)

    class Lights(house.House):
        def states(self, fresh=False):
            return [{"entity_id": "light.kitchen_under_1", "state": "off", "attributes": {"friendly_name": "kitchen under 1"}}]
    h = house.Combined([Lights(), g], sensitive=True)
    assert h.state_of("garage door") == "Garage door is closed."
    assert "confirm" in h.control("garage door", "open")
    assert h.control("garage door", "open", confirm=True).startswith("Done")
    assert box.commands == [("cover", DOOR, 1.0, False)]
    assert h.control("garage", "on").startswith("Done")          # "the garage" is the room: its lights
    assert box.commands[-1] == ("light", LIGHT, True)
    g.close()


def test_reconnects(box):
    g = make(box)
    assert g.sessions == 1
    box.up = False
    box.drop()
    assert wait_for(lambda: not g.connected)
    assert g.state_of("garage door") == "Garage door is unavailable."
    assert house.run_tool(g, "home_control", {"name": "garage door", "action": "open", "confirm": True}) == garage.OFFLINE
    assert box.commands == []
    box.up = True
    assert wait_for(lambda: g.connected and g.sessions == 2)
    assert g.state_of("garage door") == "Garage door is closed."
    g.close()


def test_never_connected():
    box = FakeRatgdo()
    box.up = False
    g = garage.Garage("esphome://10.0.0.50", sensitive=True, make_client=lambda: FakeClient(box), log=lambda m: None,
                      connect_wait=0.1, backoff=(0.05, 0.1))
    assert g.state_of("garage door") == "Garage door is unavailable."
    assert house.run_tool(g, "home_control", {"name": "garage door", "action": "open", "confirm": True}) == garage.OFFLINE
    assert house.Combined([g], sensitive=True).state_of("garage door") == "Garage door is unavailable."
    g.close()


def test_layout_has_the_garage():
    import house_layout  # noqa: PLC0415
    lay = house_layout.default()
    assert lay.room_of("cover.garage_door") == "garage"
    assert lay.device("light.garage_light").get("kind") == "light"
    assert json.loads(json.dumps(lay.device("cover.garage_door")))["kind"] == "garage door"
