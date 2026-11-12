"""Round 2 of the house hub's review (2026-11-05), hub.py. Three findings, each with the test that failed before its fix:

* the longer YouTube-outro lines Whisper writes for noise ("Thanks for watching, see you next time.") were taken for commands: round 1 narrowed the filter to a
  sentence that IS the phrase, and the old filter (which dropped any sentence holding the words) used to catch all of them;
* houseaudio.note_start() was called after every play and resume whatever the radio answered, so the chat gave "his own request" as the reason for music that the
  start had not started;
* a plain "stop" (the sign-off, a routine's end) waited on the radio's pause with no bound: a radio that took the poll but not the call held the room deaf for up to 40 s.

Self-contained (its own stubs), so a change to another test file's helpers cannot break it."""
import asyncio
import json
import struct
import threading
import time
from types import SimpleNamespace

import pytest

import hub as hub_mod
from hub import Room, music_tools, without_artifacts
from timers import Timers

QUIET, VOICE, SOFT = 0.004, 0.12, 0.03


# ── a room on its own clock, as test_sat_hub drives one ──
def pcm(level, ms, rate=16000):
    return struct.pack("<h", int(level * 32767)) * (rate * ms // 1000)


class Spotter:
    """Hears its name when the test says so, on the next block it is fed."""
    name = "skippy"

    def __init__(self):
        self.armed = False

    def arm(self):
        self.armed = True

    def feed(self, block):
        hit, self.armed = self.armed, False
        return hit

    def reset(self):
        pass


class Link:
    """What Room asks of a box, recorded."""
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


class Gate:
    def __init__(self):
        self.said, self.stt_calls = [], 0

    def stt(self, wav):
        self.stt_calls += 1
        return self.said.pop(0) if self.said else ""

    def tts(self, text):
        return b"mp3"

    def summary(self, text):
        return "SUMMARY"


class Brain:
    def __init__(self):
        self.heard = []

    def reply(self, heard):
        self.heard.append(heard)
        return "Done, monkey."


def listening_room(spotter=True):
    log = []
    gate, brain, link = Gate(), Brain(), Link()
    spot = Spotter() if spotter else None
    room = Room("test", link, gate, brain, Timers(clock=lambda: 0), spotter=spot, log=log.append)
    return room, gate, brain, link, spot, log


async def hear(room, level, ms):
    """`ms` of one steady level on the room's microphone, a block at a time, on the room's own clock (it waits while the room is busy, so a slow machine cannot move it)."""
    for _ in range(ms // 50):
        waited = 0
        while room.mode == "busy":
            await asyncio.sleep(0.001)
            waited += 1
            assert waited < 5000, "the room stayed busy"
        room.feed_pcm(pcm(level, 50))
        await asyncio.sleep(0)


async def hear_name(room, spot, level=SOFT, ms=500):
    await hear(room, QUIET, 1000)
    await hear(room, level, ms)
    spot.arm()
    await hear(room, level, 50)


# ── finding 1: the longer outros are noise ──
SIX_OUTROS = [
    "Thanks for watching, see you next time.",
    "Thank you for watching and see you in the next video.",
    "Thank you for watching, and I'll see you in the next one.",
    "Please subscribe to my channel and like the video.",
    "Please subscribe and hit the bell.",
    "Thanks for watching, don't forget to like and subscribe",
]

# the family of them: the phrase with the audience, the video it is about, a subscribe asked a few ways, a greeting before it, the farewell in a sentence of its own
MORE_OUTROS = [
    "Thanks for watching!",
    "Thank you so much for watching!",
    "Thank you for watching this video.",
    "Thanks for watching my video.",
    "Thank you for watching the video.",
    "Thanks for watching my latest video!",
    "Thank you for watching this week's episode.",
    "Thanks for watching, and please subscribe.",
    "Thank you for watching, bye bye.",
    "Thanks for watching everyone, see you next time.",
    "And thank you for watching.",
    "Please subscribe to the channel.",
    "Please subscribe my channel.",
    "Please subscribe to my YouTube channel.",
    "Please subscribe!",
    "Please subscribe to my new channel.",
    "Please like and subscribe.",
    "Please like, share and subscribe.",
    "Thank you for watching. Like, comment and subscribe.",
    "Thank you for watching. Subscribe to my channel.",
    "Hi everyone, thanks for watching!",
    "Okay, thank you for watching.",
    "Thanks for watching. See you next time.",
    "Thank you for watching. I'll see you in the next video.",
    "Thank you for watching! Please subscribe to my channel.",
    "Thank you for watching! Don't forget to like and subscribe.",
    "Thanks for watching. I" + chr(0x2019) + "ll see you in the next video. Bye.",           # (a typographic apostrophe, as Whisper writes it now and then)
    "Thank you for watching... Have a great day.",
    "Subtitles by the Amara.org community",
]

# sentences that hold the words and are about something: they reach the house (round 1's pinned narrowing, and the ones it did not name)
REAL = [
    "thanks for watching the house while I was out",
    "thank you for watching the kids",
    "thanks for watching over the house",
    "thank you for watching it for me",
    "thanks for watching my back",
    "thanks for watching him",
    "thanks for watching a movie with me",
    "Hi, thanks for watching the kids",
    "please subscribe me to the weather alerts",
    "please subscribe to the weather alerts",
    "please subscribe my daughter to the school newsletter",
    "please share and subscribe me to the alerts",
    "thanks for watching the house while I was watching a video",
    "put the subtitles by default on the TV",
    "thank you, turn the lights off",
    "please turn on the lights",
    "please like this song",
]


@pytest.mark.parametrize("line", SIX_OUTROS + MORE_OUTROS)
def test_hub_r2_the_longer_outro_lines_whisper_writes_for_noise_are_not_a_command(line):
    kept, dropped = without_artifacts(line)
    assert kept == "", line
    assert " ".join(dropped) == line                                    # all of it, sentence by sentence, is what was taken out


@pytest.mark.parametrize("text", REAL)
def test_hub_r2_a_real_sentence_that_starts_with_the_outro_words_but_is_about_something_still_reaches_the_house(text):
    assert without_artifacts(text) == (text, [])


def test_hub_r2_a_command_with_an_outro_behind_it_keeps_the_command_and_loses_the_outro():
    assert without_artifacts("turn off the lights. Thanks for watching, see you next time.") == ("turn off the lights.", ["Thanks for watching, see you next time."])
    assert without_artifacts("Please turn off the lights. Please subscribe to my channel and like the video.") == ("Please turn off the lights.", ["Please subscribe to my channel and like the video."])
    kept, dropped = without_artifacts("turn off the lights. Thanks for watching! See you next time.")
    assert kept == "turn off the lights." and dropped == ["Thanks for watching!", "See you next time."]
    assert without_artifacts("turn off the lights, thank you for watching") == ("turn off the lights", ["thank you for watching"])       # round 1's end-of-command cut stays


def test_hub_r2_only_a_goodbye_right_behind_a_dropped_outro_goes_with_it():
    """Behind an outro the TV's own farewell is noise too ("Thanks for watching. See you next time."), but a command after it, and a goodbye said with no outro before it, are what they were."""
    assert without_artifacts("Thanks for watching. Please turn off the lights.") == ("Please turn off the lights.", ["Thanks for watching."])
    assert without_artifacts("Thanks for watching. Like this song.") == ("Like this song.", ["Thanks for watching."])
    assert without_artifacts("Thanks for watching. See you next time. Play some jazz.") == ("Play some jazz.", ["Thanks for watching.", "See you next time."])
    for alone in ("See you next time.", "Bye.", "Have a great day.", "Don't forget to turn off the oven."):
        assert without_artifacts(alone) == (alone, []), alone


def test_hub_r2_the_longer_outros_are_not_a_command_in_a_listening_room():
    """The six lines after the name: no brain, no answer aloud, and the room still listens for the command (the path _nothing_followed takes)."""
    async def go():
        for text in SIX_OUTROS:
            room, gate, brain, link, spot, log = listening_room()
            gate.said.append(text)
            await hear_name(room, spot, level=VOICE, ms=300)
            await hear(room, VOICE, 1200)
            await hear(room, QUIET, 1500)
            await asyncio.sleep(0.05)
            assert brain.heard == [], text
            assert room.mode == "listening" and any("only what Whisper makes of noise" in line for line in log), text
    asyncio.run(go())


def test_hub_r2_with_whisper_as_the_wake_word_the_name_and_a_longer_outro_leave_the_room_listening():
    """No wake-word model: Whisper hears the name (the room wakes) and then only what it makes of noise, the longer kind."""
    async def go():
        for text in ("Hey Skippy, thanks for watching, see you next time.", "Skippy, please subscribe to my channel and like the video."):
            room, gate, brain, link, spot, log = listening_room(spotter=False)
            gate.said.append(text)
            await hear(room, QUIET, 1000)
            await hear(room, VOICE, 1500)
            await hear(room, QUIET, 1500)
            await asyncio.sleep(0.05)
            assert ("woke", "skippy") in link.events and brain.heard == [], text
            assert room.mode == "listening" and ("released",) not in link.events, text
    asyncio.run(go())


def test_hub_r2_a_command_said_with_an_outro_after_it_reaches_the_brain_without_the_outro():
    async def go():
        room, gate, brain, link, spot, log = listening_room()
        gate.said.append("Turn off the lights. Thanks for watching, see you next time.")
        await hear_name(room, spot, level=VOICE, ms=300)
        await hear(room, VOICE, 1200)
        await hear(room, QUIET, 1500)
        await asyncio.sleep(0.05)
        assert brain.heard == ["Turn off the lights."]
    asyncio.run(go())


# ── finding 2: the note of who started the music is made only by a start that started something ──
@pytest.fixture
def started_note():
    import houseaudio
    houseaudio._STARTED.clear()
    yield houseaudio._STARTED
    houseaudio._STARTED.clear()


class AnswerRadio:
    """The house radio as music_tools sees it: a tool that answers whatever it is told to."""

    def __init__(self, said):
        self.said, self.calls = said, []

    def tool(self, name, args, room=""):
        self.calls.append((name, dict(args), room))
        return self.said


def a_start(said, name, args):
    """Run one music tool whose answer is `said`; -> the tool's own answer."""
    radio = AnswerRadio(said)
    _, run = music_tools(lambda: SimpleNamespace(name="living"), None, stereo=lambda: radio)
    return run(name, args), radio


def test_hub_r2_a_play_the_radio_found_nothing_for_does_not_become_the_reason_the_music_is_on(started_note):
    import houseaudio
    houseaudio.note_start("the morning routine")                         # the morning routine started the library ...
    said, radio = a_start("Nothing in his library matches 'Gasoline'.", "play_music", {"what": "Gasoline"})      # ... and "play Gasoline" finds nothing
    assert said.startswith("Nothing in his library matches") and radio.calls[0][0] == "play_music"
    assert started_note["by"] == "the morning routine"
    assert "started by the morning routine" in houseaudio.started_line()


def test_hub_r2_a_start_the_radio_could_not_do_leaves_the_note_of_the_one_before_it():
    import houseaudio
    for name, args, said in [("play_music", {"what": "x"}, "The house radio is not answering right now."),
                             ("music", {"action": "resume"}, "The house radio could not do that (ValueError)."),
                             ("spotify", {"action": "play", "what": "x"}, "Nothing in his library to play right now (the music gate is not answering)."),
                             ("audiobook", {"action": "resume"}, "The house radio is not answering right now.")]:
        houseaudio._STARTED.clear()
        houseaudio.note_start("the homecoming routine")
        a_start(said, name, args)
        assert houseaudio._STARTED["by"] == "the homecoming routine", (name, said)
    houseaudio._STARTED.clear()


def test_hub_r2_a_radio_that_is_away_does_not_make_the_note_either(started_note):
    """The real RadioClient with no radio behind it: its own answer is what start_ok() reads."""
    import houseaudio
    import radio_client

    def away(method, path, body=None, timeout=10.0):
        raise OSError("connection refused")
    client = radio_client.RadioClient("http://127.0.0.1:1", log=lambda m: None, request=away, start=False)
    _, run = music_tools(lambda: SimpleNamespace(name="living"), None, stereo=lambda: client)
    houseaudio.note_start("the homecoming routine")
    assert run("play_music", {"what": "x"}).startswith("The house radio is not answering")
    assert run("music", {"action": "resume"}).startswith("The house radio is not answering")
    assert started_note["by"] == "the homecoming routine"


def test_hub_r2_a_start_that_started_something_is_still_noted(started_note):
    import houseaudio
    houseaudio.note_start("the morning routine")
    a_start("Playing 3 tracks of heavy metal.", "play_music", {"what": "heavy metal"})
    assert started_note["by"] == "his own request"
    a_start("Nothing polka in his library, so here is the closest. Playing 12 tracks.", "play_music", {"what": "polka"})      # the closest is a start
    assert started_note["by"] == "his own request"
    houseaudio.note_start("the morning routine")
    a_start("Done: resume on the house radio.", "music", {"action": "resume"})
    assert started_note["by"] == "his resume"
    houseaudio.note_start("the morning routine")
    a_start("Done: the book plays.", "audiobook", {"action": "play", "book": "Dune"})
    assert started_note["by"] == "his own request"


def test_hub_r2_a_song_added_to_the_queue_or_a_pause_is_not_a_start(started_note):
    import houseaudio
    houseaudio.note_start("the morning routine")
    a_start("Queued 2 more.", "play_music", {"what": "x", "queue": True})
    a_start("Done: pause on the house radio.", "music", {"action": "pause"})
    assert started_note["by"] == "the morning routine"


# ── finding 3: "stop" does not wait on a radio that does not answer ──
class RadioStub:
    """The house radio as the hub sees it: playing or not, a count of his pauses, a tool that pauses it."""

    def __init__(self):
        self.playing, self.pauses, self.calls = True, 0, []

    def state(self):
        return {"playing": self.playing, "kind": "song", "title": "Gasoline"}

    def tool(self, name, args, room=""):
        self.calls.append((name, dict(args)))
        if name == "music" and args.get("action") == "pause":
            self.pauses += 1
            self.playing = False
        return "Done: paused."


class HungBrain:
    """A brain that knows what plays and whose music tool hangs (a radio that answers its poll but not the call) until the test lets it go."""

    def __init__(self, radio, release):
        import houseaudio
        self.heard, self.actions, self.radio, self.release = [], [], radio, release
        self.audio = houseaudio.Audio(radio)
        self.called = threading.Event()

    def reply(self, heard):
        self.heard.append(heard)
        return "Brain answer."

    def run_tool(self, name, args):
        self.actions.append({"tool": name, "args": args, "outcome": ""})
        self.called.set()
        self.release.wait(20)
        return self.radio.tool(name, args)


def house_log(path):
    day = time.strftime("%Y-%m-%d")
    file = path / "log" / f"{day}.jsonl"
    return [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()] if file.exists() else []


async def landed(radio, want=1):
    for _ in range(500):
        if radio.pauses >= want:
            return True
        await asyncio.sleep(0.01)
    return False


async def started(event, seconds=5.0):
    for _ in range(int(seconds / 0.01)):
        if event.is_set():
            return True
        await asyncio.sleep(0.01)
    return False


def test_hub_r2_a_plain_stop_signs_off_without_waiting_for_a_radio_that_does_not_answer(monkeypatch, tmp_path):
    import housemem
    monkeypatch.setattr(hub_mod, "STOP_AUDIO_WAIT_S", 1.5, raising=False)       # (a generous margin: the box is busy)

    async def go():
        release, radio, link, log = threading.Event(), RadioStub(), Link(), []
        brain = HungBrain(radio, release)
        room = Room("test", link, Gate(), brain, Timers(), log=log.append, hlog=housemem.HouseLog(tmp_path))
        try:
            await asyncio.wait_for(room.handle("stop"), 10)             # it must come back while the radio is still hanging
            assert await started(brain.called) and not release.is_set() and radio.pauses == 0       # (the call was made, and has not come back)
            assert ("released",) in link.events and room.mode == "idle" and brain.heard == []
            assert any("could not stop the house audio" in line for line in log), log
            records = [r for r in house_log(tmp_path) if r["kind"] == "command"]
            assert [(r["words"], r["outcome"], r["tools"]) for r in records] == [("stop", "sign_off", [])]       # no tool confirmed: none claimed
        finally:
            release.set()
        assert await landed(radio), "the radio is let go: the pause that was on its way lands, the stop is not lost"
    asyncio.run(go())


def test_hub_r2_with_the_real_music_tools_over_a_radio_client_whose_call_hangs_the_stop_is_counted_and_the_room_is_freed(monkeypatch):
    """The whole chain but the model: the room's stop -> the hub's music tools -> RadioClient.tool, whose HTTP call hangs (a radio that answers its poll and not the call)."""
    import houseaudio
    import radio_client
    monkeypatch.setattr(hub_mod, "STOP_AUDIO_WAIT_S", 1.5, raising=False)

    class ToolBrain:
        def __init__(self, client):
            self.heard, self.actions = [], []
            self.audio = houseaudio.Audio(client)
            _, self.run = music_tools(lambda: SimpleNamespace(name="test"), None, stereo=lambda: client)

        def reply(self, heard):
            self.heard.append(heard)
            return "Brain answer."

        def run_tool(self, name, args):
            return self.run(name, args)

    async def go():
        release, entered, link = threading.Event(), threading.Event(), Link()

        def hung(method, path, body=None, timeout=10.0):
            entered.set()
            release.wait(20)
            return {"said": "Done: paused."}
        client = radio_client.RadioClient("http://127.0.0.1:1", log=lambda m: None, request=hung, start=False)
        client.ok, client.last = True, {"playing": True}                 # its poll worked: the house is playing
        room = Room("test", link, Gate(), ToolBrain(client), Timers(), log=lambda m: None)
        try:
            await asyncio.wait_for(room.handle("stop"), 10)
            assert await started(entered) and client.pauses == 1         # the call is out there, and his stop is counted already
            assert ("released",) in link.events and room.mode == "idle"
        finally:
            release.set()
    asyncio.run(go())


def test_hub_r2_the_stop_that_ends_a_routine_does_not_wait_for_it_either(monkeypatch):
    """_also_stop_audio is the one place both the sign-off and a routine's end stop the house audio (handle() claims the routine's "stop" first)."""
    monkeypatch.setattr(hub_mod, "STOP_AUDIO_WAIT_S", 1.5, raising=False)

    async def go():
        release, radio, log = threading.Event(), RadioStub(), []
        brain = HungBrain(radio, release)
        room = Room("test", Link(), Gate(), brain, Timers(), log=log.append)
        try:
            tools = await asyncio.wait_for(room._also_stop_audio("stop"), 10)
            assert tools == [] and not release.is_set() and await started(brain.called)       # not stopped as far as it knows: the routine's record is not written
            assert any("could not stop the house audio" in line for line in log), log
        finally:
            release.set()
        assert await landed(radio)
    asyncio.run(go())


def test_hub_r2_a_radio_that_answers_is_still_stopped_and_counted_with_the_default_wait():
    async def go():
        release, radio, log = threading.Event(), RadioStub(), []
        release.set()                                                    # the tool answers at once
        room = Room("test", Link(), Gate(), HungBrain(radio, release), Timers(), log=log.append)
        assert await asyncio.wait_for(room._also_stop_audio("stop"), 6) == ["music"]
        assert radio.calls == [("music", {"action": "pause"})] and radio.pauses == 1
        assert any("stopped the house audio too" in line for line in log)
        assert 1 <= hub_mod.STOP_AUDIO_WAIT_S <= 10                      # long enough for a healthy radio, nowhere near its 40 s
    asyncio.run(go())
