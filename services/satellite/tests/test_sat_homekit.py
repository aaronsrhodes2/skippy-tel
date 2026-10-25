"""HomeKit, natively: pairing stores a key and never the code, accessories become lights and switches, and the House's guard rails apply unchanged."""
import json
from types import SimpleNamespace as NS

import pytest

import homectl
import homekit
from homekit import HomeKit, Store
from house import HomeError


def char(short, iid, value=None):
    return NS(type=f"{short:08X}-0000-1000-8000-0026BB765291", iid=iid, value=value)


def service(kind, *chars):
    return NS(type=f"{kind:08X}-0000-1000-8000-0026BB765291", characteristics=list(chars))


class FakePairing:
    def __init__(self, accessories):
        self.accessories, self.values, self.writes, self.reads = accessories, {}, [], 0
        self.pairing_data = {"AccessoryPairingID": "x", "AccessoryLTPK": "key-material", "iOSDevicePairingID": "y", "iOSDeviceLTSK": "secret", "Connection": "IP"}

    async def async_populate_accessories_state(self, force_update=False):
        return None

    async def get_characteristics(self, wanted):
        self.reads += 1
        return {k: {"value": self.values.get(k)} for k in wanted}

    async def identify(self):
        self.identified = True

    async def put_characteristics(self, writes):
        self.writes.append(list(writes))
        for aid, iid, value in writes:
            self.values[(aid, iid)] = value


class FakeDiscovery:
    def __init__(self, ident, name, unpaired, controller):
        self.description = NS(id=ident, name=name, status_flags=1 if unpaired else 0, address="192.168.0.9")
        self.controller = controller

    async def async_identify(self):
        self.controller.identified.append(self.description.id)

    async def async_start_pairing(self, alias):
        async def finish(code):
            self.controller.codes.append(code)
            if code != "123-45-678":
                raise RuntimeError("wrong code")
            return self.controller.make_pairing()
        return finish


class FakeController:
    def __init__(self):
        self.codes, self.loaded, self.identified = [], [], []
        self.devices = [FakeDiscovery("aa:bb:cc:00:00:01", "LEVDS-MotDim-0001", True, self), FakeDiscovery("10:a6:d6:61:9b:8a", "LG webOS TV", False, self)]
        dimmer = NS(aid=1, name="Dimmer", services=[service(homekit.LIGHTBULB, char(homekit.ON, 10), char(homekit.BRIGHTNESS, 11))])
        switch = NS(aid=2, name="Plug", services=[service(homekit.OUTLET, char(homekit.ON, 20))])
        self.accessories = {"aa:bb:cc:00:00:01": [dimmer], "aa:bb:cc:00:00:02": [switch]}
        self.pairings = {}

    def make_pairing(self):
        return FakePairing(self.accessories["aa:bb:cc:00:00:01"])

    async def async_discover(self, timeout=10):
        for d in self.devices:
            yield d

    def load_pairing(self, alias, data):
        self.loaded.append(alias)
        if alias not in self.pairings:
            self.pairings[alias] = FakePairing(self.accessories[alias])
        return self.pairings[alias]


@pytest.fixture
def kit(tmp_path):
    fc = FakeController()

    async def factory():
        return fc
    clock = [0.0]
    hk = HomeKit(Store(tmp_path / "home" / "home.json"), controller_factory=factory, clock=lambda: clock[0])
    hk.fc, hk.clock_box = fc, clock
    return hk


def test_discovery_says_which_accessories_can_be_paired_here(kit):
    found = {d["id"]: d for d in kit.discover(1)}
    assert found["aa:bb:cc:00:00:01"]["paired"] is False and found["10:a6:d6:61:9b:8a"]["paired"] is True and found["aa:bb:cc:00:00:01"]["host"] == "192.168.0.9"


def test_pairing_stores_the_key_and_the_names_but_never_the_setup_code(kit):
    assert kit.pair("aa:bb:cc:00:00:01", "12345678", "Living room dimmer", "Living room") == "Paired Living room dimmer."
    assert kit.fc.codes == ["123-45-678"]                                           # eight digits become the hyphenated form HomeKit wants
    raw = kit.store.path.read_text(encoding="utf-8")
    assert "123-45-678" not in raw and "12345678" not in raw and "iOSDeviceLTSK" in raw
    assert json.loads(raw)["devices"]["aa:bb:cc:00:00:01"]["name"] == "Living room dimmer"
    for bad in ("1234", "abcdefgh", "", "123-45-67890"):
        with pytest.raises(HomeError, match="eight digits"):
            kit.pair("aa:bb:cc:00:00:01", bad, "x")
    with pytest.raises(HomeError):                                                  # a wrong code or a device that is not there is a reason, not a crash
        kit.pair("aa:bb:cc:00:00:01", "999-99-999", "x")
    with pytest.raises(HomeError, match="cannot see"):
        kit.pair("ff:ff:ff:ff:ff:ff", "123-45-678", "x")


def test_a_paired_dimmer_is_a_light_with_a_name_a_room_and_a_state(kit):
    kit.pair("aa:bb:cc:00:00:01", "123-45-678", "Living room dimmer", "Living room")
    pairing = kit.fc.make_pairing()
    kit.fc.pairings["aa:bb:cc:00:00:01"] = pairing
    pairing.values = {(1, 10): True, (1, 11): 40}
    (state,) = kit.states(fresh=True)
    assert state["entity_id"] == "light.living_room_dimmer" and state["state"] == "on" and state["attributes"]["friendly_name"] == "Living room dimmer"
    assert state["attributes"]["brightness"] == round(40 * 255 / 100)
    assert kit.areas() == {"light.living_room_dimmer": "Living room"}
    assert kit.state_of("living room dimmer") == "Living room dimmer is on, at 40 percent."


def test_controlling_by_name_writes_the_right_characteristics(kit):
    kit.pair("aa:bb:cc:00:00:01", "123-45-678", "Living room dimmer", "Living room")
    pairing = kit.fc.make_pairing()
    kit.fc.pairings["aa:bb:cc:00:00:01"] = pairing
    assert kit.control("dimmer", "on").startswith("Done: Living room dimmer")
    assert pairing.writes[-1] == [(1, 10, True)]
    kit.control("the living room dimmer", "set brightness", 25)
    assert pairing.writes[-1] == [(1, 10, True), (1, 11, 25)]
    kit.control("dimmer", "off")
    assert pairing.writes[-1] == [(1, 10, False)]
    kit.control("dimmer", "toggle")
    assert pairing.writes[-1] == [(1, 10, True)]                                    # it was off, so toggle turns it on


def test_a_switch_or_outlet_is_a_switch_and_two_devices_with_the_same_name_ask_which(kit):
    kit.pair("aa:bb:cc:00:00:01", "123-45-678", "Lamp", "Study")
    kit.fc.pairings["aa:bb:cc:00:00:01"] = kit.fc.make_pairing()
    store = kit.store.load()
    store["devices"]["aa:bb:cc:00:00:02"] = {"name": "Lamp", "area": "Office", "pairing": {"k": 1}}
    kit.store.save(store)
    kit.invalidate()
    said = kit.control("lamp", "on")
    assert "do not guess" in said and "Done" not in said
    assert kit.control("lamp in the office", "on").startswith("Done") and kit.fc.pairings["aa:bb:cc:00:00:02"].writes == [[(2, 20, True)]]
    assert {s["entity_id"] for s in kit.states(fresh=True)} == {"light.lamp", "switch.lamp"}


def test_an_unreachable_device_is_skipped_so_the_others_still_answer(kit, monkeypatch):
    kit.pair("aa:bb:cc:00:00:01", "123-45-678", "Dimmer", "")
    store = kit.store.load()
    store["devices"]["aa:bb:cc:00:00:02"] = {"name": "Plug", "area": "", "pairing": {"k": 1}}
    kit.store.save(store)
    kit.fc.pairings["aa:bb:cc:00:00:01"] = kit.fc.make_pairing()
    broken = FakePairing(kit.fc.accessories["aa:bb:cc:00:00:02"])

    async def boom(*a, **k):
        raise ConnectionError("switch is off the network")
    broken.get_characteristics = boom
    kit.fc.pairings["aa:bb:cc:00:00:02"] = broken
    assert [s["entity_id"] for s in kit.states(fresh=True)] == ["light.dimmer"]
    assert "cannot find anything" in kit.control("plug", "on")


def test_the_guard_rails_are_the_houses_and_a_lock_like_name_is_still_just_a_light(kit):
    kit.pair("aa:bb:cc:00:00:01", "123-45-678", "Front door light", "")
    kit.fc.pairings["aa:bb:cc:00:00:01"] = kit.fc.make_pairing()
    assert kit.control("front door light", "on").startswith("Done")                # a light is a light, whatever it is called
    assert "cannot find anything" in kit.control("front door lock", "unlock")


def test_unpair_and_label(kit):
    kit.pair("aa:bb:cc:00:00:01", "123-45-678", "Old name", "")
    kit.label("aa:bb:cc:00:00:01", name="New name", area="Hall")
    assert kit.store.load()["devices"]["aa:bb:cc:00:00:01"]["name"] == "New name"
    with pytest.raises(HomeError):
        kit.label("zz", name="x")
    kit.unpair("aa:bb:cc:00:00:01")
    assert kit.store.load()["devices"] == {}


def test_the_command_line_scans_pairs_lists_and_tries_a_control(kit):
    lines = []
    run = lambda *argv: homectl.main(list(argv), hk=kit, out=lines.append)       # noqa: E731
    assert run("scan") == 0 and any("unpaired: can be paired here" in l for l in lines) and any("paired elsewhere" in l for l in lines)
    assert run("pair", "aa:bb:cc:00:00:01", "123-45-678", "Dimmer", "--area", "Hall") == 0 and lines[-1] == "Paired Dimmer."
    assert run("pair", "aa:bb:cc:00:00:01", "000", "Dimmer") == 1 and "eight digits" in lines[-1]
    kit.fc.pairings["aa:bb:cc:00:00:01"] = kit.fc.make_pairing()
    assert run("list") == 0 and "light.dimmer" in lines[-1] and "Hall" in lines[-1]
    assert run("say", "dimmer", "on") == 0 and lines[-1].startswith("Done")
    assert run("scan") == 0 and any("ours" in l for l in lines)


def test_identify_flashes_an_unpaired_device_by_its_id_and_a_paired_one_by_its_name(kit):
    assert kit.identify(accessory_id="aa:bb:cc:00:00:01") == "It is flashing." and kit.fc.identified == ["aa:bb:cc:00:00:01"]
    kit.pair("aa:bb:cc:00:00:01", "123-45-678", "Dimmer")
    kit.fc.pairings["aa:bb:cc:00:00:01"] = pairing = kit.fc.make_pairing()
    assert kit.identify(eid="light.dimmer") == "It is flashing." and getattr(pairing, "identified", False)
    with pytest.raises(homekit.HomeError, match="cannot see"):
        kit.identify(accessory_id="ff:ff:ff:ff:ff:ff")


def test_the_state_file_is_in_the_git_ignored_state_folder_by_default(monkeypatch):
    monkeypatch.delenv("SKIPPY_HOME_STATE", raising=False)
    assert homectl.store_path({}).parts[-3:] == ("state", "home", "home.json")
