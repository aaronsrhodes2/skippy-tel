"""Matter through skippy-core's controller (matter.py), against a fake python-matter-server over a real WebSocket: its first message
is server_info, every command is {"message_id", "command", "args"}, every answer {"message_id", "result"} or {..., "error_code"}.
The nodes are what the real controller reports: attribute paths "endpoint/cluster/attribute", struct fields by id ("0") or by name."""
import asyncio
import threading

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

import homectl  # noqa: E402
import matter as matter_mod  # noqa: E402
from house import Combined, HomeError  # noqa: E402
from matter import Matter, Names  # noqa: E402


def plug(node_id=1, on=False):
    return {"node_id": node_id, "available": True, "attributes": {
        "0/29/0": [{"0": 22, "1": 1}], "0/40/1": "Amazon", "0/40/3": "Smart Plug",
        "1/29/0": [{"0": 266, "1": 1}], "1/6/0": on}}


def light(node_id=2):
    return {"node_id": node_id, "available": True, "attributes": {
        "0/29/0": [{"0": 22, "1": 1}], "0/40/5": "Reading Lamp",
        "1/29/0": [{"0": 257, "1": 1}], "1/6/0": True, "1/8/0": 127}}


def leviton(node_id=5):
    """A D2MSD: a dimmer with a motion sensor on a second endpoint, and Identify on the dimmer's."""
    return {"node_id": node_id, "available": True, "attributes": {
        "0/29/0": [{"0": 22, "1": 1}], "0/40/1": "Leviton", "0/40/5": "Kitchen Light",
        "1/29/0": [{"0": 257, "1": 1}], "1/6/0": False, "1/8/0": 254, "1/3/0": 0,
        "2/29/0": [{"0": 263, "1": 1}], "2/1030/0": 1}}


def nest(node_id=3, mode=4, fahrenheit=True):
    return {"node_id": node_id, "available": True, "attributes": {
        "0/29/0": [{"deviceType": 22, "revision": 1}], "0/40/3": "Nest Thermostat",
        "1/29/0": [{"deviceType": 769, "revision": 1}],                 # struct fields by name: the other shape the controller sends
        "1/513/0": 2100, "1/513/17": 2500, "1/513/18": 2000, "1/513/28": mode, "1/516/0": 1 if fahrenheit else 0}}


class FakeController:
    """python-matter-server's WebSocket API, the parts the hub uses."""

    def __init__(self, nodes):
        self.nodes = {n["node_id"]: n for n in nodes}
        self.calls, self.wifi, self.swallow = [], None, 0
        self.fail, self.next_node = {}, 7

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"fabric_id": 1, "compressed_fabric_id": 99, "schema_version": 11, "min_supported_schema_version": 9,
                            "sdk_version": "2025.7", "wifi_credentials_set": self.wifi is not None, "thread_credentials_set": False,
                            "bluetooth_enabled": True})
        async for msg in ws:
            req = msg.json()
            self.calls.append((req["command"], req.get("args") or {}))
            await ws.send_json({"event": "node_updated", "data": {}})       # events arrive between answers: the client skips them
            if req["command"] == "commission_with_code" and (req.get("args") or {}).get("code") in self.fail.get("bad_codes", ()):
                await ws.send_json({"message_id": req["message_id"], "error_code": 5, "details": "Commissioning failed: timed out"})
                continue
            if req["command"] in self.fail and req["command"] != "bad_codes":
                await ws.send_json({"message_id": req["message_id"], "error_code": 5, "details": self.fail[req["command"]]})
                continue
            await ws.send_json({"message_id": req["message_id"], "result": self.run(req["command"], req.get("args") or {})})
        return ws

    def run(self, command, a):
        if command == "server_info":
            return {"fabric_id": 1, "sdk_version": "2025.7", "wifi_credentials_set": self.wifi is not None, "bluetooth_enabled": True}
        if command == "get_nodes":
            return list(self.nodes.values())
        if command == "device_command":
            attrs = self.nodes[a["node_id"]]["attributes"]
            ep = a["endpoint_id"]
            if a["cluster_id"] == 6:
                if self.swallow:
                    self.swallow -= 1
                    return None
                key = f"{ep}/6/0"
                attrs[key] = {"On": True, "Off": False, "Toggle": not attrs[key]}[a["command_name"]]
            if a["cluster_id"] == 8:
                attrs[f"{ep}/8/0"], attrs[f"{ep}/6/0"] = a["payload"]["level"], True
            return None
        if command == "read_attribute":
            return {a["attribute_path"]: self.nodes[a["node_id"]]["attributes"].get(a["attribute_path"])}
        if command == "write_attribute":
            self.nodes[a["node_id"]]["attributes"][a["attribute_path"]] = a["value"]
            return [{"Path": {}, "Status": 0}]
        if command == "set_wifi_credentials":
            self.wifi = (a["ssid"], a["credentials"])
            return None
        if command == "commission_with_code":
            new = plug(node_id=self.next_node)
            self.nodes[self.next_node] = new
            self.next_node += 1
            return new
        if command == "remove_node":
            self.nodes.pop(a["node_id"], None)
            return None
        raise AssertionError(command)


@pytest.fixture
def controller():
    """The fake controller on a real port, in a loop of its own (the backend's calls are synchronous, as the brain's are)."""
    ctl = FakeController([plug(), light(), nest(), {**plug(node_id=4), "available": False}])
    loop = asyncio.new_event_loop()
    started = threading.Event()
    box = {}

    async def serve():
        app = web.Application()
        app.router.add_get("/ws", ctl.handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        box["url"] = f"ws://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/ws"
        box["runner"] = runner
        started.set()

    threading.Thread(target=lambda: (loop.run_until_complete(serve()), loop.run_forever()), daemon=True).start()
    started.wait(5)
    ctl.url = box["url"]
    yield ctl                                               # (its loop runs on in a daemon thread: the backend's WebSocket stays open, as it should)


@pytest.fixture
def house(controller, tmp_path, monkeypatch):
    monkeypatch.setattr(matter_mod, "RETRY_SECONDS", 0)
    names = Names(tmp_path / "home" / "matter.json")
    names.save({"nodes": {"1": {"name": "Theater Fan", "area": "Theater"}}})
    return Matter(controller.url, names)


def test_nodes_become_a_switch_a_light_and_a_thermostat_in_his_units_and_an_unavailable_one_is_left_out(house):
    rows = {s["entity_id"]: s for s in house.states(fresh=True)}
    assert set(rows) == {"switch.theater_fan", "light.reading_lamp", "climate.nest_thermostat"}
    assert rows["switch.theater_fan"]["state"] == "off" and house.areas() == {"switch.theater_fan": "Theater"}
    assert rows["light.reading_lamp"]["attributes"]["brightness"] == round(127 * 255 / 254)
    t = rows["climate.nest_thermostat"]
    assert t["state"] == "heat" and t["attributes"]["current_temperature"] == 70 and t["attributes"]["temperature"] == 68
    assert t["attributes"]["unit_of_measurement"] == "°F"
    assert house.state_of("thermostat") == "Nest Thermostat is heat °F, currently 70 degrees, set to 68."


def test_the_fan_switches_by_his_words_and_each_change_is_read_back_from_the_device(house, controller):
    assert house.control("the fan", "on").startswith("Done: Theater Fan")
    assert controller.nodes[1]["attributes"]["1/6/0"] is True and house.last["attempts"] == 1
    sent = [a for c, a in controller.calls if c == "device_command"]
    assert sent[-1] == {"node_id": 1, "endpoint_id": 1, "cluster_id": 6, "command_name": "On", "payload": {}}
    assert ("read_attribute", {"node_id": 1, "attribute_path": "1/6/0"}) in controller.calls
    house.control("theater fan", "toggle")
    assert controller.nodes[1]["attributes"]["1/6/0"] is False


def test_a_device_that_ignored_the_first_command_is_asked_again_and_one_that_never_changes_is_reported(house, controller):
    controller.swallow = 1
    assert house.control("theater fan", "on").startswith("Done") and house.last["attempts"] == 2
    controller.swallow = 9
    with pytest.raises(HomeError, match="twice"):
        house.control("theater fan", "off")


def test_brightness_is_a_level_command(house, controller):
    house.control("reading lamp", "set_brightness", 50)
    assert controller.nodes[2]["attributes"]["1/8/0"] == 127
    (cmd,) = [a for c, a in controller.calls if c == "device_command"]
    assert cmd["cluster_id"] == 8 and cmd["command_name"] == "MoveToLevelWithOnOff" and cmd["payload"]["transitionTime"] == 0


def test_the_nest_is_set_in_the_unit_it_shows_and_within_sane_limits(house, controller):
    house.control("thermostat", "set_temperature", 72)                        # Fahrenheit, as the Nest shows it
    assert controller.nodes[3]["attributes"]["1/513/18"] == round((72 - 32) * 5 / 9 * 100)
    assert "cannot" in house.control("thermostat", "set_temperature", 120)    # nobody sets a house to 120
    house.control("thermostat", "off")
    assert controller.nodes[3]["attributes"]["1/513/28"] == 0
    house.control("thermostat", "on")
    assert controller.nodes[3]["attributes"]["1/513/28"] == 4


def test_heat_and_cool_keeps_its_band_around_what_he_asked_for(controller, tmp_path):
    controller.nodes[3] = nest(mode=1, fahrenheit=False)
    h = Matter(controller.url, Names(tmp_path / "m.json"))
    h.control("thermostat", "set_temperature", 21)
    assert controller.nodes[3]["attributes"]["1/513/18"] == 2000 and controller.nodes[3]["attributes"]["1/513/17"] == 2200
    with pytest.raises(HomeError, match="outside"):
        h.control("thermostat", "set_temperature", 50)                        # 50 °C


def test_pairing_checks_the_code_and_keeps_his_name_for_it(house, controller):
    for bad in ("1234", "abc", ""):
        with pytest.raises(HomeError, match="11 digits"):
            house.pair(bad, "x")
    house.set_wifi("HomeNet", "secret")
    assert house.pair("3497-011-2332", "Kitchen Plug", "Kitchen") == "Paired Kitchen Plug."
    assert controller.wifi == ("HomeNet", "secret")
    assert ("commission_with_code", {"code": "34970112332", "network_only": False}) in controller.calls
    assert {s["entity_id"] for s in house.states()} >= {"switch.kitchen_plug"}
    house.pair("MT:Y.K90AFN00KA0648G00", "Nest", network_only=True)
    last = [a for c, a in controller.calls if c == "commission_with_code"][-1]
    assert last == {"code": "MT:Y.K90AFN00KA0648G00", "network_only": True}     # the Nest: already on the network, no Bluetooth


def test_the_controllers_refusal_is_said_plainly(house, controller):
    controller.fail["commission_with_code"] = "Commissioning failed: device not found"
    with pytest.raises(HomeError, match="device not found"):
        house.pair("34970112332", "x")


def test_homekit_and_matter_are_one_house_each_thing_driven_by_its_own_backend(house, controller):
    class Kit(Combined.__mro__[1]):                                           # a House with one HomeKit lamp
        def __init__(self):
            self.done, self.on = [], False

        def states(self, fresh=False):
            return [{"entity_id": "light.living_room_dimmer", "state": "on" if self.on else "off", "attributes": {"friendly_name": "Living room dimmer"}}]

        def _service(self, domain, service, data):
            self.done.append(service)
            self.on = service == "turn_on"
    kit = Kit()
    both = Combined([kit, house])
    assert {s["entity_id"] for s in both.states()} == {"light.living_room_dimmer", "switch.theater_fan", "light.reading_lamp", "climate.nest_thermostat"}
    both.control("living room dimmer", "on")
    both.control("theater fan", "on")
    assert kit.done == ["turn_on"] and controller.nodes[1]["attributes"]["1/6/0"] is True and both.last["attempts"] == 1


def test_one_backend_that_is_down_never_hides_the_other(house):
    class Down(Combined.__mro__[1]):
        def states(self, fresh=False):
            raise HomeError("HomeKit is asleep")
    both = Combined([Down(), house])
    assert "switch.theater_fan" in {s["entity_id"] for s in both.states()}


def test_the_command_line_pairs_lists_and_proves_matter_devices(house, controller, tmp_path):
    lines = []
    env = {"MATTER_URL": controller.url, "ROOM_WIFI_SSID": "HomeNet", "ROOM_WIFI_PASSWORD": "pw", "SKIPPY_HOME_STATE": str(tmp_path / "hk")}

    class NoKit(Combined.__mro__[1]):
        def states(self, fresh=False):
            return []
    run = lambda *argv: homectl.main(list(argv), hk=NoKit(), out=lines.append, mt=house, env=env, sleep=lambda s: None)   # noqa: E731
    assert run("matter-info") == 0 and "Bluetooth (for brand-new devices): ready" in "\n".join(lines)
    assert run("matter-pair", "34970112332", "Den Plug", "--area", "Den") == 0 and lines[-1] == "Paired Den Plug." and controller.wifi == ("HomeNet", "pw")
    assert run("list") == 0 and any("switch.theater_fan" in line and "Theater" in line for line in lines)
    assert run("prove", "theater fan", "--rounds", "2", "--gap", "0") == 0 and lines[-1].startswith("Theater Fan: 4/4 worked")
    assert homectl.main(["matter-pair", "34970112332", "X"], hk=NoKit(), out=lines.append, mt=house, env={"MATTER_URL": "x"}) == 1
    assert "ROOM_WIFI_SSID" in lines[-1]                                      # a new device needs to know which Wi-Fi to join
    assert homectl.main(["matter-info"], hk=NoKit(), out=lines.append, env={}) == 1 and "MATTER_URL" in lines[-1]


def test_the_listener_builds_a_matter_house_from_the_vault(tmp_path, monkeypatch):
    import ear
    house = ear.build_house({"MATTER_URL": "ws://skippy-core:5580/ws", "SKIPPY_HOME_STATE": str(tmp_path)})
    assert isinstance(house, Matter) and house.url == "ws://skippy-core:5580/ws"
    assert ear.build_house({"SKIPPY_HOME_STATE": str(tmp_path)}) is None


def test_a_motion_dimmer_is_a_light_and_a_read_only_motion_sensor(house, controller):
    controller.nodes[5] = leviton()
    rows = {s["entity_id"]: s for s in house.states(fresh=True)}
    assert rows["light.kitchen_light"]["state"] == "off"
    motion = rows["binary_sensor.kitchen_light_motion"]
    assert motion["state"] == "on" and motion["attributes"]["device_class"] == "occupancy"
    with pytest.raises(HomeError, match="can only be read"):
        house._service("binary_sensor", "turn_on", {"entity_id": "binary_sensor.kitchen_light_motion"})
    controller.nodes[5]["attributes"]["2/1030/0"] = 0
    assert {s["entity_id"]: s["state"] for s in house.states(fresh=True)}["binary_sensor.kitchen_light_motion"] == "off"


def test_identify_flashes_the_endpoint_that_has_identify_or_the_things_own(house, controller):
    controller.nodes[5] = leviton()
    house.states(fresh=True)
    assert "flashing" in house.identify("light.kitchen_light")
    flashes = [a for c, a in controller.calls if c == "device_command" and a["cluster_id"] == 3]
    assert flashes == [{"node_id": 5, "endpoint_id": 1, "cluster_id": 3, "command_name": "Identify", "payload": {"identifyTime": 15}}]
    house.identify("switch.theater_fan", seconds=5)                           # no Identify listed: its own endpoint
    assert [a for c, a in controller.calls if c == "device_command" and a["cluster_id"] == 3][-1]["endpoint_id"] == 1
    with pytest.raises(HomeError, match="do not know"):
        house.identify("light.nowhere")


def test_the_import_list_reads_commas_tabs_and_bars_and_says_which_line_is_wrong():
    rows = matter_mod.parse_import("# from the My Leviton app\n3497-011-2332, Kitchen Light, Kitchen\n\n1234-567-8901\tHall Light\n"
                                   "MT:Y.K90AFN00KA0648G00 | Porch | Outside  # by the door\n")
    assert rows == [("3497-011-2332", "Kitchen Light", "Kitchen"), ("1234-567-8901", "Hall Light", ""), ("MT:Y.K90AFN00KA0648G00", "Porch", "Outside")]
    with pytest.raises(HomeError, match="line 2"):
        matter_mod.parse_import("34970112332, A\njust-a-code\n")


def test_matter_import_pairs_them_all_goes_on_past_a_failure_and_skips_the_done_ones_next_time(house, controller, tmp_path):
    lines = []
    listing = tmp_path / "levitons.txt"
    listing.write_text("34970112332, Kitchen Light, Kitchen\n11111111111, Hall Light, Hall\n22222222222, Den Light, Den\n", encoding="utf-8")
    controller.fail["bad_codes"] = {"11111111111"}

    class NoKit(Combined.__mro__[1]):
        def states(self, fresh=False):
            return []
    run = lambda *argv, env=None: homectl.main(list(argv), hk=NoKit(), out=lines.append, mt=house,   # noqa: E731
                                               env=env or {"MATTER_URL": controller.url}, sleep=lambda s: None)
    assert run("matter-import", str(listing)) == 1
    assert lines[-4:] == ["1/3 Kitchen Light: Paired Kitchen Light.", "2/3 Hall Light: FAILED: Matter said no: Commissioning failed: timed out.",
                          "3/3 Den Light: Paired Den Light.", lines[-1]]
    assert lines[-1].startswith("2 paired, 0 already done, 1 failed.")
    pairs = [a for c, a in controller.calls if c == "commission_with_code"]
    assert all(a["network_only"] for a in pairs)                              # already on the Wi-Fi: no Bluetooth, no ladder
    assert "34970112332" not in (tmp_path / "home" / "matter.json").read_text()       # the codes themselves are never kept
    controller.fail["bad_codes"] = set()
    assert run("matter-import", str(listing)) == 0
    assert lines[-1] == "1 paired, 2 already done, 0 failed." and len([c for c, _ in controller.calls if c == "commission_with_code"]) == 4
    rows = {s["entity_id"]: s for s in house.states(fresh=True)}
    assert "switch.hall_light" in rows and {"name": "Hall Light", "area": "Hall"} in house.names.load()["nodes"].values()
    assert run("matter-import", str(listing), "--new") == 1 and "ROOM_WIFI_SSID" in lines[-1]   # brand-new ones need the Wi-Fi


def test_identify_from_the_command_line_finds_it_by_name_in_whichever_backend_has_it(house, controller):
    lines = []
    controller.nodes[5] = leviton()

    class Kit(Combined.__mro__[1]):
        flashed = []

        def states(self, fresh=False):
            return [{"entity_id": "light.living_room_dimmer", "state": "off", "attributes": {"friendly_name": "Living room dimmer"}}]

        def identify(self, accessory_id="", eid=""):
            self.flashed.append(accessory_id or eid)
            return "It is flashing."
    kit = Kit()
    run = lambda *argv: homectl.main(list(argv), hk=kit, out=lines.append, mt=house, env={"MATTER_URL": controller.url})   # noqa: E731
    assert run("identify", "kitchen light") == 0 and lines[-1].startswith("Kitchen Light") and "flashing" in lines[-1]
    assert any(c == "device_command" and a["cluster_id"] == 3 for c, a in controller.calls)
    assert run("identify", "living room dimmer") == 0 and kit.flashed == ["light.living_room_dimmer"]
    assert run("identify", "AA:BB:CC:00:00:01") == 0 and kit.flashed[-1] == "aa:bb:cc:00:00:01"     # an unpaired one, by its id from scan
    assert run("identify", "nothing like it") == 1
