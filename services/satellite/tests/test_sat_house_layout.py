"""The house's layout (house_layout.json): one source for floors, rooms, their spoken names, and every device's room."""
import json
from pathlib import Path

import house_layout
from brain import Brain
from house import Combined, House, HomeError
from timers import Timers

REAL = house_layout.load(house_layout.FILE)


class Lights(House):
    """A backend with no rooms of its own, like Leviton's cloud: only names."""

    def __init__(self, names, layout=None, fail=()):
        self.rows = [{"entity_id": "light." + house_layout.slug(n), "state": "off", "attributes": {"friendly_name": n}} for n in names]
        self.layout, self.calls, self.fail = layout, [], set(fail)

    def states(self, fresh=False):
        return self.rows

    def _service(self, domain, service, data):
        if data["entity_id"] in self.fail:
            raise HomeError("no answer")
        self.calls.append((domain, service, data["entity_id"], data.get("brightness_pct")))


HOUSE = ["kitchen under 1", "Deck sliding door", "living track middle", "living track throne", "kitchen under 2", "kitchen under 3",
         "track stove light", "security Light", "Bedroom"]


def test_the_file_is_sound():
    data = json.loads(house_layout.FILE.read_text(encoding="utf-8"))
    for floor, d in data["floors"].items():
        for r in d["rooms"]:
            assert r in data["rooms"], f"{floor} lists {r}, which is not a room"
    placed = [r for d in data["floors"].values() for r in d["rooms"]]
    assert sorted(placed) == sorted(data["rooms"]), "every room is on exactly one floor"
    for name, room in data["rooms"].items():
        for d in room["devices"]:
            assert d.get("kind") and (d.get("id") or d.get("name")), f"{name}: a device needs a kind and a name"


def test_places_floors_aliases_and_everywhere():
    assert REAL.place("upstairs") == ["living room", "kitchen", "bedroom"]
    assert REAL.place("the theater") == ["theater"]
    assert REAL.place("sony") == ["theater"]
    assert sorted(REAL.place("everywhere")) == sorted(REAL.rooms)
    assert REAL.place("the upstairs bedroom") == ["bedroom"]          # the longest phrase wins over "upstairs"
    assert REAL.place("kitchen under 1") is None                       # a place plus more words is a name, not a place
    assert REAL.split("upstairs lights") == (["living room", "kitchen", "bedroom"], "lights")


def test_outputs_are_router_kinds():
    assert REAL.outputs("upstairs") == ["enclave"]
    assert REAL.outputs("living room") == ["enclave"]
    assert REAL.outputs("enclave") == ["enclave"]
    assert REAL.outputs("downstairs") == ["hdmi"]
    assert REAL.outputs("theater") == ["hdmi"]
    assert REAL.outputs("sony") == ["hdmi"]
    assert sorted(REAL.outputs("everywhere")) == ["enclave", "hdmi"]
    assert REAL.outputs("the office") == []                            # a room with no speaker of its own
    assert REAL.outputs("pink floyd") is None


def test_rooms_of_devices_and_pods():
    assert REAL.room_of("light.living_track_throne", "living track throne") == "living room"
    assert REAL.room_of("switch.bedroom", "Bedroom") == "bedroom"     # by its name, whatever domain the backend gives it
    assert REAL.room_of("light.lamp", "Lamp", area="Theater") == "theater"   # a backend's own room, read through the aliases
    assert REAL.room_of("light.x", "Nowhere thing") == ""
    assert REAL.room_of_pod("office") == "theater" and REAL.room_of_pod("theater") == "theater"


def test_the_upstairs_lights_turn_on():
    """The bug (2026-11-01): 'turn on the upstairs lights' -> 'No lights found upstairs', because Leviton's names know no floors."""
    house = Lights(HOUSE, layout=REAL)
    said = house.control("upstairs lights", "on")
    on = {c[2] for c in house.calls}
    assert on == {"light.living_track_middle", "light.living_track_throne", "light.kitchen_under_1", "light.kitchen_under_2",
                  "light.kitchen_under_3", "light.track_stove_light", "light.bedroom"}
    assert said.startswith("Done: upstairs lights, on")


def test_group_words_and_single_names():
    house = Lights(HOUSE, layout=REAL)
    house.control("the kitchen lights", "off")
    assert {c[2] for c in house.calls} == {"light.kitchen_under_1", "light.kitchen_under_2", "light.kitchen_under_3", "light.track_stove_light"}
    house.calls.clear()
    house.control("kitchen under 1", "on")                             # one thing's name: one thing
    assert [c[2] for c in house.calls] == ["light.kitchen_under_1"]
    house.calls.clear()
    house.control("bedroom", "on")                                     # a thing whose own name is the room
    assert [c[2] for c in house.calls] == ["light.bedroom"]
    house.calls.clear()
    house.control("all the lights", "off")
    assert len(house.calls) == len(HOUSE)
    house.calls.clear()
    house.control("living room lights", "set_brightness", 40)
    assert {(c[2], c[3]) for c in house.calls} == {("light.living_track_middle", 40), ("light.living_track_throne", 40)}


def test_group_says_what_failed_and_what_it_cannot_do():
    house = Lights(HOUSE, layout=REAL, fail={"light.bedroom"})
    said = house.control("upstairs lights", "on")
    assert "did not answer: Bedroom" in said
    assert "only switch" in house.control("upstairs lights", "set_temperature", 70)
    before = len(house.calls)
    assert "cannot find" in house.control("office lights", "on") and len(house.calls) == before   # no lights there: nothing switched


def test_devices_by_floor():
    house = Lights(HOUSE, layout=REAL)
    listed = house.devices(area="upstairs")
    assert "living track throne" in listed and "Bedroom" in listed and "security Light" not in listed
    assert "security Light" in house.devices(area="outside")
    assert house.devices(area="office") == "Nothing matches in the house."


def test_combined_backends_share_the_layout():
    a, b = Lights(["living track middle"]), Lights(["Bedroom"])
    house = Combined([a, b])
    house.layout = REAL
    house.control("upstairs lights", "on")
    assert a.calls and b.calls


def test_layout_file_is_reread_when_changed(tmp_path, monkeypatch):
    p = tmp_path / "layout.json"
    p.write_text(json.dumps({"floors": {"up": {"rooms": ["den"]}}, "rooms": {"den": {"outputs": ["enclave"]}}}), encoding="utf-8")
    monkeypatch.setenv("SKIPPY_HOUSE_LAYOUT", str(p))
    assert house_layout.default().outputs("up") == ["enclave"]
    import os
    p.write_text(json.dumps({"floors": {"up": {"rooms": ["den"]}}, "rooms": {"den": {"outputs": ["hdmi"]}}}), encoding="utf-8")
    os.utime(p, ns=(1, 2_000_000_000_000_000_000))
    assert house_layout.default().outputs("up") == ["hdmi"]


def test_a_missing_file_is_an_empty_house(tmp_path):
    lay = house_layout.load(tmp_path / "none.json")
    assert lay.place("upstairs") is None and lay.outputs("everywhere") == []


def test_skippy_knows_the_house():
    brain = Brain(client=None, model="m", timers=Timers(), persona="P")
    brain.room = "office"
    text = brain.system()
    assert "## The house" in text and "upstairs: living room" in text and "Enclave speakers" in text
    assert "talking to you from the theater (downstairs)" in text
