"""Skippy's quick brain for the house: the persona, spoken style, a short memory, and timers as its only tool."""
from types import SimpleNamespace as NS

from brain import FORGET_AFTER_S, TOOLS, UNCLAIM, Brain, say_duration
from timers import Timers


def text(t):
    return NS(type="text", text=t)


def tool(name, **args):
    return NS(type="tool_use", id="tu_" + name, name=name, input=args)


class Client:
    """Answers from a script: each call returns the next list of blocks."""

    def __init__(self, *rounds):
        self.rounds, self.calls = list(rounds), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return NS(content=self.rounds.pop(0))


def make(*rounds, now=None):
    clock = [1000.0]
    timers = Timers(clock=lambda: clock[0])
    client = Client(*rounds)
    return Brain(client, "m", timers, persona="PERSONA", clock=lambda: clock[0], now=lambda: "Sunday 09:30"), client, timers, clock


def test_the_system_prompt_is_the_persona_plus_how_to_speak_aloud_and_the_time():
    b, c, *_ = make([text("Done.")])
    b.reply("hello")
    system = c.calls[0]["system"]
    assert system.startswith("PERSONA") and "speaking aloud" in system and "no markdown" in system and "Sunday 09:30" in system
    assert "replacing his Alexa" in system and c.calls[0]["model"] == "m" and c.calls[0]["max_tokens"] <= 700 and "WHOLE thing" in system


def test_a_timer_request_runs_the_tool_and_speaks_the_result():
    b, c, timers, _ = make([tool("set_timer", seconds=600, label="pasta")], [text("Ten minutes, on the clock.")])
    assert b.reply("set a pasta timer for ten minutes") == "Ten minutes, on the clock."
    assert timers.remaining() == [("pasta", 600)]
    second = c.calls[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result" and "Timer set: pasta, 10 minutes" in second[-1]["content"][0]["content"]
    assert second[-2]["role"] == "assistant"


def test_tools_cancel_and_list_and_bad_input_never_crash():
    b, _, timers, _ = make()
    timers.add(90, "tea")
    assert "tea: 1 minute 30 seconds left" in b.run_tool("list_timers", {})
    assert b.run_tool("cancel_timers", {"label": "tea"}) == "Cancelled 1 timer."
    assert b.run_tool("list_timers", {}) == "No timers running."
    assert b.run_tool("set_timer", {"seconds": "soon"}).startswith("That did not work")
    assert b.run_tool("set_timer", {"seconds": 0}).startswith("That did not work")
    assert b.run_tool("open_the_pod_bay_doors", {}) == "I have no such tool." and b.run_tool("set_timer", None).startswith("That did not work")


def test_it_remembers_the_last_few_exchanges_and_forgets_after_ten_quiet_minutes():
    b, c, _, clock = make([text("One.")], [text("Two.")], [text("Three.")])
    b.reply("first")
    b.reply("second")
    assert [m["content"] for m in c.calls[1]["messages"]] == ["first", "One.", "second"]       # the tool traces are not kept, only the words
    clock[0] += FORGET_AFTER_S + 1
    b.reply("third")
    assert [m["content"] for m in c.calls[2]["messages"]] == ["third"]


def test_it_never_loops_forever_on_tools_and_never_returns_nothing(monkeypatch):
    b, c, *_ = make(*[[tool("list_timers")] for _ in range(4)])
    assert "nothing to say" in b.reply("loop")                                                    # four rounds at most
    assert len(c.calls) == 4
    b2, c2, *_ = make([], [text("Back.")])                                                        # an empty reply is the model dropping out:
    b2.sleep = lambda s: None
    assert b2.reply("hmm") == "Back." and len(c2.calls) == 2                                      # asked again (brain.RETRY_FOR_S)
    import brain
    monkeypatch.setattr(brain, "RETRY_FOR_S", 0.0)
    b3, *_ = make([])
    assert "nothing to say" in b3.reply("hmm")                                                    # and when it stays empty, never silence


def test_the_only_tools_are_timers_and_say_duration_reads_naturally():
    assert {t["name"] for t in TOOLS} == {"set_timer", "cancel_timers", "list_timers"}            # no shell, no files, no deploys
    assert say_duration(1) == "1 second" and say_duration(61) == "1 minute 1 second" and say_duration(3600) == "1 hour" and say_duration(5400) == "1 hour 30 minutes"


def test_with_home_assistant_the_house_tools_join_the_timers_and_the_prompt_stops_saying_the_house_is_unconnected():
    from test_sat_hass import make as make_ha
    ha, ha_fake = make_ha()
    clock = [1000.0]
    timers = Timers(clock=lambda: clock[0])
    client = Client([NS(type="tool_use", id="tu_h", name="home_control", input={"name": "kitchen light", "action": "on"})], [text("Kitchen light is on.")])
    b = Brain(client, "m", timers, persona="PERSONA", clock=lambda: clock[0], now=lambda: "Sunday 09:30", hass=ha)
    assert b.reply("kitchen light on") == "Kitchen light is on."
    assert ha_fake.calls == [("light/turn_on", {"entity_id": "light.kitchen"})]
    assert {t["name"] for t in client.calls[0]["tools"]} == {"set_timer", "cancel_timers", "list_timers", "home_devices", "home_state", "home_control"}
    system = client.calls[0]["system"]
    assert "the thermostat (in the unit it shows)" in system and "not connected yet" not in system
    plain, c2, *_ = make([text("ok")])
    plain.reply("hi")
    assert {t["name"] for t in c2.calls[0]["tools"]} == {"set_timer", "cancel_timers", "list_timers"} and "not connected yet" in c2.calls[0]["system"]


def test_the_brain_knows_the_date():
    import time
    from brain import Brain
    b = Brain(None, "m", None, persona="P")
    assert time.strftime("%B %d, %Y") in b.system() and time.strftime("%A") in b.system()


def test_a_reply_that_claims_an_alarm_with_no_tool_called_is_asked_again_and_never_promises_what_was_not_done():
    calls = []
    b, c, *_ = make([text('Alarm set for 14:05, labeled "test".')], [tool("set_timer", seconds=60)], [text("Set, for real this time.")])
    assert b.reply("set an alarm for 14:05") == "Set, for real this time."
    assert "NOTHING was done" in str(c.calls[1]["messages"]) and b.actions
    b, c, *_ = make([text("Your alarm is set for seven.")], [text("Yes, it's set.")])
    assert b.reply("wake me at 7") == "That did not get done, whatever I just claimed. Say it again and I'll actually do it this time."
    b, c, *_ = make([text("It is seven o'clock, monkey.")])
    assert b.reply("what time is it") == "It is seven o'clock, monkey." and len(c.calls) == 1


def test_a_tool_call_written_as_text_is_run_and_annotations_are_never_spoken():
    b, c, timers, _ = make([text('[set_timer seconds="90" label="tea"]')], [text("Tea in ninety seconds. [SKIPPY_LEARN: {\"phrase\": \"tea\"}]")])
    assert b.reply("tea timer, ninety seconds") == "Tea in ninety seconds."
    assert timers.remaining() == [("tea", 90)] and "Timer set: tea" in str(c.calls[1]["messages"])
    assert "tools" not in c.calls[1]
    b, c, *_ = make([text('Noted. [SKIPPY_LEARN: {"phrase": "x", "intent": "y"}]')])
    assert b.reply("hello") == "Noted." and "SKIPPY_LEARN" not in str(b.history)
    b, c, *_ = make([text("[nonsense a=1] Sure.")])
    assert b.reply("hi") == "[nonsense a=1] Sure."                      # not a tool of ours: left alone


def test_plain_announcements_are_routed_to_the_tool():
    from brain import announce_route
    assert announce_route("announce upstairs that the reminders test is done") == {"text": "The reminders test is done", "place": "upstairs"}
    assert announce_route("Skippy, announce dinner is ready") == {"text": "Dinner is ready", "place": "everywhere"}
    assert announce_route("tell everyone the movie is starting") == {"text": "The movie is starting", "place": "everywhere"}
    assert announce_route("tell upstairs I'm leaving in five minutes") == {"text": "I'm leaving in five minutes", "place": "upstairs"}
    assert announce_route("tell me a joke") is None and announce_route("what's the weather") is None


def test_plain_music_commands_are_routed_to_the_music_tool():
    from brain import music_route
    assert music_route("What's playing?") == {"action": "what"} and music_route("who sings this") == {"action": "what"}
    assert music_route("what song is this") == {"action": "what"}
    assert music_route("turn it down") == {"action": "quieter"} and music_route("Skippy, louder please") == {"action": "louder"}
    assert music_route("volume five") == {"action": "volume", "level": 5} and music_route("set the volume to 3") == {"action": "volume", "level": 3}
    assert music_route("pause the music") == {"action": "pause"} and music_route("next song") == {"action": "next"}
    assert music_route("resume") == {"action": "resume"} and music_route("previous track") == {"action": "previous"}
    assert music_route("what's the weather") is None and music_route("play Creedence") is None and music_route("what's on my list") is None


def test_bilby_is_his_music_however_whisper_spells_it():
    # 2026-11-01: "Bill be everywhere." and "Tell Bill B to play music everywhere." went to Core, which didn't know Bilby and asked to confirm
    from brain import bilby_route, fix_bilby
    assert bilby_route("Bill be everywhere.") == {"what": "everything", "shuffle": True, "speaker": "everywhere"}
    assert bilby_route("Tell Bill B to play music everywhere.") == {"what": "everything", "shuffle": True, "speaker": "everywhere"}
    assert bilby_route("Skippy, bilby upstairs") == {"what": "everything", "shuffle": True, "speaker": "upstairs"}
    assert bilby_route("play bilby in the living room")["speaker"] == "upstairs"
    assert bilby_route("tell bilby to play some music on the sony")["speaker"] == "downstairs"
    assert bilby_route("bilby") == {"what": "everything", "shuffle": True}
    assert bilby_route("music everywhere")["speaker"] == "everywhere"
    for not_music in ("pay the bill by friday", "my phone bill", "did the bill be paid", "what is the weather", "play Pink Floyd upstairs"):
        assert bilby_route(not_music) is None, not_music
    assert fix_bilby("pay the bill by friday") == "pay the bill by friday" and fix_bilby("Bill B upstairs") == "Bilby upstairs"


def test_a_tool_call_written_with_a_colon_and_commas_is_still_caught():
    # 2026-11-01: "[play_music: what=everything, speaker=everywhere] Done." was spoken and nothing played
    from brain import TEXT_ARG, TEXT_CALL
    m = TEXT_CALL.search("Playing music everywhere then. [play_music: what=everything, speaker=everywhere] Done.")
    assert m and m.group(1) == "play_music"
    assert {a.group(1): next(g for g in a.groups()[1:] if g is not None) for a in TEXT_ARG.finditer(m.group(2))} == {"what": "everything", "speaker": "everywhere"}
    assert TEXT_CALL.search('[list action="read" list="shopping"]').group(1) == "list"


# ── 2026-11-04: the model said it played, moved and turned up the music, and called nothing ─────────────────────────────────────────────────────────
# "Playing your heavy metal library on the theater's Sony speakers right now." and "Playing ... *turns an imaginary dial* There. Magnificently loud and appropriately
# chaotic for housekeeping staff." (the radio's log had no tool call for either): the music stayed on the theater's own speakers and the housekeeper upstairs
# heard it only on the box. A reply that claims music was played, moved or turned, to a request for it, with no music tool run, is asked again.
def music_brain(*rounds):
    ran = []
    tools = [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in ("play_music", "music", "speakers")]
    b, c, *_ = make(*rounds)
    b.extra_tools, b.run_extra = tools, lambda name, args: ran.append((name, args)) or f"({name} done)"
    return b, c, ran


def test_a_claim_of_music_with_no_music_tool_run_is_asked_again_and_the_second_try_can_do_it():
    b, c, ran = music_brain([text("*turns an imaginary dial* There. Magnificently loud and appropriately chaotic for housekeeping staff.")],
                            [tool("music", action="louder")], [text("Louder, for the maid.")])
    assert b.reply("Skippy, crank it up for the maid, make the volume louder") == "Louder, for the maid." and ran == [("music", {"action": "louder"})]
    assert "you called no music tool" in str(c.calls[1]["messages"]) and "tools" in c.calls[1]
    b, c, ran = music_brain([text("Consider it done. Playing your heavy metal library on the theater's Sony speakers right now.")], [text("I did it, honest.")])
    assert b.reply("Skippy, put something loud on, play some heavy metal for me") == UNCLAIM and not ran       # still nothing run: he hears that it did not happen


def test_chatter_about_music_and_answers_after_a_music_tool_are_left_alone():
    for heard, said in (("do you like playing music at night", "Playing music at night is fine, monkey."),
                        ("can you recommend some good heavy metal bands", "Playing it safe: Judas Priest, Iron Maiden, Black Sabbath. Classic songs, all of them."),
                        ("tell me a joke about a band playing music", "A band walks into a bar, playing music: ouch."),
                        ("play me a haiku", "Playing along: autumn moon, bright, a beer can sighs, nobody listens.")):
        b, c, ran = music_brain([text(said)])
        assert b.reply(heard) == said and len(c.calls) == 1 and not ran, heard
    b, c, ran = music_brain([tool("play_music", genre="goth")], [text("Playing goth upstairs, as ordered.")])
    assert b.reply("play something dark for the maid, goth please") == "Playing goth upstairs, as ordered." and len(c.calls) == 2           # a tool ran: not a claim


def test_play_it_on_the_upstairs_stereo_runs_the_speakers_tool_before_the_model_answers():
    b, c, ran = music_brain([text("Upstairs it is. The maid will be thrilled, I'm sure.")])
    said = b.reply("Play it on the upstairs stereo. The maid is coming over to clean and they like heavy metal")
    assert ran == [("speakers", {"what": "music", "place": "upstairs", "play": True})] and said.startswith("Upstairs it is")
    assert "(speakers done)" in str(c.calls[0]["messages"]) and "tools" not in c.calls[0]          # the tool ran first; the model only words the answer
    b, c, ran = music_brain([text("Heavy metal, upstairs, on the Enclave.")])
    b.reply("Play heavy metal music upstairs on the enclave")
    assert ran == [("play_music", {"speaker": "upstairs", "genre": "heavy metal", "shuffle": True})]
