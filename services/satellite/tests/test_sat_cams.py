"""The cameras (cams.py) against a fake Frigate and a fake Reolink doorbell: Frigate's events become "someone's outside" (once per window, never
for what happened before the hub started), the doorbell's button becomes "someone's at the front door" in every room, his phone gets the
snapshot, home_state's words, read only, and build_house wiring. Nothing here reaches a camera."""
import asyncio
import base64
import json
import time
from types import SimpleNamespace

import pytest

import cams
import homectl
import house


class FakeFrigate(cams.Frigate):
    def __init__(self):
        super().__init__("http://frigate", get=self.answer)
        self.cameras = {"front_door": {"camera_fps": 5.0}, "driveway": {"camera_fps": 5.0}}
        self.list = []
        self.up = True
        self.asked = []

    def answer(self, path):
        self.asked.append(path)
        if not self.up:
            raise OSError("no route to skippy-core")
        if path == "/api/stats":
            return json.dumps({"cameras": self.cameras}).encode()
        if path.startswith("/api/events?"):
            return json.dumps(self.list).encode()
        if path.endswith("/snapshot.jpg?bbox=0"):
            return b"\xff\xd8event-" + path.split("/")[3].encode()
        if path.endswith("/latest.jpg?height=720"):
            return b"\xff\xd8latest-" + path.split("/")[2].encode()
        raise OSError(path)

    def event(self, eid, camera, label="person", at=None):
        self.list.append({"id": eid, "camera": camera, "label": label, "start_time": at if at is not None else time.time()})


def owner(frigate, now=None, **kw):
    clock = (lambda: now[0]) if now else time.time
    return cams.Cams(frigate, {"front_door": "192.168.1.60"}, owner=True, start=False, log=lambda m: None, clock=clock, **kw)


def test_what_happened_before_the_hub_started_fills_in_but_is_never_announced():
    f = FakeFrigate()
    f.event("old", "driveway", at=time.time() - 600)
    c = owner(f)
    c.refresh()
    assert c.take_events() == []
    assert c.state_of("driveway") == "Driveway is idle, last person 10 minutes ago."
    f.event("new", "driveway")
    c.refresh()
    c.refresh()                                                     # asked again: the same event is not news twice
    assert [(e["kind"], e["camera"], e["id"]) for e in c.take_events()] == [("person", "driveway", "new")]
    assert {s["entity_id"]: s["state"] for s in c.states()}["camera.driveway"] == "person"


def test_the_front_door_is_a_doorbell_and_says_so():
    c = owner(FakeFrigate())
    c.refresh()
    assert c.state_of("front door") == "Front door is idle, no doorbell press lately."
    c.ding("front_door")
    assert c.states()[0]["state"] == "ringing" and "last doorbell press just now" in c.state_of("front door")
    assert c.control("front door", "off") == "Front door can only be read, not changed."
    assert c.devices() == "Nothing matches in the house."            # never offered as something to switch
    with pytest.raises(house.HomeError):
        c._service("camera", "turn_off", {})


def test_a_camera_with_no_frames_or_frigate_away_is_unavailable():
    f = FakeFrigate()
    f.cameras["driveway"] = {"camera_fps": 0}
    c = owner(f)
    c.refresh()
    assert {s["entity_id"]: s["state"] for s in c.states()} == {"camera.front_door": "idle", "camera.driveway": "unavailable"}
    f.up = False
    c.refresh()
    assert all(s["state"] == "unavailable" for s in c.states())
    lonely = owner(f)
    lonely.refresh()
    with pytest.raises(house.HomeError, match="Frigate on skippy-core isn't answering"):
        lonely.states()


def test_the_doorbells_button_is_a_ding_once_per_press():
    """The push channel calls back on every message; only visitor False -> True is a press."""
    class FakeHost:
        def __init__(self):
            self.visitor = False
            self.callbacks = {}
            self.baichuan = SimpleNamespace(register_callback=lambda cid, cb, cmd, ch: self.callbacks.update({cid: (cb, cmd, ch)}),
                                            subscribe_events=self.ok, check_subscribe_events=self.ok, unsubscribe_events=self.ok)

        async def ok(self):
            pass

        async def get_host_data(self):
            pass

        async def logout(self):
            pass

        def visitor_detected(self, ch):
            return self.visitor
    host = FakeHost()
    c = cams.Cams(FakeFrigate(), {"front_door": "192.168.1.60"}, owner=True, start=False, log=lambda m: None, make_host=lambda h: host)

    async def go():
        task = asyncio.create_task(c._bell("front_door", "192.168.1.60"))
        await asyncio.sleep(0.05)
        cb, cmd, ch = host.callbacks["skippy-front_door"]
        assert (cmd, ch) == (None, 0)
        cb()                                                        # some other push (a motion): no press
        host.visitor = True
        cb()
        cb()                                                        # the same press, pushed again
        host.visitor = False
        cb()
        host.visitor = True
        cb()                                                        # pressed again
        task.cancel()
    asyncio.run(go())
    assert [(e["kind"], e["camera"]) for e in c.take_events()] == [("ding", "front_door"), ("ding", "front_door")]


class Room:
    def __init__(self):
        self.said = []

    async def announce(self, text):
        self.said.append(text)


def test_a_press_is_heard_everywhere_a_person_once_and_his_phone_gets_the_snapshot():
    f = FakeFrigate()
    c = owner(f)
    c.refresh()
    f.event("e1", "driveway")
    f.event("e2", "driveway")                                       # still the same visit: inside the window
    f.event("c1", "driveway", label="car")                          # cars stay quiet unless CAMS_ANNOUNCE_CARS
    c.refresh()
    c.ding("front_door")
    c.ding("front_door")
    dms = []
    watch = cams.Watch(c, lambda text, pic: dms.append((text, pic)), log=lambda m: None)
    hub = SimpleNamespace(rooms={"office": Room(), "kitchen": Room()})

    async def go():
        task = asyncio.create_task(watch.run(hub, lambda: "office", every=0.01))
        await asyncio.sleep(0.1)
        task.cancel()
    asyncio.run(go())
    assert hub.rooms["office"].said == ["Someone's outside, at the driveway.", "Someone's at the front door."]
    assert hub.rooms["kitchen"].said == ["Someone's at the front door."]
    for _ in range(100):
        if len(dms) == 2:
            break
        time.sleep(0.02)
    assert sorted(dms) == [("Someone's at the front door.", b"\xff\xd8latest-front_door"),
                           ("Someone's outside, at the driveway.", b"\xff\xd8event-e1")]


def test_cars_when_he_wants_them_and_the_window_lets_the_next_visit_through():
    now = [1000.0]
    f = FakeFrigate()
    c = owner(f, now)
    c.refresh()
    watch = cams.Watch(c, lambda t, p: None, cars=True, log=lambda m: None, clock=lambda: now[0])
    f.event("c1", "driveway", label="car", at=1000)
    c.refresh()
    assert [e["kind"] for e in watch.news()] == ["car"]
    f.event("c2", "driveway", label="car", at=1100)
    now[0] = 1100
    c.refresh()
    assert watch.news() == []
    f.event("c3", "driveway", label="car", at=1000 + cams.ANNOUNCE["car"])
    now[0] = 1000 + cams.ANNOUNCE["car"]
    c.refresh()
    assert len(watch.news()) == 1


def test_only_the_hub_listens_and_the_rest_read_frigate_when_asked():
    f = FakeFrigate()
    reader = cams.Cams(f, {}, owner=False, log=lambda m: None)
    assert {s["entity_id"] for s in reader.states()} == {"camera.front_door", "camera.driveway"}
    asked = len(f.asked)
    reader.states()
    assert len(f.asked) == asked                                    # cached for a few seconds
    assert cams.build({}, reader) is None                           # nothing to announce from a reader
    f.event("x", "driveway")
    reader.refresh()
    assert reader.take_events() == []


def test_build_house_adds_the_cameras_with_frigate_and_the_hub_owns_them(tmp_path):
    base = {"SKIPPY_HOME_STATE": str(tmp_path), "SKIPPY_NEST": "0", "FRIGATE_URL": "http://skippy-core:7355",
            "REOLINK_DOORBELL": "front_door=192.168.1.60", "REOLINK_PASSWORD": "pw"}
    part = homectl.build_house(dict(base))
    assert isinstance(part, cams.Cams) and not part.owner and part.bells == {"front_door": "192.168.1.60"}
    assert homectl.build_house(dict(base, SKIPPY_CAMS="0")) is None
    assert homectl.build_house({k: v for k, v in base.items() if k != "FRIGATE_URL"}) is None
    assert cams.doorbells("front_door=192.168.1.60, side_door = 10.0.0.9,junk") == {"front_door": "192.168.1.60", "side_door": "10.0.0.9"}


def test_the_dm_carries_the_snapshot_as_base64(monkeypatch):
    sent = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        sent.update(url=req.full_url, body=json.loads(req.data), auth=req.headers["Authorization"])
        return Resp()
    monkeypatch.setattr(cams.urllib.request, "urlopen", urlopen)
    cams.portal_alert("https://portal.example/", "tok")("Someone's at the front door.", b"\xff\xd8x")
    assert sent == {"url": "https://portal.example/home/alert", "auth": "Bearer tok",
                    "body": {"text": "Someone's at the front door.", "image": base64.b64encode(b"\xff\xd8x").decode()}}
