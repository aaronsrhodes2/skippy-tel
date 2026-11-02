"""The Ring cameras (ringcam.py) against a fake Ring cloud: the real connect loop on its own thread, a press and a motion pushed in, the
pods told (a press in every room, a motion in one, each once per window), the DM with its snapshot, home_state's words, the hub's mirror for
the other processes, read only, and build_house choosing the owner. Nothing here reaches Ring."""
import asyncio
import base64
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import homectl
import house
import ringcam

pytest.importorskip("ring_doorbell")


class FakeCam:
    def __init__(self, id, name, kind, battery, history=()):
        self.id, self.name, self.kind, self.battery_life = id, name, kind, battery
        self.connection_status = "online"
        self.history = list(history)
        self.snapshots = 0

    async def async_update_health_data(self):
        pass

    async def async_history(self, limit=30):
        return self.history[:limit]

    async def async_get_snapshot(self, retries=3, delay=1):
        self.snapshots += 1
        return b"\xff\xd8jpeg-of-" + self.name.encode() + b"\xff\xd9"


class FakeRing:
    def __init__(self, cams):
        self.cams = cams

    async def async_update_devices(self):
        pass

    def video_devices(self):
        return self.cams


class FakeListener:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


def event(kind, cam, update=False):
    return SimpleNamespace(kind=kind, doorbot_id=cam.id, device_name=cam.name, device_kind=cam.kind, is_update=update)


HOUR_AGO = datetime.fromtimestamp(time.time() - 3600, tz=timezone.utc)


@pytest.fixture
def cams():
    return [FakeCam(11, "Front Door", "doorbell_v5", 82, [{"kind": "motion", "created_at": HOUR_AGO}]),
            FakeCam(22, "Driveway", "stickup_cam_v4", 40)]


def owner(tmp_path, cams, **kw):
    listener = FakeListener()

    async def connect():
        return FakeRing(cams)

    async def listen(ring, on_event):
        return listener
    r = ringcam.Ring(ringcam.TokenFile(tmp_path), connect=connect, listen=listen, log=lambda m: None, **kw)
    for _ in range(100):
        if r.connected:
            break
        time.sleep(0.02)
    assert r.connected
    return r, listener


def test_each_camera_is_one_read_only_thing_with_battery_and_last_motion(tmp_path, cams):
    r, _ = owner(tmp_path, cams)
    states = {s["entity_id"]: s for s in r.states()}
    assert set(states) == {"camera.front_door", "camera.driveway"}
    door = states["camera.front_door"]
    assert door["state"] == "idle" and door["attributes"]["battery"] == 82 and door["attributes"]["doorbell"] is True
    assert abs(door["attributes"]["last_motion"] - HOUR_AGO.timestamp()) < 1          # from Ring's history: a hub restart still knows it
    said = r.state_of("front door")
    assert said == "Front Door is idle, battery 82 percent, last motion 1 hour ago, no doorbell press lately."
    assert r.devices() == "Nothing matches in the house."                              # never offered as something to switch
    with pytest.raises(house.HomeError):
        r._service("camera", "turn_off", {"entity_id": "camera.front_door"})
    assert r.control("front door", "off") == "Front Door can only be read, not changed."


def test_a_press_rings_and_a_repeat_of_the_same_event_is_not_news(tmp_path, cams):
    r, _ = owner(tmp_path, cams)
    r.on_event(event("ding", cams[0]))
    r.on_event(event("ding", cams[0], update=True))
    r.on_event(event("on_demand", cams[0]))                                             # a live view he opened himself: not a visitor
    states = {s["entity_id"]: s["state"] for s in r.states()}
    assert states["camera.front_door"] == "ringing"
    assert [e["kind"] for e in r.take_events()] == ["ding"] and r.take_events() == []
    assert "last doorbell press just now" in r.state_of("front door")


class Room:
    def __init__(self):
        self.said = []

    async def announce(self, text):
        self.said.append(text)


def run_watch(watch, hub, seconds=0.15):
    async def go():
        task = asyncio.create_task(watch.run(hub, lambda: "office", every=0.01))
        await asyncio.sleep(seconds)
        task.cancel()
    asyncio.run(go())


def test_the_pods_hear_a_press_everywhere_and_a_motion_once_and_his_phone_gets_the_snapshot(tmp_path, cams):
    r, _ = owner(tmp_path, cams)
    dms = []
    watch = ringcam.Watch(r, lambda text, pic: dms.append((text, pic)), log=lambda m: None)
    hub = SimpleNamespace(rooms={"office": Room(), "kitchen": Room()})
    r.on_event(event("ding", cams[0]))
    r.on_event(event("ding", cams[0]))                                                  # pressed twice: one visitor
    r.on_event(event("motion", cams[1]))
    r.on_event(event("motion", cams[1]))
    run_watch(watch, hub)
    assert hub.rooms["office"].said == ["Someone's at the Front Door.", "Motion at the Driveway."]
    assert hub.rooms["kitchen"].said == ["Someone's at the Front Door."]
    for _ in range(100):
        if len(dms) == 2:
            break
        time.sleep(0.02)
    assert sorted(dms) == [("Motion at the Driveway.", b"\xff\xd8jpeg-of-Driveway\xff\xd9"),
                           ("Someone's at the Front Door.", b"\xff\xd8jpeg-of-Front Door\xff\xd9")]


def test_motion_can_be_kept_quiet_but_a_press_never_is(tmp_path, cams):
    r, _ = owner(tmp_path, cams)
    watch = ringcam.Watch(r, lambda text, pic: None, motion=False, log=lambda m: None)
    r.on_event(event("motion", cams[1]))
    r.on_event(event("ding", cams[0]))
    assert [e["kind"] for e in watch.news()] == ["ding"]


def test_the_window_lets_the_next_visitor_through(tmp_path, cams):
    now = [1000.0]
    r, _ = owner(tmp_path, cams, clock=lambda: now[0])
    watch = ringcam.Watch(r, lambda text, pic: None, log=lambda m: None, clock=lambda: now[0])
    r.on_event(event("ding", cams[0]))
    assert len(watch.news()) == 1
    now[0] += 10
    r.on_event(event("ding", cams[0]))
    assert watch.news() == []
    now[0] += ringcam.ANNOUNCE_DING_S
    r.on_event(event("ding", cams[0]))
    assert len(watch.news()) == 1


def test_the_other_processes_read_the_hubs_mirror_and_never_sign_in(tmp_path, cams):
    r, _ = owner(tmp_path, cams)
    r.on_event(event("ding", cams[0]))
    mirror = ringcam.Ring(ringcam.TokenFile(tmp_path), owner=False, connect=lambda: pytest.fail("a mirror never signs in"))
    assert {s["entity_id"]: s["state"] for s in mirror.states()} == {"camera.front_door": "ringing", "camera.driveway": "idle"}
    assert "token" not in (tmp_path / "cams.json").read_text()
    assert ringcam.build({}, mirror) is None                                            # no events to tell from a mirror
    stale = ringcam.Ring(ringcam.TokenFile(tmp_path), owner=False, clock=lambda: time.time() + 10 * ringcam.REFRESH_S)
    assert all(s["state"] == "unavailable" for s in stale.states())


def test_not_signed_in_says_how(tmp_path):
    r = ringcam.Ring(ringcam.TokenFile(tmp_path), log=lambda m: None)                   # the real connect: no token.json, no seed
    r.ready.wait(5)
    with pytest.raises(house.HomeError, match="ringcam.py signin"):
        r.states()


def test_a_refreshed_token_is_kept_whole(tmp_path):
    tokens = ringcam.TokenFile(tmp_path, seed="from-the-vault")
    assert tokens.load() == {"refresh_token": "from-the-vault"}
    tokens.save({"refresh_token": "rotated", "access_token": "a"})
    assert tokens.load()["refresh_token"] == "rotated" and not list(tmp_path.glob("*.tmp"))


def test_build_house_signs_in_only_in_the_hub(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(ringcam, "Ring", lambda tokens, sensitive=False, owner=True, **kw: made.append(owner) or house.House())
    (tmp_path / "token.json").write_text("{}")
    base = {"RING_STATE": str(tmp_path), "SKIPPY_HOME_STATE": str(tmp_path / "home"), "SKIPPY_NEST": "0"}
    homectl.build_house(dict(base))
    homectl.build_house(dict(base, SKIPPY_RING_OWNER="1"))
    homectl.build_house(dict(base, SKIPPY_RING="0"))
    assert made == [False, True]


def test_the_dm_carries_the_snapshot_as_base64():
    posts = []
    alert = ringcam.portal_alert("https://portal.example", "tok", post=lambda text, image: posts.append((text, image)) or True)
    alert("Someone's at the Front Door.", b"\xff\xd8x")
    assert posts == [("Someone's at the Front Door.", b"\xff\xd8x")]
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
    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = urlopen
    try:
        ringcam.portal_alert("https://portal.example/", "tok")("hi", b"\xff\xd8x")
    finally:
        urllib.request.urlopen = orig
    assert sent == {"url": "https://portal.example/home/alert", "auth": "Bearer tok",
                    "body": {"text": "hi", "image": base64.b64encode(b"\xff\xd8x").decode()}}
