"""One voice per room (the Captain, 2026-10-31: "letting the stereo be the single source, it's too hard to sync these wimpy hubs"): with a PC
output open, Skippy speaks from it alone, the pod stays silent but draws the lips on the output's clock; no output open, the pod speaks."""
import asyncio
import time

import numpy as np

import router as rt
import speaker_out
import sync
from hub import BUSY, FOLLOWUP, IDLE, Room
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


def test_a_rooms_stereo_kinds_are_its_one_voice_while_it_is_on():
    """The box's own speaker is the last resort (the Captain, 2026-11-04): the alarm clock and the announce tool ask the room which stereo stands in for it."""
    out = Out()
    out.kinds = {"enclave"}
    r = room(out)
    assert r.stereo_kinds() == ["enclave"]
    out.open_ = False
    assert r.stereo_kinds() == []                                      # the stereo is off: only the box can speak
    out.open_, out.sole = True, False
    assert r.stereo_kinds() == []                                      # one voice is off: the box speaks as well, so the stereo does not stand in for it


def test_a_look_at_the_stereo_spends_no_probe_and_leaves_out_what_is_taken_for_off():
    """RoutedOut.available() restarts a silent output's probe clock and flags the next clip as its probe, but the alarm clock and the announce tool play on
    a RoutedOut of their own, which drops the outputs taken for off: they ask playable() instead (found by the review of this change, 2026-11-04)."""
    class Cal:
        def silent(self, kind):
            return kind == "hdmi"

        def probe_due(self, kind):
            raise AssertionError("a look must not touch the probe clock")

    class FakeRouter:
        calibrator = Cal()

        def available(self, kind):
            return True

    both = speaker_out.RoutedOut(FakeRouter(), ["hdmi", "enclave"], log=lambda m: None, sole=True)
    r = Room("living", Link(), Gate(), object(), Timers(), log=lambda m: None, pc_outs=[both])
    assert both.playable() == ["enclave"] and r.stereo_kinds() == ["enclave"] and both.probe is False
    only = speaker_out.RoutedOut(FakeRouter(), ["hdmi"], log=lambda m: None, sole=True)
    r = Room("theater", Link(), Gate(), object(), Timers(), log=lambda m: None, pc_outs=[only])
    assert only.playable() == [] and r.stereo_kinds() == [] and only.probe is False       # taken for off: the box speaks, and no probe is spent


def test_the_real_clock_and_the_real_room_ring_the_stereo_alone_then_the_box_joins(tmp_path, monkeypatch):
    """The contract between AlarmClock.ring and hub.Room (stereo_kinds, ring(quiet=, wait=)) with neither side faked: the Enclave rings alone for two
    rings and the box listens for "stop" once it is done; at the third ring the box rings too."""
    import alarm_clock
    from test_sat_alarm_clock import UPSTAIRS, at, make, run
    monkeypatch.setattr(alarm_clock, "LISTEN_AFTER_S", 0)
    out = Out()
    out.kinds = {"enclave"}
    r = Room("living", Link(), Gate(), object(), Timers(), log=lambda m: None, pc_outs=[out])
    now = [at(2026, 11, 2, 6, 0)]
    c, _, played = make(tmp_path, now, rooms={"living": r})
    c.layout = UPSTAIRS
    c.alarms.add("alarm", "06:30", where="upstairs", room="living")
    now[0] = at(2026, 11, 2, 6, 30)
    for ring in (1, 2):
        run(c.tick())
        assert r.link.spoken == [] and r.mode == FOLLOWUP and played[-1][0] == ("enclave",), ring     # the stereo alone; the clock opened the window
        r.mode = IDLE
        now[0] += 60
    run(c.tick())
    assert [s[0] for s in r.link.spoken] == ["announce", "announce"] and played[-1][0] == ("enclave",)   # ring 3: the chime and the words on the box too
    out.open_ = False
    r.link.spoken.clear()
    now[0] += 60
    run(c.tick())
    assert [s[0] for s in r.link.spoken] == ["announce", "announce"]


def test_a_stereo_that_is_off_at_the_first_ring_means_the_box_rings_at_once(tmp_path, monkeypatch):
    import alarm_clock
    from test_sat_alarm_clock import UPSTAIRS, at, make, run
    monkeypatch.setattr(alarm_clock, "LISTEN_AFTER_S", 0)
    out = Out(open_=False)
    out.kinds = {"enclave"}
    r = Room("living", Link(), Gate(), object(), Timers(), log=lambda m: None, pc_outs=[out])
    now = [at(2026, 11, 2, 6, 0)]
    c, _, played = make(tmp_path, now, rooms={"living": r})
    c.layout = UPSTAIRS
    c.alarms.add("alarm", "06:30", where="upstairs", room="living")
    now[0] = at(2026, 11, 2, 6, 30)
    run(c.tick())
    assert [s[0] for s in r.link.spoken] == ["announce", "announce"]       # nothing is on in the room: the box is the only thing that can wake him


def silent_ring(r, wait):
    import alarm_clock
    return r.ring(alarm_clock.chime(0.5, repeats=1), b"VOICE", "Your alarm.", quiet=True, wait=wait)


def test_a_silent_ring_is_deaf_until_the_stereo_has_finished_and_then_listens_as_after_any_ring(monkeypatch):
    """The Enclave beside the box says "Say stop" aloud: a box that listened then would stop the alarm by itself, in a quiet room or in the middle of
    a conversation window alike (found by the review of this change, 2026-11-04)."""
    import alarm_clock
    monkeypatch.setattr(alarm_clock, "LISTEN_AFTER_S", 0.05)
    for before in (IDLE, FOLLOWUP):
        out = Out()
        out.kinds = {"enclave"}
        r = room(out)
        r.mode = before

        async def go():
            stereo = asyncio.get_running_loop().create_future()
            ring = asyncio.ensure_future(silent_ring(r, stereo))
            await asyncio.sleep(0.03)
            ringing = r.mode                                           # the stereo is still ringing
            stereo.set_result(True)
            await asyncio.sleep(0.02)
            echo = r.mode                                              # it has finished; its echo has not died away
            await ring
            return ringing, echo
        ringing, echo = asyncio.run(go())
        assert (ringing, echo) == (BUSY, BUSY), before
        assert r.link.spoken == [] and r.mode == FOLLOWUP              # no sound from the box; then the "stop" window


def test_a_stereo_ring_that_failed_or_never_finished_means_the_box_rings_after_all(monkeypatch):
    import alarm_clock
    monkeypatch.setattr(alarm_clock, "LISTEN_AFTER_S", 0)
    monkeypatch.setattr(alarm_clock, "RING_WAIT_S", 0.05)
    for outcome in ("nothing sounded", "raised", "never finished"):
        out = Out()
        out.kinds = {"enclave"}
        r = room(out)

        async def go():
            stereo = asyncio.get_running_loop().create_future()
            if outcome == "nothing sounded":
                stereo.set_result(False)
            elif outcome == "raised":
                stereo.set_exception(RuntimeError("mixer stalled"))
            await silent_ring(r, stereo)
        asyncio.run(go())
        assert [s[0] for s in r.link.spoken] == ["announce", "announce"], outcome      # the chime and the words, on the box
        assert r.mode == FOLLOWUP


def test_a_box_that_joins_a_stereo_ring_rings_after_it_never_with_it(monkeypatch):
    """The same words from the Enclave and from the box a moment apart are two Skippys at once (the Captain, 2026-11-05): from the third ring the
    box rings once the stereo has finished and its echo has died away, not while it plays."""
    import alarm_clock
    monkeypatch.setattr(alarm_clock, "LISTEN_AFTER_S", 0)
    out = Out()
    out.kinds = {"enclave"}
    r = room(out)

    async def go():
        stereo = asyncio.get_running_loop().create_future()
        ring = asyncio.ensure_future(r.ring(alarm_clock.chime(0.5, repeats=1), b"VOICE", "Your alarm.", quiet=False, wait=stereo))
        await asyncio.sleep(0.03)
        while_it_plays = list(r.link.spoken)
        stereo.set_result(True)
        await ring
        return while_it_plays
    assert asyncio.run(go()) == []                                       # the stereo was still ringing: the box said nothing
    assert [s[0] for s in r.link.spoken] == ["announce", "announce"] and r.mode == FOLLOWUP     # and then the chime and the words, and the "stop" window


def test_an_answer_stops_a_ring_between_its_parts_so_the_box_never_rings_a_stopped_alarm(tmp_path, monkeypatch):
    """The box rings after the Enclave has finished, so "Skippy" or the phone's Stop can come in between: the box must not then ring an alarm that is off."""
    import alarm_clock
    from test_sat_alarm_clock import UPSTAIRS, at, make, run
    monkeypatch.setattr(alarm_clock, "LISTEN_AFTER_S", 0)
    out = Out()
    out.kinds = {"enclave"}
    r = Room("living", Link(), Gate(), object(), Timers(), log=lambda m: None, pc_outs=[out])
    now = [at(2026, 11, 2, 6, 0)]
    c, _, played = make(tmp_path, now, rooms={"living": r})
    c.layout = UPSTAIRS
    a = c.alarms.add("alarm", "06:30", where="upstairs", room="living")

    async def slow(kinds, clips, gain):
        await asyncio.sleep(0.2)                                       # the Enclave is still saying it
        return True
    c._play_outs = slow

    async def go(stop):
        c.ringing = {"alarm": a, "key": "k", "since": now[0], "rings": 3, "next": now[0] + 60, "alerts": 0}
        ring = asyncio.ensure_future(c._ring_once(a, 3))               # the third ring: the box follows the Enclave
        await asyncio.sleep(0.05)
        if stop:
            c.stop("phone")
        await ring                                                     # (an answer is no error)
    run(go(stop=True))
    assert r.link.spoken == [] and not r.voice.locked() and c.ringing is None      # it never rang, and it let go of the floor
    run(go(stop=False))
    assert [s[0] for s in r.link.spoken] == ["announce", "announce"]   # unanswered, the box does follow the Enclave


def test_a_ring_that_holds_the_floor_does_not_wait_for_the_rooms_voice_lock():
    """The alarm clock takes the room's voice lock for the whole ring (it covers the stereo's part too): the room's own ring must not ask for it again."""
    import alarm_clock
    out = Out()
    out.kinds = {"enclave"}
    r = room(out)

    async def go():
        await r.voice.acquire()
        try:
            await asyncio.wait_for(r.ring(alarm_clock.chime(0.5, repeats=1), b"VOICE", "Your alarm.", floor=True), 1)
        finally:
            r.voice.release()
    asyncio.run(go())
    assert [s[0] for s in r.link.spoken] == ["announce", "announce"]


def test_a_loud_ring_is_as_before_the_chime_the_words_and_then_listening():
    import alarm_clock
    out = Out()
    out.kinds = {"enclave"}
    r = room(out)
    asyncio.run(r.ring(alarm_clock.chime(0.5, repeats=1), b"VOICE", "Your alarm."))
    assert [s[0] for s in r.link.spoken] == ["announce", "announce"] and r.mode == FOLLOWUP


def test_a_rooms_stereo_is_what_the_vault_gives_it_not_where_its_voice_was_last_sent():
    """"Talk to me downstairs" rewrites the RoutedOut's kinds until the next restart (hub.music_tools): another floor's stereo is no stand-in for this box."""
    out = Out()
    out.kinds = {"enclave", "hdmi"}                                    # "talk to me everywhere"
    r = room(out)
    r.env = {"SATELLITE_OUT": "office=pod+enclave"}
    assert r.stereo_kinds() == ["enclave"]
    out.kinds = {"hdmi"}                                               # "talk to me downstairs"
    assert r.stereo_kinds() == []


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


def test_a_clip_whose_play_is_cancelled_is_taken_off_the_router_so_it_is_not_said_over(tmp_path):
    """An answer cancels the alarm's ring (AlarmClock._end), but the clip the Enclave was already saying stayed on the router and played out: 'Alarm off.' was
    said over its tail, two Skippys at once (review of 2026-11-05)."""
    from test_sat_router import World, tone
    w = World(present=("HDMI",))
    router = rt.Router([("hdmi", "HDMI", 0.0)], find=w.find, stream_factory=w.factory, reinit=w.reinit, log=lambda *a: None, autostart=True)
    out = speaker_out.RoutedOut(router, ["hdmi"], log=lambda *a: None, duck_others=False, decoder=lambda mp3, gain: tone(3.0), sole=True)

    async def go():
        playing = asyncio.ensure_future(out.play(b"x"))
        await asyncio.sleep(0.4)                                          # the clip is on the router and sounding
        on_it = len(router.plays)
        playing.cancel()
        try:
            await playing
        except asyncio.CancelledError:
            pass
        return on_it, len(router.plays)
    try:
        on_it, after = asyncio.run(go())
    finally:
        router.close()
    assert on_it == 1 and after == 0                                      # cancelled: gone from the router at once, not 2.6 s later


def test_a_pod_with_voice_off_never_makes_the_sound_the_words_and_lips_still_go():
    r = Room("office", Link(), Gate(), object(), Timers(), log=lambda m: None, pc_outs=[])
    r.pod_voice = False
    asyncio.run(r._out("Hello.", ("mp3", b"CLIP"), announce=False))
    assert r.link.spoken == [("speak", "Hello.", None)]               # no clip to the box: only the words


def test_a_pod_with_voice_off_is_not_the_fallback_when_the_stereo_drops_it():
    out = Out(plays=False)
    r = room(out)
    r.pod_voice = False
    asyncio.run(r._out("Hello.", ("mp3", b"CLIP"), announce=True))
    assert all(v is None for _, _, v in r.link.spoken)               # the dropped clip is not said by the pod


def test_a_timer_may_still_sound_from_the_pod():
    r = Room("office", Link(), Gate(), object(), Timers(), log=lambda m: None, pc_outs=[])
    r.pod_voice = False
    r._alarm_sound = True
    asyncio.run(r._out("Your timer is done.", ("mp3", b"CLIP"), announce=True))
    assert r.link.spoken == [("announce", "Your timer is done.", ("mp3", b"CLIP"))]
