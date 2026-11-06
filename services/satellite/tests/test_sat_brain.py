"""Skippy's quick brain for the house: the persona, spoken style, a short memory, and timers as its only tool."""
from types import SimpleNamespace as NS

from brain import FORGET_AFTER_S, TOOLS, UNCLAIM, UNCLAIM_MUSIC, Brain, say_duration
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
    assert system.startswith("PERSONA") and "speaking aloud" in system and "no markdown" in system and "Sunday 09:30" not in system      # the clock rides in the note that starts his message (Brain.context)
    assert "replacing his Alexa" in system and c.calls[0]["model"] == "m" and c.calls[0]["max_tokens"] <= 700 and "WHOLE thing" in system and "Sunday 09:30" in c.calls[0]["messages"][-1]["content"]


def test_a_timer_request_runs_the_tool_and_speaks_the_result():
    b, c, timers, _ = make([tool("set_timer", seconds=600, label="pasta")], [text("Ten minutes, on the clock.")])
    assert b.reply("I want a pasta timer for ten minutes") == "Ten minutes, on the clock."          # (a plain "set a pasta timer for ten minutes" is routed now, before the model: test_sat_speed_brain)
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
    assert [m["content"] for m in c.calls[1]["messages"][:2]] == ["first", "One."] and c.calls[1]["messages"][2]["content"].endswith("\n\nsecond")       # the tool traces and the context note are not kept, only the words
    clock[0] += FORGET_AFTER_S + 1
    b.reply("third")
    assert len(c.calls[2]["messages"]) == 1 and c.calls[2]["messages"][0]["content"].endswith("\n\nthird")


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
    assert b.reply("it is too dark in the kitchen") == "Kitchen light is on."          # (a plain "kitchen light on" and, since fix:brain-routes P1, "I want the kitchen light on" are routed before the model: test_sat_speed_brain)
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
    assert time.strftime("%B %d, %Y") in b.context() and time.strftime("%A") in b.context() and time.strftime("%B %d, %Y") not in b.system()          # the date rides in the note, not the prompt


def test_a_reply_that_claims_an_alarm_with_no_tool_called_is_asked_again_and_never_promises_what_was_not_done():
    calls = []
    b, c, *_ = make([text('Alarm set for 14:05, labeled "test".')], [tool("set_timer", seconds=60)], [text("Set, for real this time.")])
    assert b.reply("set an alarm for 14:05") == "Set, for real this time."
    assert "NOTHING was done" in str(c.calls[1]["messages"]) and b.actions
    b, c, *_ = make([text("Your alarm is set for seven.")], [text("Yes, it's set.")])
    assert b.reply("wake me at 7") == "That did not get done, whatever I just claimed. Say it again and I'll actually do it this time."
    b, c, *_ = make([text("It is seven o'clock, monkey.")])
    assert b.reply("what time is it") == "It is seven o'clock, monkey." and len(c.calls) == 1


def test_a_reply_that_says_it_cannot_with_no_tool_called_is_asked_again_with_the_tools_it_has():
    """The Captain, 2026-11-04: "I do not have access to your car's systems" with the tool right there; "You are wrong, you DO have access. Missed tool call."
    (2026-11-05: the house chat "still gets confused about what it has access to")."""
    b, c, *_ = make([text("I have no access to your timers, Captain.")], [tool("list_timers")], [text("Nothing is running, monkey.")])
    assert b.reply("what timers do I have") == "Nothing is running, monkey."
    nudge = str(c.calls[1]["messages"])
    assert "you DO have these" in nudge and "list_timers" in nudge and [a["tool"] for a in b.actions] == ["list_timers"] and b.guard == ("reask", "denial")
    b, c, *_ = make([text("I cannot browse the stars from here.")], [text("Still no: I have no tool for that.")])
    assert b.reply("take me to the stars") == "I cannot browse the stars from here." and len(c.calls) == 2         # no tool ran: the first reply stands, it may be true
    b, c, *_ = make([text("I cannot believe it is already November, monkey.")])
    assert b.reply("it's november") == "I cannot believe it is already November, monkey." and len(c.calls) == 1   # a figure of speech is no denial


def test_the_second_try_never_touches_a_door_and_never_confirms_for_him():
    """A real run, 2026-11-05: "Can you open the garage door?" was answered "it requires confirmation"; asked again, the model called home_control open with confirm=true."""
    b, c, *_ = make([text("I cannot operate the garage door from this voice interface.")])
    assert b.reply("can you open the garage door") == "I cannot operate the garage door from this voice interface." and len(c.calls) == 1      # not even asked again
    b, c, *_ = make([text("I cannot do that.")], [tool("set_timer", seconds=60, confirm=True)], [text("A minute.")])
    assert b.reply("I want a one minute timer") == "A minute." and "confirm" not in b.actions[0]["args"] and b.actions[0]["args"]["seconds"] == 60


def test_the_prompt_says_in_his_words_what_the_tools_are_and_only_while_they_exist():
    b, *_ = make()
    assert "In his words, you can: timers." in b.system()
    b.extra_tools = [{"name": "audiobook", "description": "", "input_schema": {"type": "object", "properties": {}}}]
    said = b.system()
    assert "his audiobooks" in said and "Bilby" not in said.split("In his words, you can:")[1].split("Out of your reach")[0]


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
    assert b.reply("Skippy, put something loud on, get some heavy metal going for me") == UNCLAIM_MUSIC and not ran       # still nothing run: he hears that it did not happen


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


# ── 2026-11-05, the review of the denial's second try ("I cannot ..." with no tool called gets one more go): what it may touch, when it runs, and that it never costs the first answer ──
def house_brain(tmp_path, *rounds):
    """A real Brain over a REAL alarm list and REAL lists (what cancel_alarm {} and list clear really do), a scripted model, and fake music / announce tools. The house has two alarms
    (the weekday wake-up, a dentist), a shopping list of three and a pasta timer."""
    import alarms as alarms_mod
    import lists as lists_mod
    tmp_path.mkdir(parents=True, exist_ok=True)
    clock = [1_793_000_000.0]
    al = alarms_mod.Alarms(tmp_path / "alarms.json", clock=lambda: clock[0])
    ls = lists_mod.Lists(tmp_path / "lists.json")
    ran = []
    music_props = {"action": {"type": "string", "enum": ["pause", "resume", "louder", "what"]}}
    fakes = [{"name": n, "description": n, "input_schema": {"type": "object", "properties": music_props if n == "music" else {}}} for n in ("music", "play_music", "speakers", "announce")]
    pairs = [al.tools("kitchen"), ls.tools(), (fakes, lambda name, args: ran.append((name, dict(args))) or f"({name} done)")]
    tools = [t for ts, _ in pairs for t in ts]

    def run_extra(name, args):
        return next(fn for ts, fn in pairs if name in {t["name"] for t in ts})(name, args)
    timers = Timers(clock=lambda: clock[0])
    client = Client(*rounds)
    b = Brain(client, "m", timers, persona="PERSONA", clock=lambda: clock[0], now=lambda: "Sunday 09:30", extra=(tools, run_extra))
    al.add("alarm", "07:00", "wake-up", "weekdays")
    al.add("alarm", "09:30", "dentist", "once")
    ls.run("list", {"action": "add", "list": "shopping", "items": ["milk", "eggs", "bread"]})
    timers.add(600, "pasta")
    return b, client, ran, al, ls, timers


class Script:
    """A model that follows a script: each call returns the next step (a list of blocks) or raises it (an exception); the last step repeats. More than 14 calls is a runaway: it raises a
    plain RuntimeError, so a retry loop that never ends fails the test instead of hanging it."""

    def __init__(self, *steps):
        self.steps, self.calls = list(steps), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) > 14:
            raise RuntimeError("runaway retries")
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, BaseException):
            raise step
        return NS(content=step)


def scripted(tmp_path, *steps):
    out = house_brain(tmp_path)
    out[0].client = Script(*steps)
    out[0].sleep = lambda s: None
    return (out[0], out[0].client, *out[2:])


LOOKS = {"list_timers", "list_alarms", "home_state", "home_devices", "car_status", "weather", "news", "web_search", "ask_mentat", "music", "audiobook", "list"}


def test_the_second_try_after_a_denial_never_cancels_clears_or_announces_on_its_own(tmp_path):
    """The review: 'are you able to cancel alarms', 'Clear the house chat' (a real utterance of 2026-10-31) and 'tell my wife I love her' were answered "I cannot ...", the nudge offered
    EVERY tool and the model's pick ran: both alarms gone (the weekday wake-up too), the shopping list emptied, an announcement in every room."""
    for i, (heard, said, call) in enumerate((("are you able to cancel alarms", "I cannot cancel alarms from here, Captain.", tool("cancel_alarm")),
                                              ("do you know how to cancel all my timers", "I cannot cancel timers.", tool("cancel_timers")),
                                              ("Clear the house chat", "I cannot clear chat history from here.", tool("list", action="clear")),
                                              ("clear my groceries", "I cannot clear that.", tool("list", action="remove", items=["milk"])),
                                              ("tell my wife I love her", "I cannot call your wife from here.", tool("announce", text="I love her", place="everywhere")))):
        b, c, ran, al, ls, timers = house_brain(tmp_path / f"case{i}", [text(said)], [call], [text("Done, all gone.")])
        assert b.reply(heard) == said, heard                                                  # nothing ran, so the first reply stands
        assert len(al.items) == 2 and ls.lists == {"shopping": ["milk", "eggs", "bread"]} and ran == [] and timers.remaining() == [("pasta", 600)] and b.actions == [], heard
        offered = {t["name"] for t in c.calls[1]["tools"]}
        assert not offered & {"cancel_alarm", "cancel_timers", "announce"}, (heard, offered)


def test_a_question_gets_a_second_try_that_can_only_look(tmp_path):
    b, c, ran, al, ls, timers = house_brain(tmp_path / "a", [text("I have no access to your timers, Captain.")], [tool("list_timers")], [text("Pasta, ten minutes left, monkey.")])
    assert b.reply("what timers do I have") == "Pasta, ten minutes left, monkey." and [a["tool"] for a in b.actions] == ["list_timers"]
    offered = {t["name"]: t for t in c.calls[1]["tools"]}
    assert set(offered) <= LOOKS and "set_timer" not in offered and "set_alarm" not in offered and "play_music" not in offered and "speakers" not in offered
    assert offered["list"]["input_schema"]["properties"]["action"]["enum"] == ["read", "lists"] and offered["music"]["input_schema"]["properties"]["action"]["enum"] == ["what"]
    assert "only asking" in str(c.calls[1]["messages"])                                       # the nudge says he may only be asking, and nothing may change
    b, c, ran, al, ls, timers = house_brain(tmp_path / "b", [text("I cannot do that, Captain.")], [tool("set_timer", seconds=5)], [tool("play_music", what="everything")], [text("Done.")])
    assert b.reply("are you able to set timers") == "I cannot do that, Captain." and timers.remaining() == [("pasta", 600)] and ran == []      # a write the model picks anyway is refused
    assert "Not done" in str(c.calls[2]["messages"]) and "Not done" in str(c.calls[3]["messages"])


def test_a_question_and_an_order_are_told_apart_for_the_second_try():
    import brain as brain_mod
    for heard in ("are you able to cancel alarms", "Skippy, do you know how to clear the list", "what timers do I have", "is the heat on", "how warm is it", "have you set the alarm",
                  "you can't set alarms, can you", "why can't you set alarms", "Hey Skippy, does the garage work", "um, is it raining"):
        assert brain_mod.NOT_AN_ORDER.search(heard), heard
    for heard in ("set a timer", "Clear the house chat", "tell my wife I love her", "can you set a timer for ten minutes", "could you turn off the kitchen light", "do you mind turning off the light",
                  "do it", "yes", "go ahead", "how about some jazz", "I want a five minute egg timer", "turn on the light", "wake me at seven"):
        assert not brain_mod.NOT_AN_ORDER.search(heard), heard


def test_an_order_the_model_wrongly_refused_is_done_by_the_second_try_with_a_menu_that_cannot_undo(tmp_path):
    b, c, ran, al, ls, timers = house_brain(tmp_path, [text("I cannot set timers from here.")], [tool("set_timer", seconds=300, label="eggs")], [text("Five minutes on the eggs.")])
    assert b.reply("I want a five minute egg timer") == "Five minutes on the eggs." and ("eggs", 300) in timers.remaining()
    offered = {t["name"]: t for t in c.calls[1]["tools"]}
    assert {"set_timer", "set_alarm", "list", "play_music", "music"} <= set(offered) and not set(offered) & {"cancel_alarm", "cancel_timers", "announce"}
    assert offered["list"]["input_schema"]["properties"]["action"]["enum"] == ["add", "read", "lists"]                                   # (the shared schema is not touched)
    from lists import TOOL as list_tool
    assert list_tool["input_schema"]["properties"]["action"]["enum"] == ["add", "remove", "read", "clear", "lists"] and "only asking" not in str(c.calls[1]["messages"])


def test_a_denial_of_a_command_that_reads_like_a_music_command_is_asked_again_all_the_same(tmp_path):
    """The review: musicguard files 'start a timer', 'put on the light', 'crank the heat', 'pause the timer' under the music commands, and its logging-only tier ('elif action') used to win
    over the denial: 26 of 123 logged commands never got their second try."""
    for i, heard in enumerate(("start a timer called pasta for ten minutes", "start the vacuum", "crank the heat up a bit", "pause the timer", "skip my alarm tomorrow", "play Pink Floyd upstairs",
                               "put on something sad")):
        b, c, *_ = house_brain(tmp_path / f"case{i}", [text("I cannot do that from here, Captain.")], [text("Still no.")])
        assert b.reply(heard) == "I cannot do that from here, Captain." and len(c.calls) == 2 and b.guard == ("reask", "denial"), heard
    b, c, ran, al, ls, timers = house_brain(tmp_path / "go", [text("I cannot start timers from here.")], [tool("set_timer", seconds=600, label="pasta noodles")], [text("Ten minutes, on the clock.")])
    assert b.reply("start a timer called pasta noodles for ten minutes") == "Ten minutes, on the clock." and ("pasta noodles", 600) in timers.remaining()


def test_a_denial_that_names_an_alarm_or_a_list_is_a_denial_not_a_claim_of_having_set_one(tmp_path):
    """'I cannot set an alarm from here' matched CLAIMED ('set an alarm'): the wrong nudge ('you said that, but NOTHING was done'), and a model that then said anything without a negation
    made him hear 'That did not get done, whatever I just claimed': a claim nobody made."""
    for i, (heard, said) in enumerate((("can you set an alarm for seven in the morning", "I cannot set an alarm from here, Captain."),
                                       ("can you set a timer on the microwave", "I cannot set a timer on the microwave."),
                                       ("i want eggs added to my list", "I cannot put anything on your shopping list."))):
        b, c, *_ = house_brain(tmp_path / f"case{i}", [text(said)], [text("That is beyond me, monkey.")])
        assert b.reply(heard) == said and b.guard == ("reask", "denial"), heard                  # no tool ran: the first reply stands
        assert "you DO have these" in str(c.calls[1]["messages"]) and "NOTHING was done" not in str(c.calls[1]["messages"]), heard
    b, c, *_ = house_brain(tmp_path / "mixed", [text("I cannot reach the speakers. Alarm set for seven.")], [text("It is not, I made that up.")])
    assert b.reply("wake me at seven") == "It is not, I made that up." and "NOTHING was done" in str(c.calls[1]["messages"])         # a real claim in another sentence is still caught
    b, c, *_ = house_brain(tmp_path / "mixed2", [text("I cannot reach the speakers, but alarm set for seven.")], [text("It is not, I made that up.")])
    b.reply("wake me at seven")
    assert "NOTHING was done" in str(c.calls[1]["messages"])


def test_a_second_try_that_cannot_get_an_answer_gives_up_quickly_and_the_first_reply_stands(tmp_path):
    """The first reply was a fine denial; the optional second call hit a model that was away: it used to wait out the 120 s of the first call with the room deaf, and any failure that was not
    'the model dropped' escaped reply() so the hub said 'My brain is not answering' instead of the good first answer."""
    b, c, *_ = scripted(tmp_path / "a", [text("I cannot set that, Captain.")], ConnectionError("skippy-core is away"))
    assert b.reply("I want a five minute timer") == "I cannot set that, Captain." and len(c.calls) <= 8
    b, c, *_ = scripted(tmp_path / "b", [text("I cannot do that, Captain.")], ValueError("400 bad request"))
    assert b.reply("I want a five minute timer") == "I cannot do that, Captain."
    b, c, *_ = scripted(tmp_path / "c", [text("I cannot do that, Captain.")], [])                 # empty replies, for ever
    assert b.reply("I want a five minute timer") == "I cannot do that, Captain." and len(c.calls) <= 8


def test_what_the_second_try_did_is_never_hidden_behind_the_denial_and_odd_tool_input_never_raises(tmp_path):
    b, c, *_ = scripted(tmp_path / "a", [text("I have no access to your timers.")], [NS(type="tool_use", id="t1", name="list_timers", input="{}")], [text("Nothing but pasta, monkey.")])
    assert b.reply("what timers do I have") == "Nothing but pasta, monkey."                    # a JSON string as the input: run_tool always tolerated it, the second try raised ValueError
    b, c, *_ = scripted(tmp_path / "b", [text("I have no access to your timers.")], [tool("list_timers")], [])                 # the tool ran, then the model dropped out
    assert b.reply("what timers do I have") == "pasta: 10 minutes left"
    b, c, *_ = scripted(tmp_path / "c", [text("I have no access to your timers.")], [tool("list_timers")])                      # three rounds of tools and never a word
    assert b.reply("what timers do I have") == "pasta: 10 minutes left" and len(b.actions) == 3


def test_the_denial_pattern_knows_how_the_model_really_says_it_and_leaves_figures_of_speech_alone():
    import brain as brain_mod
    for said in ("I can’t see the thermostat, Captain.", "I don’t have access to your car.", "I’m unable to reach the car.", "This voice cannot do that yet, but the portal can.",
                 "I can't help with that.", "There is no tool for that.", "I have no tools for that.", "I have no control over the lights.", "That is outside what I can do from here.",
                 "I lack access to your car.", "I could not do that from here.", "I cannot set alarms.", "I am not able to do it.", "no such tool"):
        assert brain_mod.denies(said), said
    for said in ("I cannot believe it is already November, monkey.", "I can't help but laugh.", "I can't help myself.", "I can not only set timers but also lists.", "I can't say I'm surprised.",
                 "I cannot imagine why.", "I cannot be bothered.", "I cannot waste my time on that.", "I cannot wait.", "I cannot stress enough how little I care."):
        assert not brain_mod.denies(said), said


def test_a_description_of_what_it_can_and_cannot_do_is_no_denial_to_be_asked_again(tmp_path):
    b, c, *_ = house_brain(tmp_path, [text("Timers, alarms, lists, the house and Bilby. What I cannot do is touch his files or code from here.")])
    assert b.reply("what can you do for me").startswith("Timers, alarms") and len(c.calls) == 1 and b.guard != ("reask", "denial")
    b, c, *_ = house_brain(tmp_path / "curly", [text("I can’t see the thermostat, Captain.")], [text("Still no.")])
    assert b.reply("is the heat on") == "I can’t see the thermostat, Captain." and len(c.calls) == 2 and b.guard == ("reask", "denial")          # (the curly apostrophe used to hide it)


def test_a_call_the_model_writes_as_text_in_the_second_try_runs_through_the_same_gate(tmp_path):
    b, c, ran, al, ls, timers = house_brain(tmp_path / "a", [text("I have no access to your timers, Captain.")], [text("[list_timers]")], [text("Pasta, ten minutes, monkey.")])
    assert b.reply("what timers do I have") == "Pasta, ten minutes, monkey." and [a["tool"] for a in b.actions] == ["list_timers"]
    b, c, ran, al, ls, timers = house_brain(tmp_path / "b", [text("I cannot do that, Captain.")], [text('[cancel_alarm what=""]')], [text("Gone.")])
    assert b.reply("are you able to cancel alarms") == "I cannot do that, Captain." and len(al.items) == 2 and not b.actions        # the same gate as a real call
    b, c, ran, al, ls, timers = house_brain(tmp_path / "c", [text("I cannot do that, Captain.")], [text("[set_timer seconds=60 label=tea confirm=true]")], [text("A minute.")])
    assert b.reply("I want a one minute timer") == "A minute." and b.actions[-1]["tool"] == "set_timer" and "confirm" not in b.actions[-1]["args"]    # and never with his yes in its mouth


def test_the_house_prompt_does_not_teach_the_denial_the_persona_files_own_examples_did():
    """The persona's example answers ('I cannot set timers directly from here, but Android's clock app is right there', 'I don't have live weather data') are what the house voice copied."""
    import brain as brain_mod
    system = Brain(None, "m", None).system()
    assert "I cannot set timers directly from here" not in system and "live weather data" not in system and "clock app" not in system
    assert "What time is it?" in system and "Who are you?" in system and "can you hear me?" in system          # the other examples stay
    assert "I cannot set timers directly from here" in brain_mod.PERSONA_FILE.read_text(encoding="utf-8")      # flask-api's phone chat keeps its own


def test_a_reply_times_itself_for_the_hub_to_log(tmp_path):
    import time
    b, c, ran, al, ls, timers = house_brain(tmp_path / "a", [tool("set_timer", seconds=60, label="tea")], [text("A minute.")])
    real = c.create

    def slow(**kw):
        time.sleep(0.03)
        return real(**kw)
    c.create = slow
    assert b.timing == {"route": None, "model_s": [], "tools_s": 0.0, "total_s": 0.0}          # before any reply
    b.reply("I want a one minute tea timer")
    t = b.timing
    assert t["route"] is None and len(t["model_s"]) == 2 and all(s >= 0.025 for s in t["model_s"]) and 0.0 <= t["tools_s"] < 0.5
    assert t["total_s"] >= sum(t["model_s"]) + t["tools_s"] - 0.001
    real_run = b.run_extra
    b.run_extra = lambda name, args: (time.sleep(0.03), real_run(name, args))[1]
    c.rounds.append([text("Quiet.")])
    b.reply("stop the music")                                                                  # a pre-routed command: the tool name, one model call, and the counters start again
    t = b.timing
    assert t["route"] == "music" and len(t["model_s"]) == 1 and t["model_s"][0] >= 0.025 and t["tools_s"] >= 0.025
    c.rounds.append([text("Hi.")])
    b.reply("hello there")
    assert b.timing["route"] is None and len(b.timing["model_s"]) == 1 and b.timing["tools_s"] == 0.0
    b.timing = None                                                                            # whatever the hub did to it, timing never raises into a reply or a tool
    assert "pasta: 10 minutes left" in b.run_tool("list_timers", {})
    c.rounds.append([text("Still here.")])
    assert b.reply("anyone there") == "Still here." and b.timing["total_s"] > 0.0
