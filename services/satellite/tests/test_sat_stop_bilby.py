"""Stopping Bilby, and the house chat knowing what Bilby is (the Captain, 2026-11-04).

He said "stop the music" as he came in at 19:31; the homecoming routine's own library shuffle started it again 90 seconds later and it played on the Enclave for fifty
minutes while "stop Bilby" and the rest never reached a tool (the model heard them, and a model may call nothing). Bilby is the ONE player for all the house's audio (music,
Spotify, Audible books): a stop with an audio word or with Bilby named is always a stop, a bare "stop" / "enough" / "shut up" is one while the house plays, a stop after a routine
began keeps that routine's music off, and the chat's prompt says what Bilby is, how to talk to it, which tools it really has and what is playing right now."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import houseaudio  # noqa: E402
import radio_client  # noqa: E402
from brain import BILBY, music_route, stop_route  # noqa: E402
from test_sat_brain import make, text  # noqa: E402

PAUSE = {"action": "pause"}

# a stop with an audio word, or with Bilby named: always a stop, playing or not
STOPS = ["stop the music", "stop music", "stop Bilby", "Bilby stop", "Bilby, stop", "stop bilby music", "turn off Bilby", "tell Bilby to stop", "tell bilby to stop playing",
         "hey Skippy stop Bilby", "Skippy, stop the music please", "can you stop the music", "turn off the music", "turn the music off", "shut the music off", "kill the music",
         "cut the music", "mute the music", "silence the music", "stop playing", "stop playing music", "stop playing heavy metal", "stop the heavy metal", "turn off the heavy metal",
         "stop all music", "stop the audio", "stop the radio", "stop the shuffle", "stop the speakers", "stop everything", "no more music", "enough music", "make the music stop",
         "I want the music to stop", "stop the book", "pause the audiobook", "stop the stereo", "turn off the stereo", "Bilby, shut up", "bilby enough", "turn it off bilby",
         "stop the music downstairs", "stop the music upstairs", "stop the heavy metal upstairs", "stop playing everywhere"]       # a floor named: Bilby is one stream, so it all stops
# only while the house plays: a person can say these to anything
BARE = ["stop", "enough", "that's enough", "shut up", "be quiet", "quiet", "silence", "mute", "turn it off", "turn that off", "kill it", "cut it out", "make it stop",
        "knock it off", "shut it off", "no more", "stop that"]
# never: the model's (a timed, conditional or one-floor stop, a negation, another thing, or only talk about stopping)
NOT_STOPS = ["stop the music when I leave", "stop the music in twenty minutes", "stop the music at eight", "don't stop the music", "I can't stop the music", "never stop the music",
             "turn off the lights", "turn off the kitchen lights", "stop talking", "stop the timer", "stop the car",
             "how do I stop the music", "what is the music called", "stop the music video", "turn off the TV", "stop by the store", "play the music", "turn the music up",
             "stop the music if it rains", "pause for effect"]


def test_every_way_of_stopping_with_an_audio_word_or_bilby_named_is_a_stop():
    for heard in STOPS:
        for playing in (None, False, True):
            assert music_route(heard, playing) == PAUSE, (heard, playing)


def test_a_bare_stop_is_a_stop_only_while_the_house_is_playing():
    for heard in BARE:
        assert music_route(heard) is None and music_route(heard, False) is None, heard       # nothing plays, or the brain cannot tell: the model hears it
        assert music_route(heard, True) == PAUSE, heard
    assert stop_route("Bilby, stop", False) == PAUSE and stop_route("stop", None) is None            # naming Bilby makes it his, playing or not


def test_conditional_timed_one_floor_and_other_things_are_left_to_the_model():
    for heard in NOT_STOPS:
        got = music_route(heard, True)
        assert got in (None, {"action": "louder"}, {"action": "resume"}) and got != PAUSE, (heard, got)


def test_the_old_plain_commands_are_unchanged():
    assert music_route("louder") == {"action": "louder"} and music_route("what's playing") == {"action": "what"} and music_route("next") == {"action": "next"}
    assert music_route("resume the music") == {"action": "resume"} and music_route("set the volume to 5") == {"action": "volume", "level": 5}
    assert music_route("pause") == PAUSE and music_route("stop it") == PAUSE and music_route("turn the volume down") == {"action": "quieter"}


# ── the chat: the tool runs before the model, and the model is told what Bilby is ──
class FakeRadio:
    def __init__(self, **state):
        self.last = state
        self.playing = bool(state.get("playing"))

    def state(self):
        return dict(self.last)


def music_brain(*rounds, radio=None, routine=""):
    ran = []
    tools = [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in ("play_music", "music", "speakers", "dj_mode", "audiobook")]
    b, c, *_ = make(*rounds)
    b.extra_tools, b.run_extra = tools, lambda name, args: ran.append((name, args)) or f"({name} done)"
    routines = NS(busy=bool(routine), run=NS(name=routine))
    b.audio = houseaudio.Audio(radio, routines)
    return b, c, ran


def test_stop_bilby_runs_the_music_tool_before_the_model_and_the_model_only_words_it():
    b, c, ran = music_brain([text("Silence. You are welcome.")], radio=FakeRadio(playing=True, title="Gasoline", artist="MDFMK"))
    assert b.reply("stop Bilby") == "Silence. You are welcome." and ran == [("music", {"action": "pause"})]
    assert len(c.calls) == 1 and "tools" not in c.calls[0] and "The music tool's answer for this request: (music done)" in str(c.calls[0]["messages"])


def test_a_bare_stop_while_a_book_plays_reaches_the_tool_and_a_bare_stop_with_nothing_playing_does_not():
    b, c, ran = music_brain([text("Quiet.")], radio=FakeRadio(playing=True, kind="book", title="Aces", book={"title": "Aces", "chapter": 3, "chapters": 9}))     # a book is the station's item
    assert b.reply("enough") == "Quiet." and ran == [("music", {"action": "pause"})]
    b, c, ran = music_brain([text("Enough of what, monkey?")], radio=FakeRadio(playing=False))
    assert b.reply("enough") == "Enough of what, monkey?" and ran == [] and "tools" in c.calls[0]
    assert "the audiobook 'Aces' by A. Writer, chapter 3 of 9" in houseaudio.station_line(dict(playing=True, kind="book", title="Aces", book={"title": "Aces", "author": "A. Writer", "chapter": 3, "chapters": 9},
                                                                                         open=["enclave"], level=0.5))


def test_the_prompt_teaches_bilby_lists_the_real_tools_and_says_what_plays_right_now():
    state = dict(playing=True, title="Gasoline", artist="MDFMK", open=["enclave"], speakers=["enclave"], closed=[], level=0.7, queue_len=700, dj=True)
    b, c, ran = music_brain([text("Done.")], radio=FakeRadio(**state), routine="homecoming")
    b.reply("hello")
    system = c.calls[0]["system"]
    assert "Bilby: the house's audio" in system and "the ONE player" in system and "single stream" in system and "last-resort speaker" in system
    assert "act ONLY through these tools: audiobook, cancel_timers, dj_mode, list_timers, music, play_music, set_timer, speakers." in system
    assert "Out of your reach from here: his car, physics calculations (Mentat)" in system and "Never claim you did what no tool did" in system
    live = system.split("## Right now in the house\n", 1)[1]
    assert "Bilby is playing Gasoline by MDFMK on the Enclave upstairs. Volume is 7 of 10. 700 tracks are queued, DJ mode is on." in live
    assert "homecoming routine is running" in live
    b2, c2, _ = music_brain([text("Done.")])
    b2.audio = None
    b2.reply("hello")
    assert "Right now in the house" not in c2.calls[0]["system"] and "Bilby: the house's audio" in c2.calls[0]["system"]
    b3, c3, *_ = make([text("Done.")])
    b3.reply("hello")
    assert "Bilby: the house's audio" not in c3.calls[0]["system"] and BILBY not in c3.calls[0]["system"] and "timers" in c3.calls[0]["system"].lower()


def test_a_question_about_the_house_answered_with_how_things_stand_is_not_a_claim_but_doing_something_still_is():
    import musicguard as g
    heard = "Why was the Enclave playing heavy metal on its own?"          # the real model's answer to this was cut to "I have not played, moved or changed any music."
    said = "[sighs] Because DJ mode is currently enabled. Bilby is playing Gasoline by MDFMK on the Enclave upstairs. Volume is 7 of 10."
    assert g.describing(heard) and not g.describing("play some metal") and not g.describing("what's playing")
    assert g.classify(heard, "", said, []) == (None, None)
    assert g.classify(heard, "", "I paused the music for you.", []) == ("retract", "claim")
    assert g.strip_claims("Because DJ mode is currently enabled. I paused the music for you.", True) == \
        "Because DJ mode is currently enabled. I have not played, moved or changed any music."
    assert g.classify("play some metal", "", "Bilby is playing Gasoline now.", [])[0] == "reask"          # a command is judged as before
    b, c, ran = music_brain([text(said)], radio=FakeRadio(playing=True, title="Gasoline", artist="MDFMK"))
    assert b.reply(heard) == said and len(c.calls) == 1 and not ran         # the chat answers from the prompt's lines, and the guard lets it


# ── houseaudio: the lines the model reads ──
def test_the_station_in_a_sentence():
    playing = houseaudio.station_line(dict(playing=True, title="T", artist="A", open=["enclave", "hdmi"], closed=[], level=0.4, queue_len=5))
    assert playing == "Bilby is playing T by A on the Enclave upstairs and the Sony downstairs. Volume is 4 of 10. 5 tracks are queued."
    paused = houseaudio.station_line(dict(playing=False, title="T", artist="", open=["enclave"], closed=["hdmi"], level=0.7, queue_len=700, dj=True))
    assert paused.startswith("Bilby is paused on T. Volume is 7 of 10. 700 tracks are queued, DJ mode is on. 'Resume' carries on.") and "Switched off right now: the Sony downstairs." in paused
    assert houseaudio.station_line(dict(playing=False)) == "Bilby is not playing anything."
    assert "not answering" in houseaudio.station_line({}) and "not answering" in houseaudio.station_line(None)
    assert houseaudio.kind_words("pod:living") == "the box in the living" and houseaudio.kind_words("bt") == "the Bluetooth stereo"


def test_the_status_says_who_started_the_music_that_is_queued():
    state = dict(playing=True, title="T", artist="A", open=["enclave"], closed=[], level=0.7, queue_len=700)
    houseaudio._STARTED.clear()
    a = houseaudio.Audio(FakeRadio(**state))
    assert "started by" not in a.status()                                      # unknown (a restarted hub): nothing is made up
    houseaudio.note_start("the homecoming routine", clock=lambda: 1_793_000_000.0)
    when = __import__("time").strftime("%I:%M %p", __import__("time").localtime(1_793_000_000.0)).lstrip("0")
    assert f"This music was started by the homecoming routine at {when}." in a.status()
    assert "started by" not in houseaudio.Audio(FakeRadio(playing=False, queue_len=0)).status()       # nothing queued: nothing to explain
    houseaudio._STARTED.clear()


def test_the_house_log_feeds_the_recently_lines_so_the_chat_can_say_why_the_music_is_on(tmp_path):
    import time
    import housemem
    now = [time.mktime((2026, 11, 4, 19, 40, 0, 0, 0, -1))]
    h = housemem.HouseLog(tmp_path, clock=lambda: now[0])
    for hh, mm, kind, fields in ((16, 5, "command", dict(words="what time is it", tools=[])),          # three and a half hours ago: not recent
                                 (19, 30, "routine", dict(routine="homecoming", outcome="arrived")), (19, 31, "routine", dict(routine="homecoming", outcome="started")),
                                 (19, 31, "command", dict(words="stop the music", tools=["music"])), (19, 33, "routine", dict(routine="homecoming", outcome="started the music"))):
        now[0] = time.mktime((2026, 11, 4, hh, mm, 0, 0, 0, -1))
        h.event(kind, **fields)
    now[0] = time.mktime((2026, 11, 4, 19, 40, 0, 0, 0, -1))
    assert [r["time"] for r in h.recent()] == ["19:30", "19:31", "19:31", "19:33"]
    assert houseaudio.recent_line(h.recent()) == ('Recently: 19:30 the car arrived home; 19:31 the homecoming routine started; 19:31 he said "stop the music" (music); '
                                                  '19:33 the homecoming routine: started the music.')
    assert houseaudio.recent_line([]) == "" and h.recent(since_s=60) == []
    houseaudio._STARTED.clear()
    a = houseaudio.Audio(FakeRadio(playing=True, title="T", artist="A", open=["enclave"], queue_len=700), hlog=h)
    assert "19:31 he said \"stop the music\" (music)" in a.status()
    assert "Recently" not in houseaudio.Audio(FakeRadio(playing=False), hlog=housemem.HouseLog(tmp_path / "empty", clock=lambda: now[0])).status()
    assert "not answering" in houseaudio.Audio(None, hlog=NS(recent=lambda: 1 / 0)).status()           # a broken log never breaks the prompt


def test_audio_playing_means_the_station_which_includes_a_book_and_never_raises():
    assert not houseaudio.Audio(FakeRadio(playing=False)).playing()
    assert houseaudio.Audio(FakeRadio(playing=True, kind="book")).playing()                     # a book is an item of the one stream: the station playing is the house playing
    broken = houseaudio.Audio(NS(), NS(busy=True, run=None))
    assert broken.playing() is False and "not answering" in broken.status()


# ── his stop counts: the routine's music step reads it ──
def test_radio_client_counts_his_stops_through_the_music_tool_only():
    sent = []
    c = radio_client.RadioClient(request=lambda m, p, b=None, t=10: sent.append((p, b)) or {"said": "ok"}, start=False)
    assert c.tool("music", {"action": "pause"}) == "ok" and c.pauses == 1
    c.tool("music", {"action": "louder"})
    c.tool("play_music", {"genre": "goth"})
    c.tool("music", {"action": "stop"})
    assert c.pauses == 2
    c.tool("audiobook", {"action": "where"})
    assert c.pauses == 2
    c.tool("audiobook", {"action": "pause"})                                 # a book is the same stream: its pause is the one stop, and his
    assert c.pauses == 3
    c.pauses = 2
    asyncio.run(c.stop())                                                    # a routine's own stop is not his
    assert c.pauses == 2


def test_a_stop_after_the_homecoming_routine_began_keeps_its_library_shuffle_off(tmp_path):
    from test_sat_daily import AWAY, HOME, car_at, finish, make as daily_make, stamp
    now = [stamp("2026-11-02", "08:00")]
    d, room, link, brain, house, posts, car = daily_make(tmp_path, now, car=car_at(AWAY))
    shuffles = []

    class Stereo:
        playing, pauses = False, 0

        async def shuffle(self):
            shuffles.append(1)
            return 10
    stereo = Stereo()
    d.stereo = lambda: stereo

    async def news():                                                      # he says "stop the music" while the news is read
        stereo.pauses += 1
        return "The news, briefly."
    d.latest_news = news

    async def go():
        await d.tick(room)
        now[0] = stamp("2026-11-02", "18:30")
        car["cars"][0].update(latitude=HOME[0], longitude=HOME[1])
        d._car_check = -1e9
        await d.tick(room)
        room._voice_at = __import__("time").monotonic() + 1
        await d.tick(room)
        await finish(d)
    asyncio.run(go())
    assert shuffles == [] and len(link.spoken) >= 2                        # the routine said its words, and left the music off
