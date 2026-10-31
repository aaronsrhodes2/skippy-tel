"""The Home chat's hub side: only what is addressed to Skippy (and what he says) is forwarded, words only; typed words behave like spoken ones."""
import asyncio

from homechat import HomeChat
from hub import FOLLOWUP, IDLE, Room
from timers import Timers


class Link:
    hub_follows_up = True

    def __init__(self):
        self.spoken, self.room = [], None

    async def heard(self, t): pass
    async def released(self): pass
    async def woke(self, by): pass
    async def show(self, t): pass
    async def voice_started(self): pass
    async def voice_stopped(self): pass

    async def speak(self, said, voice, follow_up=False):
        self.spoken.append(said)

    announce = speak


class Gate:
    def tts(self, text): return b"mp3"
    def summary(self, text): return text
    def stt(self, wav): return "not for you"


class Brain:
    def __init__(self):
        self.heard, self.actions = [], []

    def reply(self, heard):
        self.heard.append(heard)
        self.actions = [{"tool": "home_control", "args": {"name": "Desk Lamp", "action": "on"}, "outcome": "ok"}, {"tool": "web_search", "args": {}, "outcome": "x"}]
        return "Lamp on. You are welcome, monkey."


def make(home=None, **kw):
    link = Link()
    hc = home or HomeChat("http://portal", "tok", post=lambda url, body: {}, log=lambda m: None)
    room = Room("office", link, Gate(), Brain(), Timers(), log=lambda m: None, filler=None, homechat=hc, **kw)
    return room, link, hc


def kinds(hc):
    return [(e["kind"], e.get("text") or e.get("tools")) for e in hc.queue]


def test_an_addressed_command_forwards_the_words_the_actions_and_the_reply():
    room, link, hc = make()
    asyncio.run(room.handle("turn on the lamp"))
    assert [e["kind"] for e in hc.queue] == ["heard", "actions", "said"]
    heard, actions, said = hc.queue
    assert heard["text"] == "turn on the lamp" and heard["room"] == "office"
    assert actions["tools"] == ["home: Desk Lamp"]                          # lights in one folded line; web_search is not a house action
    assert said["text"].startswith("Lamp on") and said["mood"] and "typed" not in said
    assert link.spoken == ["Lamp on. You are welcome, monkey."]


def test_room_talk_that_was_not_for_him_is_never_forwarded():
    room, link, hc = make()
    room.mode = IDLE
    asyncio.run(room._utterance(IDLE, [0.0] * 800))                        # Whisper heard "not for you": no name, no wake
    assert list(hc.queue) == []


def test_nothing_is_forwarded_while_a_recorder_runs_or_forwarding_is_off():
    room, _, hc = make()
    room.recorder = object()
    asyncio.run(room.say("Say it again."))
    assert list(hc.queue) == []
    room.recorder = None
    hc.forward = False
    asyncio.run(room.handle("hello"))
    assert list(hc.queue) == []
    hc.forward, hc.enabled = True, False
    asyncio.run(room.handle("hello"))
    assert list(hc.queue) == []


def test_a_timer_or_announcement_is_forwarded_as_an_announcement():
    room, link, hc = make()
    asyncio.run(room.announce("Your pasta timer is done."))
    assert kinds(hc) == [("announce", "Your pasta timer is done.")]


def test_typed_say_is_answered_like_speech_and_marked_typed_without_a_second_heard():
    room, link, hc = make()
    asyncio.run(room.typed("turn on the lamp", True))
    assert [e["kind"] for e in hc.queue] == ["actions", "said"]            # the portal itself shows the typed words
    assert hc.queue[-1]["typed"] is True and link.spoken                     # spoken on the room's outputs
    assert room.mode == IDLE


def test_typed_say_with_speak_off_only_appears_in_the_thread():
    room, link, hc = make()
    asyncio.run(room.typed("what time is it", False))
    assert link.spoken == [] and hc.queue[-1]["kind"] == "said" and hc.queue[-1]["silent"] is True


def test_typed_does_not_disturb_an_open_conversation():
    room, _, hc = make()
    room.mode = FOLLOWUP
    asyncio.run(room.typed("hello", True))
    assert room.mode == FOLLOWUP


def test_sync_loop_posts_events_and_runs_the_captains_commands():
    sent = []
    replies = [{"forward": True, "commands": [{"id": "1", "kind": "announce", "room": "*", "text": "Dinner is ready.", "speak": True},
                                              {"id": "2", "kind": "say", "room": "office", "text": "lights off", "speak": False}]}]
    hc = HomeChat("http://portal/", "tok", post=lambda url, body: (sent.append((url, body)), replies.pop(0) if replies else {})[1], log=lambda m: None)
    room, link, _ = make(home=hc)

    class Hub:
        rooms = {"office": room}

    async def go():
        hc.emit(room, "heard", text="hello")
        await hc.once(Hub())
        await asyncio.gather(*list(hc.tasks))
    asyncio.run(go())
    url, body = sent[0]
    assert url == "http://portal/home/house/sync" and body["rooms"] == ["office"] and body["events"][0]["text"] == "hello"
    assert link.spoken == ["Dinner is ready."]                               # the announcement was spoken; the silent "say" was not
    assert [e["kind"] for e in hc.queue] == ["announce", "actions", "said"]


def test_an_unreachable_portal_keeps_the_words_for_the_next_try():
    def boom(url, body):
        raise OSError("down")
    hc = HomeChat("http://portal", "tok", post=boom, log=lambda m: None)
    room, _, _ = make(home=hc)

    class Hub:
        rooms = {}

    hc.emit(room, "heard", text="hello")
    asyncio.run(hc.once(Hub()))
    assert [e["text"] for e in hc.queue] == ["hello"]


def test_switching_forwarding_off_from_the_portal_clears_the_queue():
    hc = HomeChat("http://portal", "tok", post=lambda url, body: {"forward": False}, log=lambda m: None)
    room, _, _ = make(home=hc)

    class Hub:
        rooms = {}

    hc.emit(room, "heard", text="hello")
    asyncio.run(hc.once(Hub()))
    hc.emit(room, "heard", text="again")
    assert hc.forward is False and list(hc.queue) == []
