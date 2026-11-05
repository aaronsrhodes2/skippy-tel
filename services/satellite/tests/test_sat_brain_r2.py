"""Round 2 of the review of the house hub's brain and music guard (2026-11-05): what the round-1 fixes (10e0486) left open. Every test here fails on that commit, except the two controls
that pin what must not change (a real question about abilities still gets no second try; a stop that a routine undid is still told true).

1. brain.ASKS_ABILITY matched ANY "what do you / what are you ...", so "what do you know about my car" (and "what do you see in the car", "what are you seeing outside") got no second try
   after "I do not have access to your car's systems": the car incident of 2026-11-04 again, for the phrasings no pre-route claims. Only real questions about abilities skip it now.
2. A request worded as a question ("how about some jazz upstairs?", "can I get some metal on the Enclave?", "do you mind pausing it?") is no question about the house since round 1, but
   musicguard.asked_music() still read it as "not asked": a false claim of it was RETRACTED ("I have not played, moved or changed any music.") and nothing played. It is asked again.
3. "DJ mode off" and "shuffle off" went to the model (the dj_mode tool) but "turn off dj mode", "turn dj mode off" and "stop the dj" PAUSED the music.
4. A true answer about the past ("Did you stop the music?" - "Yes, I paused the music at 7:31.") was excused by ANY music record of the last three hours, so with only a play_music in the
   log a lie about a pause stood. The claimed verb has to be in the log: a pause needs a pause (or a stop, or a routine that left the music off), a play needs a play.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brain as brain_mod  # noqa: E402
import car as car_mod  # noqa: E402
import houseaudio  # noqa: E402
import housemem  # noqa: E402
import musicguard as g  # noqa: E402
from brain import music_route, stop_route  # noqa: E402
from test_sat_brain import house_brain, make, music_brain, text, tool  # noqa: E402

PAUSE = {"action": "pause"}
RETRACTED = "I have not played, moved or changed any music."


# ── 1. only a real question about abilities skips the second try after a denial ───────────────────────────────────────────────────────────────────────────────────────────────
ABILITY_QUESTIONS = ["what can you do", "what can you do for me", "what else can you do", "what could you do", "what can't you do", "what can you not do", "what can you help with",
                     "what can you help me with", "what are you able to do", "what are you capable of", "what do you do", "what do you do around here", "what are your abilities",
                     "what are your capabilities", "what are your tools", "what are your limits", "list your abilities", "tell me what you can do", "Skippy, what can you do?",
                     "so what else can you do for me", "what are you good at", "what do you know how to do", "what can you control", "what can you handle"]
NOT_ABILITY_QUESTIONS = ["what do you know about my car", "what do you see in the car", "what are you seeing outside", "what do you hear from the garage", "what do you think of the weather",
                         "what are you doing", "what do you want", "what can you tell me about my car", "what can you see in the car", "what is my car's charge level right now",
                         "how is my car doing", "what do you make of the news"]


def test_sat_brain_r2_only_real_questions_about_abilities_skip_the_second_try():
    for heard in ABILITY_QUESTIONS:
        assert brain_mod.ASKS_ABILITY.search(heard), heard
    for heard in NOT_ABILITY_QUESTIONS:
        assert not brain_mod.ASKS_ABILITY.search(heard), heard


def test_sat_brain_r2_a_denial_to_what_do_you_know_about_my_car_gets_its_second_try_with_the_car_tool(tmp_path):
    """The reviewer's run: with the model replying "I do not have access to your car's systems" there was 1 model call, guard (None, None), and the denial stood."""
    for i, heard in enumerate(("what do you know about my car", "what do you see in the car", "what are you seeing outside")):
        b, c, *_ = house_brain(tmp_path / f"q{i}", [text("I do not have access to your car's systems, Captain.")], [tool("car_status", about="charge")], [text("Eighty percent, plugged in, at home.")])
        b.car = NS(tools=lambda: [car_mod.TOOL], run=lambda args: "Battery 80 percent, plugged in, at home.")
        assert b.reply(heard) == "Eighty percent, plugged in, at home.", heard
        assert len(c.calls) == 3 and b.guard == ("reask", "denial") and [a["tool"] for a in b.actions] == ["car_status"], (heard, len(c.calls), b.guard)
    b, c, *_ = house_brain(tmp_path / "plain", [text("I do not have access to your car's systems, Captain.")], [text("Still no.")])
    assert b.reply("what are you seeing outside") == "I do not have access to your car's systems, Captain." and len(c.calls) == 2 and b.guard == ("reask", "denial")     # (nothing ran: the first reply stands)


def test_sat_brain_r2_a_real_question_about_abilities_still_gets_no_second_try(tmp_path):
    said = "Timers, alarms, lists, the house and Bilby. What I cannot do is touch his files or code from here."
    for i, heard in enumerate(("what can you do for me", "what else can you do", "what are you able to do", "what are your limits", "what do you do")):
        b, c, *_ = house_brain(tmp_path / f"a{i}", [text(said)])
        assert b.reply(heard).startswith("Timers, alarms") and len(c.calls) == 1 and b.guard != ("reask", "denial"), heard


# ── 2. a request worded as a question is asked again, not retracted ──────────────────────────────────────────────────────────────────────────────────────────────────────────
REQUESTS = [("how about some jazz upstairs?", "Jazz is now playing upstairs."), ("can I get some metal on the Enclave?", "Heavy metal is playing on the Enclave."),
            ("what about some jazz?", "Jazz is now playing."), ("do you mind pausing it?", "Music paused."), ("could I have it louder?", "Volume is now at eight."),
            ("what about the Sony?", "The music is now on the Sony."), ("is it possible to get DJ mode on?", "DJ mode is on."), ("why not put on some jazz?", "Jazz is playing now."),
            ("when you get a chance, play some jazz", "Jazz is now playing upstairs."), ("would you mind turning it down?", "Volume is now at three."),
            ("are you able to put the music on the Sony?", "The music is now on the Sony."), ("Skippy, how about some jazz upstairs?", "Jazz is now playing upstairs.")]
NOT_REQUESTS = ["what music is playing?", "what is the volume", "why is the Enclave playing heavy metal?", "how loud is it", "is anything on", "who started the music", "where is the music playing",
                "did you stop the music?", "have you been changing the volume?", "is it ok to play music at night", "what time is it", "would you play jazz if I asked",
                "can I get some metal if she leaves?", "do you like playing heavy metal?"]


def test_sat_brain_r2_a_false_claim_to_a_request_worded_as_a_question_is_asked_again_and_not_retracted():
    for heard, said in REQUESTS:
        assert g.asked_music(heard), heard
        assert g.classify(heard, "", said, []) == ("reask", "claim"), (heard, said, g.classify(heard, "", said, []))
    for heard in NOT_REQUESTS:
        assert not g.asked_music(heard), heard                                                  # a plain question about the house is still no ask: a claim to it is cut
    assert g.classify("what time is it", "", "It is nine o'clock. Also, I started some music for the maid.", []) == ("retract", "claim")
    assert g.classify("Did you stop the music?", "", "Yes. Playing jazz now.", []) == ("retract", "claim")
    assert g.classify("how loud is it", "", "Loud. I raised the volume to nine just now.", []) == ("retract", "claim")


def test_sat_brain_r2_the_brain_asks_again_for_how_about_some_jazz_upstairs_and_the_second_try_plays_it():
    b, c, ran = music_brain([text("Jazz is now playing upstairs.")], [tool("play_music", genre="jazz", speaker="upstairs")], [text("Jazz, upstairs, on the Enclave.")])
    said = b.reply("how about some jazz upstairs?")
    assert said == "Jazz, upstairs, on the Enclave." and ran == [("play_music", {"genre": "jazz", "speaker": "upstairs"})] and b.guard == ("reask", "claim")
    assert "you called no music tool" in str(c.calls[1]["messages"]) and "how about some jazz upstairs" in str(c.calls[1]["messages"])      # (his words quoted)
    b, c, ran = music_brain([text("Music paused.")], [tool("music", action="pause")], [text("Paused, monkey.")])
    assert b.reply("do you mind pausing it?") == "Paused, monkey." and ran == [("music", PAUSE)]
    b, c, ran = music_brain([text("It is nine o'clock. Also, I started some music for the maid.")])
    assert b.reply("what time is it") == "It is nine o'clock. " + RETRACTED and len(c.calls) == 1 and b.guard == ("retract", "claim")              # not a request: still cut, no second call


# ── 3. the verb forms of DJ mode and shuffle ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
DJ_SWITCHES = ["turn off dj mode", "turn dj mode off", "switch off dj mode", "switch dj mode off", "shut off dj mode", "shut dj mode off", "shut down dj mode", "power off dj mode",
               "turn off the dj", "turn the dj off", "stop dj mode", "stop the dj", "stop the dj mode", "pause the dj", "kill dj mode", "cut dj mode", "end dj mode", "halt the dj",
               "no more dj", "no more dj mode", "enough dj", "enough of the dj mode", "make the dj stop", "i want the dj to stop", "i want dj mode to be off",
               "Bilby, turn off DJ mode", "Skippy, please turn off DJ mode", "can you turn off dj mode", "turn off dj mode please", "stop playing dj mode",
               "dj mode off", "dj off", "Bilby, DJ mode off"]                                  # (the last three: the verbless forms round 1 sent to the model already)
SHUFFLE_SWITCHES = ["turn off shuffle", "turn shuffle off", "turn off the shuffle", "switch off shuffle", "switch the shuffle off", "shut off shuffle", "shut down the shuffle",
                    "no more shuffle", "enough shuffle", "enough of the shuffle", "shuffle off"]
STILL_STOPS = ["stop the music", "turn off the music", "turn the music off", "stop", "stop playing", "stop all the music", "pause the music", "no more music", "enough music",
               "make the music stop", "i want the music to stop", "shut down the music", "music off", "turn off the radio", "stop bilby", "kill the music", "stop the shuffle",
               "pause the shuffle", "stop shuffle", "stop everything"]


def test_sat_brain_r2_turning_off_dj_mode_in_any_verb_form_is_for_the_dj_mode_tool_never_a_pause():
    for heard in DJ_SWITCHES:
        for playing in (None, False, True):
            assert music_route(heard, playing) is None and stop_route(heard, playing) is None, (heard, playing, music_route(heard, playing))


def test_sat_brain_r2_turning_off_shuffle_is_the_modes_switch_like_shuffle_off_but_stopping_the_shuffle_still_stops_what_plays():
    for heard in SHUFFLE_SWITCHES:
        for playing in (None, False, True):
            assert music_route(heard, playing) is None, (heard, playing, music_route(heard, playing))
    for heard in STILL_STOPS:
        for playing in (None, False, True):
            if heard == "stop" and playing is not True:
                continue                                                                     # a bare stop is a stop only while the house plays
            assert music_route(heard, playing) == PAUSE, (heard, playing, music_route(heard, playing))


def test_sat_brain_r2_the_brain_hands_turn_off_dj_mode_to_the_model_with_its_tools_and_runs_the_dj_tool_not_a_pause():
    tools = [{"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}} for n in ("play_music", "music", "speakers", "dj_mode", "audiobook")]
    for heard in ("turn off dj mode", "stop the dj", "turn off shuffle"):
        b, c, *_ = make([tool("dj_mode", on=False)], [text("DJ mode is off. The music plays on.")])
        ran = []
        b.extra_tools, b.run_extra = tools, lambda name, args: ran.append((name, args)) or f"({name} done)"
        b.audio = NS(playing=lambda: True, status=lambda: "")
        assert b.reply(heard) == "DJ mode is off. The music plays on." and ran == [("dj_mode", {"on": False})], (heard, ran)
        assert "tools" in c.calls[0] and {t["name"] for t in c.calls[0]["tools"]} >= {"dj_mode", "music"} and b.timing["route"] is None, heard
    b, c, *_ = make([text("Silence.")])
    ran = []
    b.extra_tools, b.run_extra = tools, lambda name, args: ran.append((name, args)) or f"({name} done)"
    assert b.reply("stop the music") == "Silence." and ran == [("music", PAUSE)]            # and a stop is a stop


# ── 4. a true answer about the past is checked against what the log shows was DONE ─────────────────────────────────────────────────────────────────────────────────────────────
def house_log(tmp_path):
    now = [time.mktime((2026, 11, 5, 7, 10, 0, 0, 0, -1))]
    return housemem.HouseLog(tmp_path, clock=lambda: now[0]), now


def turn(log, words, *calls, outcome="ok"):
    """What the hub writes for a turn (hub.py's note()): the command with the tools it called, and one action record for each lights / timer / music tool."""
    calls = [{"tool": name, "args": args, "outcome": "done"} for name, args in calls]
    log.event("command", room="living", words=words, tools=[c["tool"] for c in calls], outcome=outcome, reply="Done.")
    log.actions("living", calls)


LOGS = {
    "play": lambda log: turn(log, "play some jazz upstairs", ("play_music", {"genre": "jazz", "speaker": "upstairs"})),
    "pause": lambda log: turn(log, "stop the music", ("music", {"action": "pause"})),
    "resume": lambda log: turn(log, "resume the music", ("music", {"action": "resume"})),
    "louder": lambda log: turn(log, "turn it up", ("music", {"action": "louder"})),
    "quieter": lambda log: turn(log, "turn it down", ("music", {"action": "quieter"})),
    "next": lambda log: turn(log, "next song", ("music", {"action": "next"})),
    "what": lambda log: turn(log, "what's playing", ("music", {"action": "what"})),
    "speakers": lambda log: turn(log, "play it on the sony too", ("speakers", {"what": "music", "device": "the sony", "on": True})),
    "dj": lambda log: turn(log, "dj mode on", ("dj_mode", {"on": True})),
    "spotify": lambda log: turn(log, "play my liked songs on spotify", ("spotify", {"action": "play", "what": "liked songs"})),
    "routine_started": lambda log: log.event("routine", room="living", routine="homecoming", outcome="started the music"),
    "routine_left_off": lambda log: log.event("routine", room="living", routine="homecoming", outcome="left the music off: he said stop"),
    "hub_stop": lambda log: log.event("command", room="living", words="that's enough", tools=["music"], outcome="sign_off"),
    "lights": lambda log: turn(log, "turn on the kitchen light", ("home_control", {"name": "kitchen", "on": True})),
    "chat": lambda log: log.event("command", room="living", words="what time is it", tools=[], outcome="ok"),
}
# what he asked, what a model that never touched the music says in the past tense, and the one thing it claims was done
CLAIMS = {
    "stop": ("Did you stop the music?", "Yes, I paused the music at 7:31."),
    "start": ("Did you start the music?", "Yes, I started the library at 7:10."),
    "volume": ("Did you change the volume?", "Yes, I lowered the volume at 7:35."),
    "skip": ("Did you skip that song?", "Yes, I skipped to the next song at 7:12."),
    "move": ("Did you move the music?", "Yes, I moved the music to the Sony at 7:20."),
    "dj": ("Did you change DJ mode?", "Yes, I turned DJ mode on at 7:12."),
}
# which kinds of claim each record excuses (a speaker's switch is a move, a start or a stop: the record does not say which)
SHOWS = {"play": {"start", "move"}, "pause": {"stop"}, "resume": {"start"}, "louder": {"volume"}, "quieter": {"volume"}, "next": {"skip"}, "what": set(), "speakers": {"move", "start", "stop"},
         "dj": {"dj"}, "spotify": {"start"}, "routine_started": {"start"}, "routine_left_off": {"stop"}, "hub_stop": {"stop"}, "lights": set(), "chat": set()}


def audio_over(tmp_path, name):
    log, now = house_log(tmp_path / name)
    if name in LOGS:
        LOGS[name](log)
    now[0] += 25 * 60
    return houseaudio.Audio(None, hlog=log)


def test_sat_brain_r2_a_pause_claimed_with_only_a_play_in_the_log_is_not_excused_but_cut(tmp_path):
    """The reviewer's run: the log shows only a play_music at 7:10, he asks at 7:35 'did you stop the music?' and a model that never stopped it says 'Yes, I paused the music at 7:31.'"""
    log, now = house_log(tmp_path / "only_a_play")
    turn(log, "play some jazz", ("play_music", {"genre": "jazz"}))
    now[0] += 25 * 60
    b, c, ran = music_brain([text("Yes, I paused the music at 7:31.")])
    b.audio = houseaudio.Audio(None, hlog=log)
    assert b.reply("Did you stop the music?") == RETRACTED and b.guard == ("retract", "claim") and not ran and len(c.calls) == 1
    log, now = house_log(tmp_path / "a_pause_too")                                                     # the same answer is TRUE when the log shows a pause
    turn(log, "play some jazz", ("play_music", {"genre": "jazz"}))
    now[0] += 20 * 60
    turn(log, "stop the music", ("music", {"action": "pause"}))
    now[0] += 5 * 60
    b, c, ran = music_brain([text("Yes, I paused the music at 7:31.")])
    b.audio = houseaudio.Audio(None, hlog=log)
    assert b.reply("Did you stop the music?") == "Yes, I paused the music at 7:31." and b.guard == (None, "history") and not ran and len(c.calls) == 1
    log, now = house_log(tmp_path / "a_stop_only")                                                     # and "Did you start the music?" - "Yes, I started it" needs a start
    turn(log, "stop the music", ("music", {"action": "pause"}))
    now[0] += 25 * 60
    b, c, ran = music_brain([text("Yes, I started the library at 7:10.")])
    b.audio = houseaudio.Audio(None, hlog=log)
    assert b.reply("Did you start the music?") == RETRACTED and b.guard == ("retract", "claim")


def test_sat_brain_r2_each_claim_is_excused_only_by_a_log_that_shows_that_kind_of_thing(tmp_path):
    for claim, (heard, said) in CLAIMS.items():
        assert g.describing(heard) and g.history(heard), heard
        for name in [*LOGS, "empty"]:
            audio = audio_over(tmp_path / claim, name)
            got = g.classify(heard, "", said, [], happened=audio.music_lately)
            want = (None, "history") if claim in SHOWS.get(name, set()) else ("retract", "claim")
            assert got == want, (claim, name, got, want)


def test_sat_brain_r2_the_claim_kinds_are_read_from_the_verbs_of_the_claim_itself():
    for claim, (heard, said) in CLAIMS.items():
        assert g.claim_kinds(said) == frozenset({claim}), (claim, said, g.claim_kinds(said))
    assert g.claim_kinds("Yes, I paused the music at 7:31 and lowered the volume at 7:35.") == frozenset({"stop", "volume"})                       # a second verb joined by "and"
    assert g.claim_kinds("Yes, I paused the music at 7:31, then lowered the volume at 7:35.") == frozenset({"stop", "volume"})
    # ... but only the claim's own verbs: the homecoming routine's "shuffled", and the maid's "started", are not what Skippy claims to have done
    assert g.claim_kinds("You told me to stop Bilby at 7:31 and I paused the music, but the homecoming routine shuffled the library again at 7:33.") == frozenset({"stop"})
    assert g.claim_kinds("I paused the music, and the maid started a movie in the theater.") == frozenset({"stop"})
    assert g.claim_kinds("Yes, I turned off DJ mode at 7:12.") == frozenset({"dj"}) and g.claim_kinds("Yes, I paused the music and turned DJ mode off.") == frozenset({"stop", "dj"})
    assert g.claim_kinds("Yes, I stopped DJ mode at 7:12.") == frozenset({"dj"})                                                                     # (DJ mode has a tool of its own)
    assert g.claim_kinds("Stopped the music, as asked.") == frozenset({"stop"}) and g.claim_kinds("I put on some Pink Floyd, because the music was off.") == frozenset({"start"})
    assert g.claim_kinds("It is nine o'clock.") == frozenset() and g.claim_kinds("") == frozenset() and g.claim_kinds(None) == frozenset()
    assert g.claim_kinds("Yes, I set the stereo to seven.") == frozenset({"volume"}) and g.claim_kinds("Yes, I set the music to six.") == frozenset({"volume"})
    assert g.claim_kinds("Yes, I enabled the stereo.") == frozenset()                          # a verb it cannot read: the old, coarse check decides


STOP_WORDINGS = ["Yes, I paused the music at 7:31.", "Yes, I stopped the music at 7:31.", "Yes, I turned the music off at 7:31.", "Yes, I turned off the music at 7:31.",
                 "I've paused the music.", "Yes. I have stopped the stereo.", "Stopped the music, as you asked.", "Paused the music at 7:31, monkey.", "Yes, I switched the music off at 7:31.",
                 "Yes, I switched off the stereo at 7:31."]
START_WORDINGS = ["Yes, I started the library at 7:10.", "Yes, I played some jazz on the Sony at 7:10.", "Yes, I put on the music at 7:10.", "Yes, I shuffled your library at 7:10.",
                  "Yes, I resumed the music at 7:10.", "Queued up your library at 7:10.", "Yes, I turned the music on at 7:10.", "Yes, I switched the stereo on at 7:10."]
VOLUME_WORDINGS = ["Yes, I lowered the volume at 7:35.", "Yes, I turned the volume down at 7:35.", "Yes, I turned the music down at 7:35.", "Yes, I raised the volume at 7:35.",
                   "Yes, I cranked the volume at 7:35.", "Yes, I turned down the volume at 7:35.", "Yes, I set the volume to six at 7:35.", "Yes, I turned the music up at 7:35."]


def test_sat_brain_r2_the_other_ways_of_claiming_the_same_thing_need_the_same_log(tmp_path):
    """'turned the music off' keeps its particle after what the claim pattern matched, 'turned it down' is the volume and not a stop, 'put on' and 'queued up' are plays."""
    asks = {"stop": "Did you stop the music?", "start": "Did you start the music?", "volume": "Did you change the volume?"}
    wordings = {"stop": STOP_WORDINGS, "start": START_WORDINGS, "volume": VOLUME_WORDINGS}
    audios = {name: audio_over(tmp_path, name) for name in ("play", "pause", "quieter", "louder", "what")}
    shown = {"play": {"start", "move"}, "pause": {"stop"}, "quieter": {"volume"}, "louder": {"volume"}, "what": set()}
    for kind, said_list in wordings.items():
        for said in said_list:
            assert g.describing(asks[kind]) and g.history(asks[kind]) and g.claim_kinds(said) == frozenset({kind}), (kind, said, g.claim_kinds(said))
            for name, audio in audios.items():
                got = g.classify(asks[kind], "", said, [], happened=audio.music_lately)
                assert got == ((None, "history") if kind in shown[name] else ("retract", "claim")), (kind, said, name, got)


def test_sat_brain_r2_a_claim_with_every_kind_backed_is_excused_and_one_unbacked_kind_cuts_it(tmp_path):
    heard, said = "What happened to the music?", "I paused the music at 7:31 and lowered the volume at 7:35."
    log, now = house_log(tmp_path / "both")
    turn(log, "stop the music", ("music", {"action": "pause"}))
    now[0] += 60
    assert g.classify(heard, "", said, [], happened=houseaudio.Audio(None, hlog=log).music_lately) == ("retract", "claim")           # the pause is there, the turning down is not
    turn(log, "quieter please", ("music", {"action": "quieter"}))
    assert g.classify(heard, "", said, [], happened=houseaudio.Audio(None, hlog=log).music_lately) == (None, "history")


def test_sat_brain_r2_the_real_story_of_the_stop_and_the_routine_that_started_it_again_is_excused_by_its_log(tmp_path):
    heard = "Why did the music start again?"
    said = "You told me to stop Bilby at 7:31 and I paused the music, but the homecoming routine shuffled the library again at 7:33."
    log, now = house_log(tmp_path)
    turn(log, "stop bilby", ("music", {"action": "pause"}))
    now[0] += 120
    log.event("routine", room="living", routine="homecoming", outcome="started the music")
    now[0] += 600
    assert g.classify(heard, "", said, [], happened=houseaudio.Audio(None, hlog=log).music_lately) == (None, "history")


def test_sat_brain_r2_the_old_call_shapes_keep_working(tmp_path):
    heard, said = "Did you stop the music?", "Yes, I paused the music at 7:31."
    assert g.classify(heard, "", said, [], happened=lambda: True) == (None, "history")                      # a check that takes no argument is asked as before
    assert g.classify(heard, "", said, [], happened=lambda: False) == ("retract", "claim")
    assert g.classify(heard, "", said, [], happened=lambda: 1 / 0) == ("retract", "claim")                  # ... and one that raises excuses nothing
    assert g.classify(heard, "", said, [], happened=lambda kinds: 1 / 0) == ("retract", "claim")
    assert g.classify(heard, "", said, [], happened=None) == ("retract", "claim")
    seen = []
    assert g.classify(heard, "", said, [], happened=lambda kinds: seen.append(kinds) or True) == (None, "history") and seen == [frozenset({"stop"})]
    seen.clear()
    assert g.classify(heard, "", "Yes, I enabled the stereo.", [], happened=lambda kinds=None: seen.append(kinds) or True) == (None, "history") and seen == [None]      # no kind read: none named
    audio = audio_over(tmp_path, "play")
    assert audio.music_lately() is True and audio.music_lately(None) is True and audio.music_lately(()) is True               # no kind named: any music record, as before
    assert audio.music_lately({"start"}) is True and audio.music_lately({"stop"}) is False and audio.music_lately({"start", "stop"}) is False
    assert houseaudio.Audio(None).music_lately({"stop"}) is False and houseaudio.Audio(None, hlog=NS(recent=lambda **kw: 1 / 0)).music_lately({"stop"}) is False


def test_sat_brain_r2_what_a_record_shows_was_done_to_the_music(tmp_path):
    for name, want in SHOWS.items():
        log, now = house_log(tmp_path / name)
        LOGS[name](log)
        shown = set()
        for r in log.recent(limit=50):
            shown |= houseaudio.record_kinds(r)
        assert shown == want, (name, shown, want)
    assert houseaudio.record_kinds({}) == set() and houseaudio.record_kinds({"kind": "command", "tools": "music", "words": "stop"}) == set()
    assert houseaudio.record_kinds({"kind": "command", "tools": ["music"], "words": "stop the music"}) == {"stop"}               # a command alone: his words say which
    assert houseaudio.record_kinds({"kind": "command", "tools": ["music"], "words": "turn it down a bit"}) == {"volume"}
    assert houseaudio.record_kinds({"kind": "command", "tools": ["music"], "words": "garbled"}) == set()
    assert houseaudio.record_kinds({"kind": "action", "tool": "music", "act": "tired"}) == {"skip"}
    assert houseaudio.record_kinds({"kind": "routine", "outcome": "done"}) == set() and houseaudio.KINDS == g.CLAIM_KINDS               # (a test keeps the two alike)


def test_sat_brain_r2_the_brain_asks_the_log_for_the_kind_it_was_claimed(tmp_path):
    """The brain's own check takes the kinds (an Audio from the older tests, with a music_lately() of no argument, proves nothing for a kind: a log it cannot ask excuses nothing)."""
    log, now = house_log(tmp_path)
    turn(log, "play some jazz", ("play_music", {"genre": "jazz"}))
    now[0] += 25 * 60
    b, c, ran = music_brain([text("Yes, I paused the music at 7:31.")])
    b.audio = houseaudio.Audio(None, hlog=log)
    assert b._music_happened() is True and b._music_happened({"start"}) is True and b._music_happened({"stop"}) is False
    b.audio = NS(music_lately=lambda: True)
    assert b._music_happened() is True and b._music_happened({"stop"}) is False
    b.audio = None
    assert b._music_happened() is False and b._music_happened({"stop"}) is False
