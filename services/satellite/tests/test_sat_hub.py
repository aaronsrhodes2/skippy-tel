"""The Wyoming hub against a simulated room box: a real TCP connection speaking the real protocol (the `wyoming` package's own reader and
writer), quiet and voice streamed as PCM, and what the hub sends back checked event by event. No hardware, no network beyond localhost."""
import asyncio
import struct

import pytest

from hub import Hub, Room, parse_rooms
from timers import Timers
from voicegate import BudgetSpent, GateError
from wyoming.event import Event, async_read_event, async_write_event

QUIET, VOICE = 0.004, 0.12


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


def test_a_long_answer_is_spoken_as_its_summary():
    async def body(box, made, hub, log, start):
        await start()
        await made["room"].say("word " * 100)
        await box.wait_for("audio-stop")
        assert made["gate"].tts_calls == ["SUMMARY"]
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
