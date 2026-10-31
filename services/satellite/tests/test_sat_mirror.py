"""Every pod shows him speaking at the same time: a pod that speaks tells the others (silently), which animate the same mouth, mood and rocking."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import esphome_link as L  # noqa: E402


class FakeClient:
    def __init__(self):
        self.texts, self.calls = [], []

    def text_command(self, key, text):
        self.texts.append((key, text))

    async def execute_service(self, svc, data):
        self.calls.append((svc.name, data))


def pod(name, hub):
    link = L.EsphomeLink(FakeClient(), None, "10.0.0.1")
    link.room = SimpleNamespace(name=name, link=link)
    link.keys = {"mouth": 7, "emote": 8}
    link.services = [SimpleNamespace(name="face")]
    link.hub = hub
    return link


def test_a_pod_mirrors_the_speech_of_the_others_silently():
    hub = SimpleNamespace(rooms={})
    a, b = pod("office", hub), pod("kitchen", hub)
    hub.rooms = {"office": a.room, "kitchen": b.room}
    assert a.others() == [b] and b.others() == [a]

    async def go():
        await b.mirror("nDDDDBBBXX", "q|QQQQ", 0.0)
    asyncio.run(go())
    assert (7, "nDDDDBBBXX") in b.client.texts and (8, "q|QQQQ") in b.client.texts      # the mouth cues and the emotion track
    assert [c[0] for c in b.client.calls] == ["face", "face"]
    assert b.client.calls[0][1] == {"state": "speaking"} and b.client.calls[1][1] == {"state": "idle"}
    assert a.client.calls == []                                                          # the speaker's own box is not told twice


def test_a_model_chooses_the_face_for_each_sentence_and_the_track_follows_the_clip():
    import emotion as E
    said = "Oh, how thrilling. I am here! Really?"
    assert E.parse_codes("m, H\nq", 3) == "mHq" and E.parse_codes("only two", 2) is None and E.parse_codes("mH", 3) is None
    track = E.track_from_codes(said, "mHq", 6.0)
    assert len(track) == 60 and track[0] == "m" and track[-1] == "q" and "H" in track
