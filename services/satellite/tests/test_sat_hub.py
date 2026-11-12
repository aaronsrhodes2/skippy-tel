"""The Wyoming hub against a simulated room box: a real TCP connection speaking the real protocol (the `wyoming` package's own reader and
writer), quiet and voice streamed as PCM, and what the hub sends back checked event by event. No hardware, no network beyond localhost."""
import asyncio
from pathlib import Path
import struct

import pytest

from hub import Hub, Room, parse_rooms
from timers import Timers
from voicegate import BudgetSpent, GateError
from wyoming.event import Event, async_read_event, async_write_event

QUIET, VOICE, SOFT = 0.004, 0.12, 0.03


class FakeGate:
    def __init__(self):
        self.said, self.stt_calls, self.tts_calls = [], 0, []
        self.tts_error = None

    def stt(self, wav):
        self.stt_calls += 1
        return self.said.pop(0) if self.said else ""

    def tts(self, text):
        if self.tts_error:
            raise self.tts_error
        self.tts_calls.append(text)
        return b"mp3"

    def summary(self, text):
        return "SUMMARY"


class FakeBrain:
    def __init__(self):
        self.heard = []

    def reply(self, heard):
        self.heard.append(heard)
        return "Done, monkey."


class FakeSpotter:
    """Hears its name when the test says so (`arm()`), on the next block it is fed."""
    name = "skippy"

    def __init__(self):
        self.armed, self.fed, self.resets = False, 0, 0

    def arm(self):
        self.armed = True

    def feed(self, block):
        self.fed += 1
        if self.armed:
            self.armed = False
            return True
        return False

    def reset(self):
        self.resets += 1


def pcm(level, ms, rate=16000, channels=1):
    v = int(level * 32767)
    return struct.pack("<h", v) * (rate * ms // 1000) * channels


class Box:
    """A Wyoming satellite: it waits for the hub to connect, answers describe, and streams what the test tells it to."""

    def __init__(self):
        self.got, self.connections = [], 0
        self.changed = asyncio.Event()
        self.connected = asyncio.Event()

    async def start(self):
        self.server = await asyncio.start_server(self._conn, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _conn(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.connections += 1
        self.connected.set()
        while True:
            event = await async_read_event(reader)
            if event is None:
                return
            self.got.append(event)
            if event.type == "describe":
                await self.send("info", {"satellite": {"name": "kitchen box"}})
            self.changed.set()

    async def send(self, kind, data=None, payload=None):
        await async_write_event(Event(kind, data or {}, payload), self.writer)

    async def stream(self, level, ms, rate=16000, channels=1):
        """Audio in 1024-sample chunks (64 ms: not a multiple of the hub's 50 ms blocks, on purpose)."""
        data = pcm(level, ms, rate, channels)
        step = 1024 * 2 * channels
        for i in range(0, len(data), step):
            await self.send("audio-chunk", {"rate": rate, "width": 2, "channels": channels}, data[i:i + step])
        await asyncio.sleep(0.01)

    def kinds(self):
        return [e.type for e in self.got if e.type != "audio-chunk"]

    def texts(self, kind):
        return [e.data.get("text") for e in self.got if e.type == kind]

    async def wait_for(self, kind, n=1, timeout=5.0):
        async def until():
            while sum(e.type == kind for e in self.got) < n:
                self.changed.clear()
                await self.changed.wait()
        await asyncio.wait_for(until(), timeout)


def run(test, forever=False):
    """Start a box and a hub with one room; run the test body; tear down."""
    async def go():
        box = Box()
        await box.start()
        made = {}
        log = []

        def make_room(name, link):
            room = Room(name, link, made["gate"], made["brain"], made["timers"], spotter=made.get("spotter"),
                        local_voice=made.get("local_voice"), log=log.append)
            made["room"] = room
            return room

        made.update(gate=FakeGate(), brain=FakeBrain(), timers=Timers(clock=lambda: made.get("now", 0)))
        hub = Hub([("kitchen", "wyoming", "127.0.0.1", box.port)], make_room, log=log.append, retry_s=(0.01,),
                  decode=lambda mp3: (pcm(0.1, 100, 22050), 22050), sleep=lambda s: asyncio.sleep(0))
        task = asyncio.ensure_future(hub.keep("kitchen", "wyoming", "127.0.0.1", box.port, forever=forever))
        try:
            await test(box, made, hub, log, start=lambda: _handshake(box))
        finally:
            task.cancel()
            box.server.close()
    asyncio.run(go())


async def _handshake(box):
    await asyncio.wait_for(box.connected.wait(), 5)
    await box.wait_for("run-satellite")
    await box.send("audio-start", {"rate": 16000, "width": 2, "channels": 1})


def with_spotter(made):
    made["spotter"] = FakeSpotter()
    return made["spotter"]


# ── the connection ──
def test_the_hub_dials_the_box_asks_who_it_is_then_runs_it():
    async def body(box, made, hub, log, start):
        await start()
        assert box.kinds()[:2] == ["describe", "run-satellite"]
        await asyncio.sleep(0.05)
        assert any("kitchen box" in line for line in log)
    run(body)


def test_rooms_are_name_equals_host_wyoming_by_default_or_an_esphome_box():
    assert parse_rooms("kitchen=192.168.1.40, bedroom=pi-bed.local:10701") == [("kitchen", "wyoming", "192.168.1.40", 10700),
                                                                                 ("bedroom", "wyoming", "pi-bed.local", 10701)]
    assert parse_rooms("den=esphome://skippy-den.local, hall=esphome://10.0.0.9:6054") == [("den", "esphome", "skippy-den.local", 6053),
                                                                                          ("hall", "esphome", "10.0.0.9", 6054)]
    assert parse_rooms("") == []
    for bad in ("kitchen", "=1.2.3.4", "kitchen=", "kitchen=pi:port", "a=1.1.1.1,a=2.2.2.2", "my room=1.2.3.4", "x=esphome://"):
        with pytest.raises(ValueError):
            parse_rooms(bad)


def test_a_box_that_drops_is_dialled_again():
    async def body(box, made, hub, log, start):
        await start()
        box.writer.close()
        for _ in range(200):
            if box.connections >= 2:
                break
            await asyncio.sleep(0.01)
        assert box.connections >= 2
    run(body, forever=True)


# ── the hub hears the name (the box streams everything) ──
def test_with_the_spotter_skippy_and_the_command_in_one_breath_is_answered_in_the_room():
    async def body(box, made, hub, log, start):
        spot = with_spotter(made)
        await start()
        await box.send("run-pipeline", {"start_stage": "wake", "end_stage": "tts"})
        await box.stream(QUIET, 600)
        made["gate"].said.append("Skippy, turn the lights off.")
        await box.stream(VOICE, 400)
        spot.arm()
        await box.stream(VOICE, 1100)
        await box.stream(QUIET, 1500)
        await box.wait_for("audio-stop")
        assert made["brain"].heard == ["turn the lights off."]
        k = box.kinds()
        assert k.index("detection") < k.index("transcript") < k.index("synthesize") < k.index("audio-start") < k.index("audio-stop")
        assert box.texts("transcript") == ["turn the lights off."]
        assert any(e.type == "audio-chunk" and e.data["rate"] == 22050 for e in box.got)
    run(body)


def test_a_command_softer_than_the_normal_bar_is_heard_after_the_name_without_shouting():
    """The Captain, 2026-11-05: "Skippy hears his wake word well, but the following commands I have to shout to get it to record my voice"."""
    async def body(box, made, hub, log, start):
        spot = with_spotter(made)
        await start()
        await box.send("run-pipeline", {"start_stage": "wake", "end_stage": "tts"})
        await box.stream(QUIET, 600)
        await box.stream(SOFT, 300)                                     # the name, spoken softly: under the normal bar (0.038), so the voice detector does not begin
        spot.arm()                                                      # the spotter has heard it
        made["gate"].said.append("turn the lights off")
        await box.stream(SOFT, 1300)                                    # and the command as softly: over the lowered bar
        await box.stream(QUIET, 1500)
        await box.wait_for("audio-stop")
        assert made["brain"].heard == ["turn the lights off"]
        assert any("(mic: a voice at" in line for line in log)          # what the room heard and the bar it had to clear, for tuning from the log
    run(body)


def test_a_name_that_nothing_followed_closes_the_window_instead_of_hanging():
    """A buffer seeded when the name was heard kept the window open for ever when the voice detector never heard him begin."""
    async def body(box, made, hub, log, start):
        spot = with_spotter(made)
        await start()
        await box.send("run-pipeline", {"start_stage": "wake", "end_stage": "tts"})
        await box.stream(QUIET, 600)
        await box.stream(SOFT, 300)                                     # the name, softly: the voice detector never begins
        spot.arm()
        await box.stream(QUIET, 9000)                                   # longer than the listening window (8 s)
        await asyncio.sleep(0.1)
        assert made["room"].mode == "idle" and made["room"].buf is None
        assert made["room"].vad._armed is None                          # back at rest the detector is disarmed like after every other way back (it used to stay armed)
        assert any("(mic: nothing said" in line for line in log)
        assert made["gate"].stt_calls == 0
    run(body)


def test_a_buffer_that_only_grows_is_cut_whatever_the_room_does(monkeypatch):
    """A steady noise that holds the detector open used to grow the buffer for ever (25 KB a block)."""
    import hub as hub_mod
    monkeypatch.setattr(hub_mod, "MAX_UTTERANCE_BLOCKS", 100)
    async def body(box, made, hub, log, start):
        with_spotter(made)
        await start()
        await box.send("run-pipeline", {"start_stage": "wake", "end_stage": "tts"})
        await box.stream(QUIET, 600)
        await box.stream(VOICE, 12000)                                  # 12 s of a steady loud noise: no pause ever ends it
        await asyncio.sleep(0.1)
        assert any("without a pause: cut there" in line for line in log)
        assert made["room"].buf is None or len(made["room"].buf) <= 100
    run(body)


def test_what_whisper_makes_of_noise_is_not_a_command():
    async def body(box, made, hub, log, start):
        spot = with_spotter(made)
        await start()
        await box.send("run-pipeline", {"start_stage": "wake", "end_stage": "tts"})
        await box.stream(QUIET, 600)
        spot.arm()
        await box.stream(VOICE, 300)
        made["gate"].said.append("Thanks for watching!")
        await box.stream(VOICE, 1200)
        await box.stream(QUIET, 1500)
        await asyncio.sleep(0.3)
        assert made["gate"].stt_calls >= 1 and made["brain"].heard == []
        assert any("only what Whisper makes of noise" in line for line in log)
    run(body)


def test_with_the_spotter_room_talk_never_reaches_whisper():
    async def body(box, made, hub, log, start):
        with_spotter(made)
        await start()
        await box.send("run-pipeline", {"start_stage": "wake", "end_stage": "tts"})
        await box.stream(QUIET, 600)
        await box.stream(VOICE, 1500)
        await box.stream(QUIET, 1500)
        await asyncio.sleep(0.05)
        assert made["gate"].stt_calls == 0
        assert "transcript" not in box.kinds() and "detection" not in box.kinds()
    run(body)


def test_without_a_model_whisper_is_the_wake_word_and_room_talk_is_dropped():
    async def body(box, made, hub, log, start):
        await start()
        await box.send("run-pipeline", {"start_stage": "wake", "end_stage": "tts"})
        await box.stream(QUIET, 600)
        made["gate"].said.append("We should order pizza.")
        await box.stream(VOICE, 1500)
        await box.stream(QUIET, 1500)
        await asyncio.sleep(0.05)
        assert made["gate"].stt_calls == 1 and made["brain"].heard == [] and "transcript" not in box.kinds()
        made["gate"].said.append("Hey Skippy, what time is it?")
        await box.stream(VOICE, 1500)
        await box.stream(QUIET, 1500)
        await box.wait_for("audio-stop")
        assert made["brain"].heard == ["what time is it?"]
        assert "detection" in box.kinds()
    run(body)


def test_after_an_answer_he_can_reply_without_the_name_but_only_for_a_few_seconds():
    async def body(box, made, hub, log, start):
        spot = with_spotter(made)
        await start()
        await box.send("run-pipeline", {"start_stage": "wake", "end_stage": "tts"})
        await box.stream(QUIET, 600)
        made["gate"].said += ["Skippy, set a timer.", "For ten minutes."]
        spot.arm()
        await box.stream(VOICE, 1500)
        await box.stream(QUIET, 1500)
        await box.wait_for("audio-stop")
        await box.stream(QUIET, 500)
        await box.stream(VOICE, 1500)                            # no name: still in the follow-up window
        await box.stream(QUIET, 1500)
        await box.wait_for("audio-stop", 2)
        assert made["brain"].heard == ["set a timer.", "For ten minutes."]
        await box.stream(QUIET, 7000)                            # the window has closed
        await box.stream(VOICE, 1500)
        await box.stream(QUIET, 1500)
        await asyncio.sleep(0.05)
        assert made["gate"].stt_calls == 2
    run(body)


def test_goodbye_ends_it_without_bothering_the_brain():
    async def body(box, made, hub, log, start):
        spot = with_spotter(made)
        await start()
        await box.send("run-pipeline", {"start_stage": "wake", "end_stage": "tts"})
        await box.stream(QUIET, 600)
        made["gate"].said.append("Skippy, goodbye.")
        spot.arm()
        await box.stream(VOICE, 1500)
        await box.stream(QUIET, 1500)
        await box.wait_for("transcript")
        await asyncio.sleep(0.05)
        assert made["brain"].heard == [] and "audio-start" not in box.kinds()
    run(body)


def test_a_box_at_another_rate_is_converted_to_what_the_hub_hears():
    async def body(box, made, hub, log, start):
        spot = with_spotter(made)
        await asyncio.wait_for(box.connected.wait(), 5)
        await box.wait_for("run-satellite")
        await box.send("audio-start", {"rate": 48000, "width": 2, "channels": 2})
        await box.send("run-pipeline", {"start_stage": "wake", "end_stage": "tts"})
        await box.stream(QUIET, 600, rate=48000, channels=2)
        made["gate"].said.append("Skippy, hello.")
        spot.arm()
        await box.stream(VOICE, 1500, rate=48000, channels=2)
        await box.stream(QUIET, 1500, rate=48000, channels=2)
        await box.wait_for("audio-stop")
        assert made["brain"].heard == ["hello."]
        assert 1400 <= made["room"].t <= 3800                    # the hub's clock counted 16 kHz audio, not 48 kHz stereo bytes
    run(body)


# ── the box hears the name itself ──
def test_a_box_that_woke_by_itself_sends_the_command_and_is_sent_back_to_its_wake_word():
    async def body(box, made, hub, log, start):
        await start()
        await box.stream(QUIET, 600)
        await box.send("run-pipeline", {"start_stage": "asr", "end_stage": "tts"})
        made["gate"].said.append("What is the weather like?")
        await box.stream(VOICE, 1500)
        await box.stream(QUIET, 1500)
        await box.wait_for("audio-stop")
        assert made["brain"].heard == ["What is the weather like?"]
        k = box.kinds()
        assert k.index("voice-started") < k.index("voice-stopped") < k.index("transcript") < k.index("audio-start")
        assert made["room"].mode == "idle"
    run(body)


def test_a_box_that_woke_and_heard_nothing_is_released_by_an_empty_transcript():
    async def body(box, made, hub, log, start):
        await start()
        await box.stream(QUIET, 600)
        await box.send("run-pipeline", {"start_stage": "asr", "end_stage": "tts"})
        await box.stream(QUIET, 9000)
        await box.wait_for("transcript")
        assert box.texts("transcript") == [""] and made["gate"].stt_calls == 0
    run(body)


def test_a_box_that_wakes_on_skippys_own_voice_is_sent_straight_back():
    async def body(box, made, hub, log, start):
        await start()
        made["room"].mode = "busy"
        await box.send("run-pipeline", {"start_stage": "asr", "end_stage": "tts"})
        await box.wait_for("transcript")
        assert box.texts("transcript") == [""] and made["room"].mode == "busy"
    run(body)


# ── the voice ──
def test_when_skippys_voice_is_out_of_budget_the_machine_voice_speaks_and_with_none_the_words_still_go():
    async def body(box, made, hub, log, start):
        await start()
        room = made["room"]
        made["gate"].tts_error = BudgetSpent("spent")
        room.local_voice = lambda text: (pcm(0.1, 50, 16000), 16000)
        await room.say("Hello.")
        await box.wait_for("audio-stop")
        assert any(e.type == "audio-start" and e.data["rate"] == 16000 for e in box.got)
        made["gate"].tts_error = GateError("down")
        room.local_voice = None
        await room.say("Still here.")
        await box.wait_for("synthesize", 2)
        assert box.texts("synthesize") == ["Hello.", "Still here."] and sum(e.type == "audio-start" for e in box.got) == 1
    run(body)


def test_a_long_technical_answer_is_spoken_as_its_summary():
    async def body(box, made, hub, log, start):
        await start()
        await made["room"].say("I changed services/satellite/hub.py and ran pytest. " * 8)       # file names and commands: nobody wants those intoned
        await box.wait_for("audio-stop")
        assert made["gate"].tts_calls == ["SUMMARY"]
    run(body)


def test_a_long_plain_answer_is_spoken_whole_in_pieces():
    async def body(box, made, hub, log, start):
        await start()
        await made["room"].say("The monkeys are at it again and I am magnificent. " * 20)        # about a thousand characters of him: jokes and all
        await box.wait_for("audio-stop")
        assert len(made["gate"].tts_calls) >= 2 and "SUMMARY" not in made["gate"].tts_calls and all(len(t) <= 480 for t in made["gate"].tts_calls)
    run(body)


def test_a_timer_is_announced_in_the_room_that_set_it():
    async def body(box, made, hub, log, start):
        await start()
        made["timers"].add(60, "pasta")
        made["now"] = 61
        await made["room"].timers_due()
        await box.wait_for("audio-stop")
        assert box.texts("synthesize") == ["Your pasta timer is done."]
    run(body)


def test_say_reaches_a_connected_room_and_refuses_an_unknown_one():
    async def body(box, made, hub, log, start):
        await start()
        await hub.say("kitchen", "Dinner is ready, monkeys.")
        await box.wait_for("audio-stop")
        assert box.texts("synthesize") == ["Dinner is ready, monkeys."]
        with pytest.raises(KeyError):
            await hub.say("attic", "Hello?")
    run(body)


def test_the_box_answers_ping():
    async def body(box, made, hub, log, start):
        await start()
        await box.send("ping", {"text": "x"})
        await box.wait_for("pong")
    run(body)


def test_a_slow_answer_is_covered_by_a_prerecorded_quip_and_a_quick_command_is_not():
    import filler as F
    bank = F.FillerBank(Path("."), {"thinking": [{"file": "x.mp3", "text": "Hmm. One moment, monkey."}], "performance": [], "long": []})
    bank.pick = lambda kind, command="": ("Hmm. One moment, monkey.", b"mp3") if kind == "thinking" else None

    async def body(box, made, hub, log, start):
        await start()
        room = made["room"]
        room.filler, room.filler_after_s = bank, 0.2
        said = []

        async def announce(text, voice):
            said.append(text)
        room.link.announce = announce
        slow = asyncio.ensure_future(asyncio.sleep(0.3))
        await room._cover_the_wait(slow, "what is the capital of france")
        assert said == ["Hmm. One moment, monkey."]
        said.clear()
        await room._cover_the_wait(asyncio.ensure_future(asyncio.sleep(0.3)), "turn the lights off")
        assert said == []
        assert bank.kind_for("recite a haiku to me") == "performance" and bank.kind_for("why is the sky blue") == "thinking"
        assert bank.kind_for("how fast is light") == "science" and bank.kind_for("you are stupid") == "insult" and bank.kind_for("turn the lights off") == "home"
    run(body)


# ── the room's own clock and a stub link: the listening window, as the review of 2026-11-05 found it ──
class StubLink:
    """What Room asks of a box, recorded: no socket, so a test can feed audio by the block on the room's own clock."""
    hub_follows_up = True

    def __init__(self):
        self.room, self.events, self.spoken = None, [], []

    async def woke(self, by): self.events.append(("woke", by))
    async def voice_started(self): self.events.append(("voice_started",))
    async def voice_stopped(self): self.events.append(("voice_stopped",))
    async def heard(self, text): self.events.append(("heard", text))
    async def released(self): self.events.append(("released",))
    async def show(self, text): pass
    async def idle_face(self): pass
    def session(self, on): self.events.append(("session", on))

    async def speak(self, said, voice, follow_up=False):
        self.spoken.append(said)

    announce = speak


class StubGate(FakeGate):
    """FakeGate that remembers what Whisper was asked and can take its time."""

    def __init__(self):
        super().__init__()
        self.delay = 0.0

    def stt(self, wav):
        import time as _time
        if self.delay:
            _time.sleep(self.delay)
        return super().stt(wav)


def listening_room(spotter=True, **kw):
    log = []
    gate, brain, link = StubGate(), FakeBrain(), StubLink()
    spot = FakeSpotter() if spotter else None
    room = Room("test", link, gate, brain, Timers(clock=lambda: 0), spotter=spot, log=log.append, **kw)
    return room, gate, brain, link, spot, log


async def hear(room, level, ms):
    """`ms` of one steady level on the room's microphone, a block at a time, on the room's own clock. While the room is busy (Whisper's turn, an answer) the clock of the
    audio waits for it, so a slow test machine cannot move the timeline: a real Whisper costs a second or so of it."""
    for _ in range(ms // 50):
        waited = 0
        while room.mode == "busy":
            await asyncio.sleep(0.001)
            waited += 1
            assert waited < 5000, "the room stayed busy"
        room.feed_pcm(pcm(level, 50))
        await asyncio.sleep(0)


async def hear_name(room, spot, level=SOFT, ms=500):
    """The name: a stretch of voice, the spotter hearing it, as a real one fires a little after the word."""
    await hear(room, QUIET, 1000)
    await hear(room, level, ms)
    spot.arm()
    await hear(room, level, 50)


def test_noise_after_the_name_does_not_keep_reopening_the_listening_window():
    """voice-detector 0 (review of 2026-11-05): every utterance Whisper made nothing of opened another full 8 s window, so a room that flickers (a TV, music) at the
    lowered bar kept it open for ever, a Whisper call for every flicker, and whatever the TV said was taken for his command."""
    async def go():
        room, gate, brain, link, spot, log = listening_room()
        await hear_name(room, spot)
        assert room.mode == "listening"
        await hear(room, QUIET, 2000)
        await hear(room, VOICE, 600)                                    # noise at 2 s: the voice detector begins, Whisper makes nothing of it
        await hear(room, QUIET, 5400)                                   # (the window's own 8 s ends at about 8 s)
        await hear(room, VOICE, 600)                                    # more noise just after it, and again later
        await hear(room, QUIET, 5400)
        await hear(room, VOICE, 600)
        await hear(room, QUIET, 3000)
        await asyncio.sleep(0.05)
        assert room.mode == "idle" and room.buf is None and room.vad._armed is None
        assert gate.stt_calls == 1 and brain.heard == []               # only the noise inside the window cost a Whisper call; the later noise was room talk
        assert ("released",) in link.events
    asyncio.run(go())


def test_the_name_alone_still_opens_a_full_window_for_the_command():
    async def go():
        room, gate, brain, link, spot, log = listening_room()
        gate.said += ["Skippy.", "turn off the lights"]
        await hear(room, QUIET, 1000)
        await hear(room, VOICE, 700)                                    # "Skippy" ...
        spot.arm()
        await hear(room, VOICE, 50)
        await hear(room, QUIET, 1500)                                   # ... and a pause: Whisper heard the name and nothing after it
        await asyncio.sleep(0.05)
        assert room.mode == "listening" and brain.heard == []
        await hear(room, QUIET, 6500)                                   # 8 s after the name, but the window opened again when the pause was heard
        await hear(room, VOICE, 1000)
        await hear(room, QUIET, 1500)
        await asyncio.sleep(0.05)
        assert brain.heard == ["turn off the lights"]
    asyncio.run(go())


def test_noise_that_runs_past_the_end_of_the_window_closes_it_and_releases_the_box():
    async def go():
        room, gate, brain, link, spot, log = listening_room()
        await hear_name(room, spot)
        await hear(room, QUIET, 7400)
        await hear(room, VOICE, 1000)                                   # begins before the window's 8 s are over, so the window waits for it ...
        await hear(room, QUIET, 3000)                                   # ... and Whisper makes nothing of it, after the deadline
        await asyncio.sleep(0.05)
        assert gate.stt_calls == 1 and brain.heard == []
        assert room.mode == "idle" and ("released",) in link.events and any("only noise since the name" in line for line in log)
    asyncio.run(go())


def test_noise_in_the_follow_up_window_does_not_extend_it_either():
    async def go():
        room, gate, brain, link, spot, log = listening_room()
        gate.said += ["turn off the lights"]
        await hear_name(room, spot, level=VOICE, ms=300)
        await hear(room, VOICE, 1000)                                   # the command ...
        await hear(room, QUIET, 1500)
        await asyncio.sleep(0.05)
        assert brain.heard == ["turn off the lights"] and room.mode == "followup"
        await hear(room, QUIET, 2500)
        await hear(room, VOICE, 600)                                    # ... a cough in the follow-up window (6 s) ...
        await hear(room, QUIET, 4500)                                   # ... which ends where it was going to end, not 6 s after the cough
        await asyncio.sleep(0.05)
        assert gate.stt_calls == 2 and room.mode == "idle"
    asyncio.run(go())


def test_a_command_is_not_thrown_away_for_what_whisper_added_to_it_but_noise_alone_is():
    async def go():
        said = {}
        for text, heard in [("Skippy, turn off the lights. Thanks for watching!", ["turn off the lights."]),
                            ("turn off the lights, thank you for watching", ["turn off the lights"]),
                            ("Skippy, thank you for watching the kids", ["thank you for watching the kids"]),
                            ("Skippy, please subscribe me to the weather alerts", ["please subscribe me to the weather alerts"]),
                            ("Skippy, put the subtitles by default on the TV", ["put the subtitles by default on the TV"]),
                            ("Subtitles by the Amara.org community", []),
                            ("Thanks for watching!", [])]:
            room, gate, brain, link, spot, log = listening_room()
            gate.said.append(text)
            await hear_name(room, spot, level=VOICE, ms=300)
            await hear(room, VOICE, 1200)
            await hear(room, QUIET, 1500)
            await asyncio.sleep(0.05)
            said[text] = brain.heard
            assert brain.heard == heard, text
            if not heard:
                assert room.mode == "listening" and any("only what Whisper makes of noise" in line for line in log)       # the command is still to come
    asyncio.run(go())


def test_without_a_wake_word_model_a_name_followed_only_by_noise_leaves_the_room_listening():
    """voice-detector 3: with Whisper as the wake word the woken room was sent back to rest with the box never released and no window open."""
    async def go():
        room, gate, brain, link, spot, log = listening_room(spotter=False)
        gate.said.append("Hey Skippy, thanks for watching.")
        await hear(room, QUIET, 1000)
        await hear(room, VOICE, 1500)
        await hear(room, QUIET, 1500)
        await asyncio.sleep(0.05)
        assert ("woke", "skippy") in link.events and brain.heard == []
        assert room.mode == "listening" and ("released",) not in link.events
    asyncio.run(go())


# ── a ring that is not this room's ──
class RingLink(StubLink):
    """StubLink that says when each part of a ring reached the box."""

    def __init__(self):
        super().__init__()
        self.when = []

    async def speak(self, said, voice, follow_up=False):
        import time
        self.when.append(time.monotonic())
        self.spoken.append(said)

    announce = speak


class StereoOut:
    """A PC output that is a room's one voice and can sound (what Room.stereo_kinds asks for)."""
    sole, kinds, kind = True, {"enclave"}, "enclave"

    def available(self):
        return True


def test_a_pod_whose_own_room_has_no_stereo_rings_at_once_but_one_that_joins_its_rooms_stereo_waits_for_it(monkeypatch):
    """alarm-concurrency 1 (review of 2026-11-05): the alarm clock hands every pod the house's one stereo task, and the room waited for it whenever it had one: a reminder
    set in the office rang there only after the Enclave upstairs had finished (and 1.2 s more), the only speaker in the room he was in kept waiting for another room's ring."""
    import alarm_clock
    import time
    monkeypatch.setattr(alarm_clock, "LISTEN_AFTER_S", 0)

    async def ring(pc_outs):
        room = Room("office", RingLink(), StubGate(), FakeBrain(), Timers(), log=lambda m: None, pc_outs=pc_outs)

        async def stereo():
            await asyncio.sleep(0.6)                                    # the house's stereo is ringing, and takes a while
            return True
        task = asyncio.ensure_future(stereo())
        began = time.monotonic()
        await room.ring(alarm_clock.chime(0.5, repeats=1), b"VOICE", "Your reminder.", quiet=False, wait=task)
        return [round(at - began, 2) for at in room.link.when], room.link.spoken

    alone, spoken = asyncio.run(ring([]))
    assert spoken == ["", "Your reminder."] and alone[0] < 0.4           # no stereo in this room: the box rings now, the stereo's ring is somewhere else
    joined, spoken = asyncio.run(ring([StereoOut()]))
    assert spoken == ["", "Your reminder."] and joined[0] >= 0.55        # the stereo is this room's own: the box rings after it, never with it


# ── "stop" while the house plays ──
class RadioStub:
    """The house radio as the hub sees it: playing or not (a song, or a book: one stream), a count of his stops, a tool that pauses it."""

    def __init__(self, playing=True, kind="song"):
        self.playing, self.kind, self.pauses, self.calls = playing, kind, 0, []

    def state(self):
        return {"playing": self.playing, "kind": self.kind, "title": "Gasoline"}

    def tool(self, name, args, room=""):
        self.calls.append((name, dict(args)))
        if name == "music" and args.get("action") == "pause":
            self.pauses += 1
            self.playing = False
        return "Done: paused."

    async def stop(self):
        self.playing = False


class AudioBrain:
    """A brain that knows what plays (houseaudio.Audio, as in the hub), answers anything with a line, and can run the music tool."""

    def __init__(self, radio):
        import houseaudio
        self.heard, self.actions, self.radio = [], [], radio
        self.audio = houseaudio.Audio(radio)

    def reply(self, heard):
        self.heard.append(heard)
        return "Brain answer."

    def run_tool(self, name, args):
        self.actions.append({"tool": name, "args": args, "outcome": ""})
        return self.radio.tool(name, args)


def house_log(tmp_path, day=None):
    import json
    import time
    path = tmp_path / "log" / f"{day or time.strftime('%Y-%m-%d')}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_a_plain_stop_while_the_house_plays_stops_it_through_the_music_tool_and_signs_off(tmp_path):
    """stop-and-audio-state 3 (review of 2026-11-05): the sign-off took "stop" before the brain's stop_route could, and the house played on."""
    import housemem
    for kind in ("song", "book"):                                       # a book is an item of the same stream
        radio = RadioStub(kind=kind)
        link = StubLink()
        room = Room("test", link, StubGate(), AudioBrain(radio), Timers(), log=lambda m: None, hlog=housemem.HouseLog(tmp_path / kind))
        asyncio.run(room.handle("stop"))
        assert radio.calls == [("music", {"action": "pause"})] and radio.pauses == 1 and room.brain.heard == [], kind
        records = [r for r in house_log(tmp_path / kind) if r["kind"] == "command"]
        assert [(r["words"], r["outcome"], r["tools"]) for r in records] == [("stop", "sign_off", ["music"])], kind
        assert ("released",) in link.events and room.mode == "idle"


def test_a_plain_stop_with_nothing_playing_is_only_a_sign_off_and_other_stops_are_the_brains():
    radio = RadioStub(playing=False)
    room = Room("test", StubLink(), StubGate(), AudioBrain(radio), Timers(), log=lambda m: None)
    asyncio.run(room.handle("stop"))
    assert radio.calls == [] and room.brain.heard == []                  # nothing to stop: "stop" is the end of the talk, as always
    radio.playing = True
    asyncio.run(room.handle("enough"))
    assert radio.calls == [] and room.brain.heard == ["enough"]          # not a sign-off word: the brain hears it (and its stop_route pauses the house), the hub adds nothing
    asyncio.run(room.handle("thanks"))
    assert radio.calls == []                                             # a sign-off that is no stop leaves the music alone


def test_a_plain_stop_that_ends_a_routine_stops_the_house_audio_it_left_playing(tmp_path, monkeypatch):
    """The routine's own "stop" (daily.END_RE) ends it with "Fine. Routine over." and used to leave the music playing, uncounted."""
    import hub as hub_mod
    from test_sat_daily import make as routine_make, stamp
    monkeypatch.setattr(hub_mod, "one_stream", lambda mp3, **k: mp3)
    for word in ("stop", "enough"):
        now = [stamp("2026-11-02", "09:00")]
        d, room, link, brain, *_ = routine_make(tmp_path / word, now)
        radio = RadioStub()
        d.stereo = lambda radio=radio: radio
        room.brain = AudioBrain(radio)
        link.on_speak = lambda text, room=room, link=link, word=word: asyncio.ensure_future(room.handle(word)) if len(link.spoken) == 1 else None      # in the middle of the morning briefing

        async def go(d=d, room=room, radio=radio):
            await room.handle("Good morning")
            for _ in range(500):
                if not d.busy and radio.calls:
                    break
                await asyncio.sleep(0.01)
        asyncio.run(go())
        assert "Fine. Routine over." in link.spoken, word
        assert radio.calls == [("music", {"action": "pause"})] and radio.pauses == 1, word       # counted, so a routine that has not started its music leaves it off
        assert [(r["words"], r["outcome"], r["tools"]) for r in house_log(tmp_path / word / "home", "2026-11-02") if r["kind"] == "command"] == [(word, "routine_stop", ["music"])]


def test_a_plain_stop_the_stereo_routine_already_took_is_not_paused_twice(tmp_path, monkeypatch):
    import hub as hub_mod
    from test_sat_daily import make as routine_make, stamp
    monkeypatch.setattr(hub_mod, "one_stream", lambda mp3, **k: mp3)
    d, room, link, *_ = routine_make(tmp_path, [stamp("2026-11-02", "09:00")])
    radio = RadioStub()
    d.stereo = lambda: radio
    room.brain = AudioBrain(radio)
    asyncio.run(room.handle("stop"))                                     # no routine running: Daily.claim's own "Music off."
    assert "Music off." in link.spoken and radio.playing is False and radio.calls == []


# ── the timing line of a spoken turn ──
def test_the_timing_line_and_numbers_of_a_turn():
    """The Captain, 2026-11-05: "streamline it so it becomes snappy like Amazon echo" (heard -> said had a median of 8 s and a p90 of 24 s): where the time goes, per turn."""
    from hub import TurnTiming
    t = TurnTiming({"wait": 0.7, "hear": 0.9})
    t.thought(3.4, {"route": None, "model_s": [2.1, 1.3], "tools_s": 0.2, "total_s": 3.4})
    t.voiced(1.1)
    assert t.line() == "(timing: wait 0.7s, hear 0.9s, think 3.4s [route none; model 2.1+1.3s; tools 0.2s], voice 1.1s = 6.1s)"
    assert t.numbers() == {"wait": 0.7, "hear": 0.9, "think": 3.4, "voice": 1.1, "total": 6.1, "route": "none", "model": [2.1, 1.3], "tools": 0.2}
    typed = TurnTiming(None)                                             # a typed turn has no wait and no hear; a brain with no split of its own gives no bracket
    typed.thought(2.04)
    typed.voiced(0.96)
    assert typed.line() == "(timing: think 2.0s, voice 1.0s = 3.0s)" and typed.numbers() == {"think": 2.0, "voice": 1.0, "total": 3.0}
    routed = TurnTiming({"wait": 1.1, "hear": 0.4})
    routed.thought(0.5, {"route": "music", "model_s": [0.3], "tools_s": 0.05})
    assert routed.line() == "(timing: wait 1.1s, hear 0.4s, think 0.5s [route music; model 0.3s; tools 0.1s] = 2.0s)"      # (a turn that never reached the voice)


def test_a_brain_timing_that_makes_no_sense_never_raises():
    from hub import TurnTiming
    for junk in ("slow", 7, [1, 2], {"route": object(), "model_s": ["a", None, 2], "tools_s": "slow"}, {"model_s": 3}, {"tools_s": float("nan")}, {}):
        t = TurnTiming({"wait": "x", "hear": None})
        t.thought(1.0, junk)
        t.voiced(0.5)
        assert t.line().startswith("(timing: think 1.0s") and "total" in t.numbers()


def test_a_spoken_turn_logs_one_timing_line_and_puts_the_numbers_in_the_command_record(tmp_path):
    import housemem
    import re
    import time

    class SlowGate(StubGate):
        def tts(self, text):
            time.sleep(0.05)
            return b"mp3"

    class SplitBrain(FakeBrain):
        timing = {"route": None, "model_s": [0.04, 0.03], "tools_s": 0.02, "total_s": 0.1}

        def reply(self, heard):
            time.sleep(0.1)
            return super().reply(heard)

    async def go():
        log = []
        room = Room("test", StubLink(), SlowGate(), SplitBrain(), Timers(), log=log.append, hlog=housemem.HouseLog(tmp_path))
        room.gate.said.append("what time is it")
        await room._utterance("listening", [0.0] * 800, 1.1)            # the voice detector waited 1.1 s for the end of it
        await room.handle("and typed this", typed=True)                 # a typed turn in the same room
        return log
    log = asyncio.run(go())
    lines = [line for line in log if "(timing:" in line]
    assert len(lines) == 2
    spoken_line = "\n".join(lines[:1])
    assert re.search(r"\(timing: wait 1\.1s, hear \d+\.\ds, think \d+\.\ds \[route none; model 0\.0\+0\.0s; tools 0\.0s\], voice \d+\.\ds = \d+\.\ds\)", spoken_line), spoken_line
    assert "wait" not in lines[1] and "hear" not in lines[1] and "think" in lines[1]
    records = [r for r in house_log(tmp_path) if r["kind"] == "command"]
    assert [r["words"] for r in records] == ["what time is it", "and typed this"]
    first, second = records[0]["timing"], records[1]["timing"]
    assert first["wait"] == 1.1 and set(first) == {"wait", "hear", "think", "voice", "total", "route", "model", "tools"} and first["route"] == "none"
    assert all(isinstance(first[k], (int, float)) and round(first[k], 1) == first[k] for k in ("wait", "hear", "think", "voice", "total"))
    assert first["think"] >= 0.1 and first["total"] >= first["wait"] + first["think"] - 0.1          # the brain took 0.1 s and the line adds the parts up
    assert set(second) == {"think", "voice", "total", "route", "model", "tools"}


def test_a_failing_house_log_never_costs_him_his_answer(tmp_path):
    class BrokenLog:
        def event(self, *a, **k):
            raise ValueError("disk on fire")

        def actions(self, *a, **k):
            raise ValueError("disk on fire")

    async def go():
        log = []
        link = StubLink()
        room = Room("test", link, StubGate(), FakeBrain(), Timers(), log=log.append, hlog=BrokenLog())
        await room.handle("what time is it", typed=True)
        return log, link
    log, link = asyncio.run(go())
    assert link.spoken == ["Done, monkey."] and any("could not log the turn: ValueError" in line for line in log)
