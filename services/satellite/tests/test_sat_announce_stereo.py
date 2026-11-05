"""The announce tool (hub.announce_tools) and the box's own speaker as the last resort (the Captain, 2026-11-04: "we never want to output to the hub
speakers unless that house level's stereo is unavailable"): another room's box whose room has its stereo on stays silent and the stereo says it;
with that stereo off, or dropping the clip, or only the machine's own voice to play, the box speaks."""
import asyncio
import threading

import pytest

import hub
import house_layout
import router as router_mod
import speaker_out

LAYOUT = house_layout.Layout({
    "floors": {"downstairs": {"rooms": ["theater", "den"]}, "upstairs": {"rooms": ["living room"]}},
    "rooms": {"theater": {"pods": ["theater"], "outputs": ["hdmi"]},
              "living room": {"pods": ["living"], "outputs": ["enclave"]},
              "den": {"pods": ["den"], "outputs": []}}})                       # a room with a box and a stereo of its own that the layout does not list
ENV = {"SATELLITE_OUT": "theater=pod+hdmi;living=pod+enclave;den=pod+bt"}


class Link:
    def __init__(self):
        self.announced = []

    async def announce(self, text, voice):
        self.announced.append((text, voice))


class Box:
    def __init__(self, name, stereo=(), voice=("mp3", b"CLIP"), broken=False):
        self.name, self.link, self.stereo, self.voice, self.broken, self.logs = name, Link(), list(stereo), voice, broken, []

    def stereo_kinds(self):
        if self.broken:
            raise RuntimeError("router gone")
        return list(self.stereo)

    def log(self, m):
        self.logs.append(m)

    async def _voice(self, text):
        return self.voice


class FakeRouter:
    def __init__(self, open_):
        self.open = set(open_)

    def available(self, kind):
        return kind in self.open


class FakeOut:
    made = []
    plays = True                                        # what play() answers: False is a clip the router dropped

    def __init__(self, router, kinds, log=print, duck_others=True, **kw):
        self.router, self.kinds, self.played = router, list(kinds), []
        FakeOut.made.append(self)

    async def play(self, mp3, **kw):
        self.played.append(mp3)
        return FakeOut.plays


@pytest.fixture
def loop():
    lp = asyncio.new_event_loop()
    thread = threading.Thread(target=lp.run_forever, daemon=True)
    thread.start()
    yield lp
    lp.call_soon_threadsafe(lp.stop)
    thread.join(2)
    lp.close()


def announce(monkeypatch, loop, others, here=None, open_=("enclave",), place="upstairs", plays=True):
    FakeOut.made.clear()
    FakeOut.plays = plays
    monkeypatch.setattr(house_layout, "default", lambda: LAYOUT)
    monkeypatch.setattr(router_mod, "shared", lambda env, log=print: FakeRouter(open_))
    monkeypatch.setattr(speaker_out, "RoutedOut", FakeOut)
    here = here or Box("theater")
    connected = {"theater": here, **{box.name: box for box in others}}
    _, run = hub.announce_tools(lambda: here, connected, ENV, loop)
    said = run("announce", {"text": "Dinner is ready.", "place": place})
    asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05), loop).result(5)         # the scheduled plays run on the loop after the tool has answered
    return said


def test_a_box_whose_stereo_is_on_stays_silent_and_the_stereo_says_it(monkeypatch, loop):
    living = Box("living", stereo=["enclave"])
    said = announce(monkeypatch, loop, [living])
    assert said == "Announced upstairs: Dinner is ready."
    assert [(o.kinds, o.played) for o in FakeOut.made] == [(["enclave"], [b"CLIP"])]      # once, on the Enclave
    assert living.link.announced == []                                                   # the box itself said nothing


def test_a_box_whose_stereo_is_off_speaks_itself(monkeypatch, loop):
    living = Box("living", stereo=[])
    said = announce(monkeypatch, loop, [living], open_=())
    assert said == "Announced living: Dinner is ready."
    assert [o.played for o in FakeOut.made] == [[]]                                      # the Enclave is not open: nothing played there
    assert living.link.announced == [("Dinner is ready.", ("mp3", b"CLIP"))]


def test_the_machines_own_voice_has_no_clip_for_the_stereo_so_the_box_speaks(monkeypatch, loop):
    living = Box("living", stereo=["enclave"])
    here = Box("theater", voice=("pcm", b"RAW", 16000))
    said = announce(monkeypatch, loop, [living], here=here)
    assert said == "Announced living: Dinner is ready."
    assert FakeOut.made == [] and living.link.announced == [("Dinner is ready.", ("pcm", b"RAW", 16000))]


def test_a_boxs_own_stereo_is_used_even_when_the_layout_lists_none_for_its_room(monkeypatch, loop):
    den = Box("den", stereo=["bt"])
    said = announce(monkeypatch, loop, [den], open_=("bt",), place="den")
    assert said == "Announced den: Dinner is ready."
    assert [(o.kinds, o.played) for o in FakeOut.made] == [(["bt"], [b"CLIP"])] and den.link.announced == []     # silent: its own stereo said it


def test_a_box_that_shares_the_askers_stereo_stays_silent_and_the_shared_stereo_says_it(monkeypatch, loop):
    """Two boxes on one level share its stereo: the announcement plays there (the asker hears it too) rather than on the other box's small speaker."""
    hall = Box("den", stereo=["hdmi"])
    here = Box("theater")                                                                # the asker: SATELLITE_OUT theater=pod+hdmi, so hdmi is its own
    said = announce(monkeypatch, loop, [hall], here=here, open_=("hdmi",), place="den")
    assert [(o.kinds, o.played) for o in FakeOut.made] == [(["hdmi"], [b"CLIP"])] and hall.link.announced == []
    assert said.startswith("Announced ")


def test_a_clip_the_stereo_dropped_is_said_by_the_box_it_stood_in_for(monkeypatch, loop):
    living = Box("living", stereo=["enclave"])
    announce(monkeypatch, loop, [living], plays=False)
    assert living.link.announced == [("Dinner is ready.", ("mp3", b"CLIP"))]


def test_a_stereo_that_went_off_since_the_look_means_the_box_speaks(monkeypatch, loop):
    living = Box("living", stereo=["enclave"])                                           # it looked on, then the router had nothing open
    said = announce(monkeypatch, loop, [living], open_=())
    assert said == "Announced living: Dinner is ready." and living.link.announced == [("Dinner is ready.", ("mp3", b"CLIP"))]


def test_a_stereo_that_cannot_be_looked_at_leaves_the_box_speaking(monkeypatch, loop):
    living = Box("living", broken=True)
    said = announce(monkeypatch, loop, [living], open_=())
    assert said == "Announced living: Dinner is ready." and living.link.announced == [("Dinner is ready.", ("mp3", b"CLIP"))]
