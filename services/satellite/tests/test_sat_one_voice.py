"""One voice per room (the Captain, 2026-10-31: "letting the stereo be the single source, it's too hard to sync these wimpy hubs"): with a PC
output open, Skippy speaks from it alone, the pod stays silent but draws the lips on the output's clock; no output open, the pod speaks."""
import asyncio
import time

import numpy as np

import router as rt
import speaker_out
import sync
from hub import Room
from timers import Timers


class Link:
    hub_follows_up = True

    def __init__(self):
        self.spoken, self.mouths, self.lip_calls = [], [], []

    async def heard(self, t): pass
    async def released(self): pass
    async def woke(self, by): pass
    async def show(self, t): pass

    async def speak(self, said, voice, follow_up=False):
        self.spoken.append(("speak", said, voice))

    async def announce(self, said, voice):
        self.spoken.append(("announce", said, voice))

    async def mouth(self, voice, said="", mirror_now=True):
        self.mouths.append((said, mirror_now))

    def lips(self, at, seconds):
        self.lip_calls.append((at, seconds))


class Out:
    def __init__(self, open_=True, plays=True, sole=True):
        self.open_, self.plays, self.sole, self.kind, self.played = open_, plays, sole, "hdmi", []

    def available(self):
        return self.open_ or None

    async def play(self, mp3, gain=1.0, on_sound=None):
        self.played.append(mp3)
        if self.plays and on_sound is not None:
            on_sound(time.monotonic() + 0.2, 1.5)
        return self.plays


class Gate:
    def tts(self, text): return b"mp3"
    def summary(self, text): return text


def room(out):
    return Room("office", Link(), Gate(), object(), Timers(), log=lambda m: None, pc_outs=[out])


def test_with_the_stereo_open_only_the_stereo_speaks_and_the_pod_draws_the_lips():
    out = Out()
    r = room(out)
    asyncio.run(r._out("Hello, monkey.", ("mp3", b"CLIP")))
    assert out.played == [b"CLIP"]
    assert r.link.spoken == [("speak", "Hello, monkey.", None)]       # the pod gets the words, not the sound
    assert r.link.mouths == [("Hello, monkey.", False)] and len(r.link.lip_calls) == 1     # the other pods wait for lips()


def test_no_stereo_open_the_pod_speaks():
    out = Out(open_=False)
    r = room(out)
    asyncio.run(r._out("Hello.", ("mp3", b"CLIP"), announce=True))
    assert r.link.spoken == [("announce", "Hello.", ("mp3", b"CLIP"))]


def test_a_clip_the_stereo_dropped_is_said_by_the_pod():
    out = Out(plays=False)
    r = room(out)
    asyncio.run(r._out("Hello.", ("mp3", b"CLIP")))
    assert r.link.spoken[-1] == ("announce", "Hello.", ("mp3", b"CLIP"))


def test_one_voice_off_keeps_both_speaking():
    out = Out(sole=False)
    r = room(out)
    asyncio.run(r._out("Hello.", ("mp3", b"CLIP")))
    assert r.link.spoken == [("speak", "Hello.", ("mp3", b"CLIP"))]


def test_the_vault_setting_and_the_room_decide_one_voice():
    assert speaker_out.one_voice({}) and not speaker_out.one_voice({"SATELLITE_ONE_VOICE": "0"})


def test_the_lips_start_when_the_router_says_it_sounds():
    """The first block's push, the queue ahead of it, the stream's latency, and the measured rest."""
    r = type("R", (), {"calibrator": type("C", (), {"extra": {"hdmi": 0.12}})()})()
    assert abs(speaker_out.sound_at(r, {"hdmi": (100.0, 0.15, 0.1)}) - 100.37) < 1e-9
    assert abs(speaker_out.sound_at(type("R", (), {})(), {"hdmi": (100.0, 0.15, 0.1)}) - (100.25 + speaker_out.SOUND_EXTRA_S)) < 1e-9


def test_the_routed_output_says_when_it_sounds_and_whether_it_played():
    from test_sat_router import World, tone
    w = World(present=("HDMI",))
    router = rt.Router([("hdmi", "HDMI", 0.15)], find=w.find, stream_factory=w.factory, reinit=w.reinit, log=lambda *a: None, autostart=True)
    out = speaker_out.RoutedOut(router, ["hdmi"], log=lambda *a: None, duck_others=False, decoder=lambda mp3, gain: tone(0.2), sole=True)
    heard = []
    try:
        played = asyncio.run(out.play(b"x", on_sound=lambda at, s: heard.append((at, s))))
    finally:
        router.close()
    assert played and len(heard) == 1 and abs(heard[0][1] - 0.2) < 1e-6
    assert heard[0][0] > time.monotonic() - 5 and out.talking_until >= heard[0][0] + 0.2


def test_one_voice_measures_how_late_the_output_really_sounds(tmp_path):
    from test_sat_sync import FakeSinks, room_hearing, voice
    clip = voice(seed=3)
    push = 101.0
    ring = room_hearing(clip, [(push + 0.15 + 0.1 + 0.17, 1.0)])
    logs = []
    c = sync.Calibrator(FakeSinks(0.15), ["hdmi"], ring, log=logs.append, path=tmp_path / "a.json", clock=lambda: 1e9, solo=True)
    got = c.measure(clip, {"hdmi": (push, 0.15, 0.1)})
    assert abs(got["outputs"]["hdmi"]["latency"] - 0.17) < 0.003 and abs(c.extra["hdmi"] - 0.17) < 0.003
    assert "sounded 170 ms after its queue" in logs[-1]


def test_the_streamed_face_talks_on_the_stereos_clock_not_the_boxs():
    import esphome_link

    calls, actions, mirrored = [], [], []

    class Client:
        async def execute_service(self, svc, data):
            actions.append(data["state"])

    class Other:
        async def mirror(self, text, etext, seconds):
            mirrored.append((text, round(seconds, 2)))

    link = esphome_link.EsphomeLink.__new__(esphome_link.EsphomeLink)
    link.streamer = type("S", (), {"set_box_state": lambda self, *a: calls.append(a)})()
    link._box_face, link._lips_until, link._mirror_next, link._tasks = ("thinking", False, False, False), 0.0, ("nABC", "e"), set()
    link.client, link.services = Client(), [type("F", (), {"name": "face"})()]
    link.others = lambda: [Other()]

    async def go():
        link.lips(time.monotonic() + 0.05, 0.1)
        await asyncio.sleep(0.3)
    asyncio.run(go())
    assert calls[0] == ("speaking", True, False, False) and calls[-1] == ("thinking", False, False, False)     # the streamed face
    assert actions == ["speaking", "idle"]                                                                     # the box's own face
    assert mirrored == [("nABC", 0.1)]                                                                         # the other pods, at the same moment


def test_an_output_nobody_hears_is_taken_for_off_then_probed_and_heard_again(tmp_path):
    """2026-11-01: the Captain turns the projector off at night; the morning routine spoke into it, unheard. Four long replies in a row
    with no copy in the room: the output is silent and the pod speaks. Once per PROBE_S one reply goes to it alone; heard, it is back."""
    from test_sat_sync import FakeSinks, room_hearing, voice
    clip = voice(seconds=2.5, seed=3)
    push, now = 101.0, [1e9]
    logs = []
    quiet = room_hearing(clip, [])                                  # the projector is off: nothing of the clip in the room
    c = sync.Calibrator(FakeSinks(0.15), ["hdmi"], quiet, log=logs.append, path=tmp_path / "a.json", clock=lambda: now[0], solo=True)
    for _ in range(sync.SILENT_AFTER - 1):
        c.measure(clip, {"hdmi": (push, 0.15, 0.1)})
    assert not c.silent("hdmi")
    c.measure(clip, {"hdmi": (push, 0.15, 0.1)})
    assert c.silent("hdmi") and "taken for off" in logs[-1]
    assert not c.probe_due("hdmi")
    now[0] += sync.PROBE_S
    assert c.probe_due("hdmi") and not c.probe_due("hdmi")          # once per PROBE_S
    c.mic = room_hearing(clip, [(push + 0.15 + 0.1 + 0.17, 1.0)])   # switched back on
    c.measure(clip, {"hdmi": (push, 0.15, 0.1)})
    assert not c.silent("hdmi") and any("room's voice again" in line for line in logs)


def test_short_quips_unheard_never_take_the_output_for_off(tmp_path):
    from test_sat_sync import FakeSinks, room_hearing, voice
    clip = voice(seconds=1.0, seed=3)
    c = sync.Calibrator(FakeSinks(0.15), ["hdmi"], room_hearing(clip, []), log=lambda m: None, path=tmp_path / "a.json", clock=lambda: 1e9, solo=True)
    for _ in range(sync.SILENT_AFTER + 2):
        c.measure(clip, {"hdmi": (101.0, 0.15, 0.1)})
    assert not c.silent("hdmi")


class SilentCal:
    def __init__(self, due=False):
        self.due, self.silent_now = due, True

    def silent(self, k):
        return self.silent_now

    def probe_due(self, k):
        due, self.due = self.due, False
        return due


def test_with_the_output_taken_for_off_the_pod_speaks_and_nothing_goes_to_it():
    from test_sat_router import World, tone
    w = World(present=("HDMI",))
    router = rt.Router([("hdmi", "HDMI", 0.15)], find=w.find, stream_factory=w.factory, reinit=w.reinit, log=lambda *a: None, autostart=True)
    router.calibrator = SilentCal()
    out = speaker_out.RoutedOut(router, ["hdmi"], log=lambda *a: None, duck_others=False, decoder=lambda mp3, gain: tone(0.2), sole=True)
    r = room(out)
    try:
        assert out.available() is None
        asyncio.run(r._out("Good morning.", ("mp3", b"CLIP"), announce=True))
        assert r.link.spoken == [("announce", "Good morning.", ("mp3", b"CLIP"))]
        assert asyncio.run(out.play(b"x")) is False                   # (Room._also: nothing is pushed to an output nobody hears)
    finally:
        router.close()


def test_a_probe_nobody_heard_is_said_again_by_the_pod():
    out = Out()
    r = room(out)

    async def play(mp3, gain=1.0, on_sound=None):
        out.played.append(mp3)
        out.last_heard = False                                       # the probe went out and the room's microphone heard nothing
        return True
    out.play = play
    asyncio.run(r._out("Good morning.", ("mp3", b"CLIP"), announce=True))
    assert out.played == [b"CLIP"] and r.link.spoken[-1] == ("announce", "Good morning.", ("mp3", b"CLIP"))
