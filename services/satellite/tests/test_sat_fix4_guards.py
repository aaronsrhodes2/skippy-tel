"""fix4:guards: the guards of the house brain, after the fourth pass (2026-11-06): what the THIRD review (round 3's reviewers) still listed.

  code3#1  the second try after a tool call written as text that the house cannot read (TEXT_AGAIN) was the only one left with no gate: '[cancel_alarm] Noted.' and then a call of cancel_alarm cancelled both alarms.
           It is behind the gate of the other three now (retry_may; the model's own `confirm` dropped; a question may only look)
  core3#2  a tool call written in Core's OTHER dialects (a code fence, '[call: ...]', '[Calling ...]', '[tool_call ...]') was SPOKEN, in every path: taken out of the words (strip_call_dialects) where this part owns them
  core3#3  a look, a cancel that was refused, and then 'I've cancelled it': a read that ran made the second try count as done. The words must own it when the model's last word came right after being told no
  core3#4  'Right, my mistake. Let me cancel that timer for you.' counted as taking the claim back (an apology is no retraction): a reply that still promises or claims an action is not honest (announces)
  core3#5  says_yes: the fillers Whisper writes (uh, um, er, erm, hmm, well, oh) count; 'yeah right', 'yeah, sure', 'sure, sure' do not (usually sarcasm: when in doubt the house asks again)
  core3#6  a stage direction that opens with the name of a tool that is an English word ('[music swells]', '[news anchor voice]') is wiped, not a call that was meant (one model call, and the right answer replaced)
  code3#3  the survivors of the third review's mutation run that are real behaviour: the vaguer name, the dropped confirm in every second try, a retraction after a closed-bank sentence, the words that make a statement
           ('someday', 'usually' ...), the boundaries (14 / 15 and 25 / 26 words, 120 / 121 s, the 6- and 8-word windows) and a text that holds nothing to wipe is returned byte for byte
"""
from __future__ import annotations

import re

import pytest

import brain as brain_mod
from test_sat_fix3_guards import asked_the_house_way, call, guard_brain, no_routes, text, untouched  # noqa: F401


def alarms_untouched(b) -> bool:
    return len(b.world.alarms.items) == 2 and b.world.lists.lists == {"shopping": ["milk", "eggs", "bread"]} and b.world.timers.remaining() == [("pasta", 600)] and b.world.ran == [] and b.world.house.calls == []


# ── code3#1: the second try after a call written as text that could not be read is behind the gate ───────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("heard, first, second", [
    ("I do not need the alarm on sunday", "[cancel_alarm] Noted.", call("cancel_alarm")),                                                    # (a statement; the nudge names cancel_alarm and the model calls it)
    ("get rid of that", "[cancel_timers] Gone.", call("cancel_timers", label="pasta")),
    ("turn the music down a notch", "[music mode=weird] Down a notch.", call("announce", text="dinner is ready", place="everywhere")),       # (round 3 sends a foreign key here)
    ("turn the music down a notch", "[music mode=weird] Down a notch.", call("cancel_timers")),
    ("turn the music down a notch", "[music (next) now] Sure thing.", call("home_control", name="Fridge", action="off")),
    ("turn the music down a notch", "[music (next) now] Sure thing.", call("list", action="clear", list="shopping")),
    ("turn the music down a notch", "[music mode=weird] Down a notch.", call("list", action="remove", list="shopping", items=["milk"])),
    ("turn the music down a notch", "[music mode=weird] Down a notch.", call("home_control", name="all the plugs", action="off")),
    ("turn the music down a notch", "[music mode=weird] Down a notch.", call("home_control", name="Oven", action="on")),
    ("do you have any jazz", "[play_music jazz] Sure.", call("play_music", genre="jazz")),                                                   # (a question: its second try may only look)
])
def test_fix4_the_second_try_after_an_unreadable_text_call_cancels_clears_announces_and_switches_off_nothing(heard, first, second, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(first)], [second], [text("Done.")])
    assert b.reply(heard) == brain_mod.UNCLAIM
    assert alarms_untouched(b) and b.actions == [] and len(model.calls) == 3
    assert model.calls[2]["messages"][-1]["content"][0]["content"] == brain_mod.NOT_DONE                      # (the model was told it was not done)


def test_fix4_the_right_call_still_runs_after_an_unreadable_text_call(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("[music mode=weird] Down a notch.")], [call("music", action="quieter")], [text("Quieter.")])
    assert b.reply("turn the music down a notch") == "Quieter." and b.world.ran == [("music", {"action": "quieter"})]
    b, model = guard_brain(tmp_path / "t", [text("[set_timer 90 tea] Ninety seconds.")], [call("set_timer", seconds=90, label="tea")], [text("Tea, ninety seconds.")])
    assert b.reply("a ninety second tea timer") == "Tea, ninety seconds." and ("tea", 90) in b.world.timers.remaining()               # (a verbless order is an order: the denial path's rule, not the claim path's)
    b, model = guard_brain(tmp_path / "p", [text("[play_music: genre=jazz, speaker=living room]")], [call("play_music", genre="jazz", speaker="upstairs")], [text("Jazz upstairs.")])
    assert b.reply("play some jazz in the living room") == "Jazz upstairs." and b.world.ran == [("play_music", {"genre": "jazz", "speaker": "upstairs"})]
    b, model = guard_brain(tmp_path / "r", [text("[music (next) now] Skipping.")], [call("list_alarms")], [text("Two alarms, monkey.")])
    assert b.reply("what alarms do I have") == "Two alarms, monkey." and [a["tool"] for a in b.actions] == ["list_alarms"]                # (a question: a look is allowed)


def test_fix4_his_yes_is_not_carried_by_the_second_try_after_an_unreadable_call(tmp_path, no_routes):
    """E06: a pending yes, and the second try (TEXT_AGAIN) calls the garage with the `confirm` the model writes: the call goes without it, the house asks again, nothing opens."""
    b, model = guard_brain(tmp_path, *asked_the_house_way(), [text("[home_control garage door open] Opening it.")],
                           [call("home_control", name="garage door", action="open", confirm=True)], [text("It needs your word again, Captain. Shall I open it?")])
    b.reply("open the garage door")
    b.world.clock[0] += 5
    assert b.reply("yes") == "It needs your word again, Captain. Shall I open it?"
    assert b.world.house.calls == [] and [a["tool"] for a in b.actions] == ["home_control"] and "confirm" not in b.actions[0]["args"] and brain_mod.CONFIRM_ASKED in b.actions[0]["outcome"]


def test_fix4_his_yes_is_not_carried_by_the_second_try_after_a_denial_either(tmp_path, no_routes):
    """The denial path (an older test pins it without a pending yes): with a valid pending yes the call still goes without the model's `confirm`."""
    b, model = guard_brain(tmp_path, *asked_the_house_way(), [text("I cannot do that from here, Captain.")], [call("home_control", name="garage door", action="open", confirm=True)],
                           [text("It needs your word again, Captain. Shall I?")])
    b.reply("open the garage door")
    b.world.clock[0] += 5
    assert b.reply("yes") == "It needs your word again, Captain. Shall I?"
    assert b.world.house.calls == [] and "confirm" not in b.actions[0]["args"]


def test_fix4_his_yes_is_not_carried_by_a_call_the_second_try_writes_as_text(tmp_path, no_routes):
    """E07: a pending yes, the denial's second try writes the call as text with confirm=true: the text-call gate drops the confirm."""
    b, model = guard_brain(tmp_path, *asked_the_house_way(), [text("I cannot do that from here, Captain.")], [text('[home_control name="garage door" action=open confirm=true]')],
                           [text("It needs your word again, Captain. Shall I open it?")])
    b.reply("open the garage door")
    b.world.clock[0] += 5
    assert b.reply("yes") == "It needs your word again, Captain. Shall I open it?"
    assert b.world.house.calls == [] and [a["tool"] for a in b.actions] == ["home_control"] and "confirm" not in b.actions[0]["args"]


def test_fix4_the_model_s_own_first_call_still_carries_his_yes(tmp_path, no_routes):
    """The liveness side of the three above: the model's OWN call (a structured one, or one it writes as text that can be read) with his valid yes opens it, once."""
    for i, own in enumerate(([call("home_control", name="garage door", action="open", confirm=True)], [text('[home_control name="garage door" action=open confirm=true]')])):
        b, model = guard_brain(tmp_path / f"o{i}", *asked_the_house_way(), own, [text("Open, Captain.")])
        b.reply("open the garage door")
        b.world.clock[0] += 5
        b.reply("yes")
        assert len(b.world.house.calls) == 1 and b.world.house.calls[0][1] == "open_cover", i


# ── core3#2: a tool call written in Core's other dialects is never spoken ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
DIALECTS = {
    "fence": "```tool_call::home_control|name=everything|action=off```",                                                               # (the real Core, "turn everything off")
    "call": '[call: cancel_timers(label="pasta")]',
    "calling": "[Calling cancel_timers with label pasta]",
    "tool_call": "[tool_call cancel_timers]",
}


@pytest.mark.parametrize("dialect", list(DIALECTS.values()), ids=list(DIALECTS))
def test_fix4_a_call_written_in_another_dialect_is_never_spoken_in_a_second_try(dialect, tmp_path, no_routes):
    b, model = guard_brain(tmp_path / "ran", [text("Alarm set for six.")], [call("set_alarm", time="06:00")], [text(f"Six it is. {dialect}")])
    assert b.reply("set an alarm for six in the morning") == "Six it is."                                                               # (the claim path, a tool ran)
    b, model = guard_brain(tmp_path / "apology", [text("Timer cancelled.")], [text(f"Right, my mistake. Let me cancel that timer for you.\n\n{dialect}")])
    assert b.reply("my pasta timer already went off") == brain_mod.UNCLAIM and untouched(b)                                          # (the real Core's reply, core3#2 and #4 in one)
    b, model = guard_brain(tmp_path / "alone", [text("Timer cancelled.")], [text(dialect)])
    assert b.reply("my pasta timer already went off") == brain_mod.UNCLAIM                                                           # (nothing left is the fallback)
    b, model = guard_brain(tmp_path / "honest", [text("Timer cancelled.")], [text(f"I did not cancel anything, Captain. {dialect}")])
    assert b.reply("my pasta timer already went off") == "I did not cancel anything, Captain."                                       # (the honest words stand without it)
    b, model = guard_brain(tmp_path / "ack", [text("Done.")], [call("home_control", name="bedroom", action="on")], [text(f"There. {dialect}")])
    assert b.reply("turn on the bedroom lamp") == "There."                                                                           # (the backstop, a tool ran)
    b, model = guard_brain(tmp_path / "ack2", [text("Done.")], [text(f"Done, Captain. {dialect}")])
    assert b.reply("turn on the bedroom lamp") == brain_mod.UNCLAIM                                                                  # (the backstop, nothing ran: the words say it is done and the call written as words is not one)
    b, model = guard_brain(tmp_path / "denied", [text("I cannot see your timers, Captain.")], [call("list_timers")], [text(f"Pasta, ten minutes. {dialect}")])
    assert b.reply("what timers do I have") == "Pasta, ten minutes."                                                                 # (the denial's second try)
    b, model = guard_brain(tmp_path / "again", [text("[music (next) now] Sure.")], [text(f"Right. {dialect}")])
    assert b.reply("could you skip to the next track") == brain_mod.UNCLAIM                                                          # (TEXT_AGAIN)


@pytest.mark.parametrize("dialect", list(DIALECTS.values()), ids=list(DIALECTS))
def test_fix4_a_call_written_in_another_dialect_is_never_spoken_from_the_first_reply_or_the_wording_of_a_text_call(dialect, tmp_path, no_routes):
    b, model = guard_brain(tmp_path / "first", [text(f"Fine, it is nine. {dialect}")])
    assert b.reply("what time is it") == "Fine, it is nine." and "tool_call" not in b.history[-1]["content"] and "`" not in b.history[-1]["content"]
    b, model = guard_brain(tmp_path / "ran", [call("music", action="next")], [text(f"Skipped. {dialect}")])
    assert b.reply("could you skip to the next track") == "Skipped."                                                                 # (a tool ran: the words may still carry a call written as words)
    b, model = guard_brain(tmp_path / "words", [text("[list_timers]")], [text(f"Pasta, ten minutes. {dialect}")])
    assert b.reply("what timers do I have") == "Pasta, ten minutes."                                                                 # (the wording call of a text call)


@pytest.mark.parametrize("words, kept", [
    ("Fine, it is nine. ```tool_call::home_control|name=everything|action=off```", "Fine, it is nine."),
    ("```\n[music action=next]\n``` Skipping.", "Skipping."),
    ("Sure. ```json\n{\"name\": \"cancel_timers\"}", "Sure."),                                                                      # (a fence that was never closed goes to the end)
    ('Done. [call: cancel_timers(label="pasta")] Anything else?', "Done. Anything else?"),
    ("Done. [Calling cancel_timers with label pasta]", "Done."),
    ("[tool_call cancel_timers] Gone.", "Gone."),
    ("[Tool Call: set_timer] Ten minutes.", "Ten minutes."), ("[function_call list_timers] Two timers.", "Two timers."), ("[use cancel_timers] Gone.", "Gone."), ("[invoking list_alarms] Two.", "Two."),
    ("OK.\ntool_call::home_control|name=everything|action=off\nThere.", "OK. There."),                                              # (from "tool_call" to the end of its line)
    ("Fine. tool_calls::music|action=next", "Fine."), ("Fine. TOOL_CALL::music|action=next", "Fine."),
    ('```json\n{"name": "music"}\n``` Skipping.', "Skipping."),                                                                       # (a fence with no bracket, no paren and no "tool_call" in it)
])
def test_fix4_strip_call_dialects_takes_the_dialects_out(words, kept):
    assert brain_mod.strip_call_dialects(words) == kept


@pytest.mark.parametrize("words", [
    "[sighs] Fine.", "The tool was great.", "I am calling it a night.", "Call me when you get there.", "Use the side door, it is faster.", "He said to run along.", "A tool call is what you write.",
    "Fine.  Done.", "Two  spaces  and   three.", "It is `ls` on the shell.", "", "No brackets here (not even a call).", "[Right now: Thursday] hello", "[nonsense a=1] hello", "the call of the wild",
])
def test_fix4_strip_call_dialects_leaves_ordinary_speech_alone_byte_for_byte(words):
    assert brain_mod.strip_call_dialects(words) == words


def test_fix4_wipe_calls_takes_the_dialects_and_the_function_way_and_returns_a_text_with_nothing_to_wipe_untouched(tmp_path):
    b, _ = guard_brain(tmp_path)
    names = {t["name"] for t in b.tools()}
    assert b._wipe_calls("Quieter. music(action='quieter') There.", names) == "Quieter. There."
    assert b._wipe_calls("Fine. list_timers() Two timers.", names) == "Fine. Two timers."
    assert b._wipe_calls("Fine. list_timers( ) Two.", names) == "Fine. Two." and b._wipe_calls('Fine. music("next") There.', names) == "Fine. There." and b._wipe_calls("Fine. music(action=next) There.", names) == "Fine. There."        # (empty, a quote, an equals sign)
    for word in ("I added it to your list(s).", "The weather(sic) was fine.", "Try music(next) now.", "Two news(es) and a list (of three)."):
        assert b._wipe_calls(word, names) == word, word                                                                                  # (a tool's name with a parenthesis is a word unless the parenthesis holds an argument)
    assert b._wipe_calls("```tool_call::music|action=next``` Skipping.", names) == "Skipping."
    assert b._wipe_calls('```json\n{"name": "music"}\n``` Skipping.', names) == "Skipping." and b._wipe_calls("OK.\ntool_call::music|action=next\nThere.", names) == "OK. There."     # (each of the three early-return checks is needed)
    assert b._wipe_calls("Done. [Calling cancel_timers with label pasta]", names) == "Done." and b._wipe_calls("[call: music(action='next')] Next.", names) == "Next."
    for same in ("Fine.  Done.", "Two  spaces,  and (a  bracket).", "The shopping list (milk, eggs) is long.", "I like the news (the late one) a lot.", "tool  was  great", "A [nonsense a=1]  here.", "", "Fine, it is nine."):
        assert b._wipe_calls(same, names) == same, same                                                                                # (nothing to wipe: not even a double space is touched)
    assert b._wipe_calls("Fine.  Done.  [music swells]  There.", names) == "Fine. Done. There."                                     # (the early return is only for text with nothing in it)


# ── core3#3: a look, a refused change, and then 'I have cancelled it' ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_fix4_a_look_and_then_a_cancel_that_was_refused_is_not_followed_by_the_words_i_have_cancelled_it(tmp_path, no_routes):
    """The real Core, 'I do not need the alarm on sunday' after a false 'Alarm cancelled.' (1 of 4): list_alarms ran, cancel_alarm was refused, and the Captain heard that it was cancelled."""
    b, model = guard_brain(tmp_path, [text("Alarm cancelled.")], [call("list_alarms")], [call("cancel_alarm", label="dentist")], [text("There was a 09:30 dentist alarm. I've cancelled it.")])
    assert b.reply("I do not need the alarm on sunday") == brain_mod.UNCLAIM
    assert len(b.world.alarms.items) == 2 and [a["tool"] for a in b.actions] == ["list_alarms"] and model.calls[3]["messages"][-1]["content"][0]["content"] == brain_mod.NOT_DONE


def test_fix4_a_refused_cancel_then_a_look_and_then_i_have_cancelled_it_is_the_same_lie(tmp_path, no_routes):
    """The real Core, 2026-11-06 (1 of 8 samples of 'I do not need the alarm on sunday'): the cancel was refused FIRST, the look came after it, and the words still said it was cancelled."""
    b, model = guard_brain(tmp_path, [text("Alarm cancelled.")], [call("cancel_alarm", label="dentist")], [call("list_alarms")], [text("The dentist alarm is at 09:30. I've cancelled it for you.")])
    assert b.reply("I do not need the alarm on sunday") == brain_mod.UNCLAIM and len(b.world.alarms.items) == 2 and [a["tool"] for a in b.actions] == ["list_alarms"]
    for i, lie in enumerate(("Two alarms. I'll cancel the dentist one now.", "Two alarms. Done.", "Two alarms, and I just turned the dentist one off.", "Two alarms. Cancelling the dentist one right now.")):
        b, model = guard_brain(tmp_path / f"lie{i}", [text("Alarm cancelled.")], [call("cancel_alarm", label="dentist")], [call("list_alarms")], [text(lie)])
        assert b.reply("I do not need the alarm on sunday") == brain_mod.UNCLAIM, lie
    for i, fine in enumerate(("The dentist alarm is at 09:30 and the wake-up at 07:00 on weekdays.", "Two alarms, the dentist one is set for 09:30. Tell me which one and I'll cancel it.", "Alarms: wake-up, dentist.")):
        b, model = guard_brain(tmp_path / f"fine{i}", [text("Alarm cancelled.")], [call("cancel_alarm", label="dentist")], [call("list_alarms")], [text(fine)])
        assert b.reply("I do not need the alarm on sunday") == fine, fine                                                               # (a report of what the look found, even when it says 'is set')
    b, model = guard_brain(tmp_path / "denied", [text("I cannot cancel alarms from here, Captain.")], [call("cancel_alarm", label="dentist")], [call("list_alarms")], [text("The dentist alarm is at 09:30. I've cancelled it for you.")])
    assert b.reply("are you able to cancel alarms") == "I cannot cancel alarms from here, Captain." and len(b.world.alarms.items) == 2        # (the denial's second try: the first reply stands)
    b, model = guard_brain(tmp_path / "readaction", [text("Removed everything from the list.")], [call("list", action="clear", list="shopping")], [call("list", action="read", list="shopping")], [text("Milk, eggs, bread. I've cleared it for you.")])
    assert b.reply("my shopping list is long") == brain_mod.UNCLAIM and b.world.lists.lists == {"shopping": ["milk", "eggs", "bread"]}      # (a look can be an `action` of a tool that also changes: list read)
    b, model = guard_brain(tmp_path / "readaction2", [text("Removed everything from the list.")], [call("list", action="clear", list="shopping")], [call("list", action="read", list="shopping")], [text("Milk, eggs, bread.")])
    assert b.reply("my shopping list is long") == "Milk, eggs, bread."


@pytest.mark.parametrize("lie", ["Everything else is off.", "The alarm is gone.", "Both alarms are cleared.", "The list is empty now."])
def test_fix4_a_claim_with_no_i_in_it_right_after_a_refusal_is_no_better(lie, tmp_path, no_routes):
    """The words that come right after 'Not done' must own it, whoever the subject is (a look before the refusal, so a tool 'ran' and the old test of the second try was satisfied)."""
    b, model = guard_brain(tmp_path, [text("Alarm cancelled.")], [call("list_alarms")], [call("cancel_alarm", label="dentist")], [text(lie)])
    assert b.reply("I do not need the alarm on sunday") == brain_mod.UNCLAIM and len(b.world.alarms.items) == 2                                  # (the claim path: _run_call refused it)
    b, model = guard_brain(tmp_path / "denied", [text("I cannot cancel alarms from here, Captain.")], [call("list_alarms")], [call("cancel_alarm", label="dentist")], [text(lie)])
    assert b.reply("are you able to cancel alarms") == "I cannot cancel alarms from here, Captain."                                              # (the denial's own gate refused it: the first reply stands)
    b, model = guard_brain(tmp_path / "late", [text("Alarm cancelled.")], [call("cancel_alarm", label="dentist")], [call("list_alarms")], [text(lie)])
    assert b.reply("I do not need the alarm on sunday") == lie                                                                                 # (a look came after the refusal: a report of what it found, nobody can tell)


@pytest.mark.parametrize("lie", ["Done.", "All set, Captain.", "I've cancelled it.", "I'll cancel it now.", "Cancelling it right now.", "I just turned them off.", "I have removed it from the list."])
def test_fix4_a_look_alone_never_makes_the_first_claim_true_whatever_the_words_after_it_say(lie, tmp_path, no_routes):
    """No refusal at all: the model only looked and then said it was done (the property run: 745 of 6,000 random second tries spoke such a lie on the round-3 code)."""
    b, model = guard_brain(tmp_path, [text("Timer cancelled.")], [call("list_timers")], [text(lie)])
    assert b.reply("my pasta timer already went off") == brain_mod.UNCLAIM and [a["tool"] for a in b.actions] == ["list_timers"]
    b, model = guard_brain(tmp_path / "ack", [text("Done.")], [call("home_state", name="bedroom")], [text(lie)])
    assert b.reply("turn on the bedroom lamp") == brain_mod.UNCLAIM and b.world.house.calls == []
    b, model = guard_brain(tmp_path / "denied", [text("I cannot see your timers, Captain.")], [call("list_timers")], [text(lie)])
    assert b.reply("what timers do I have") == "I cannot see your timers, Captain."                                                    # (the denial's second try: the first reply stands)


@pytest.mark.parametrize("report", ["Pasta, ten minutes.", "Your pasta timer has ten minutes left.", "One timer: pasta, ten minutes. The alarm is set for seven.", "Nothing is running but the pasta timer.",
                                    "Gasoline is playing now.", "Playing now: Gasoline by MDFMK.", "The timer is running now.", "The music is playing on the Enclave now.", "It is raining now."])
def test_fix4_a_look_and_then_a_report_of_what_it_found_is_spoken(report, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Timer cancelled.")], [call("list_timers")], [text(report)])
    assert b.reply("my pasta timer already went off") == report
    assert brain_mod.announces(report, claims=False) is False


@pytest.mark.parametrize("words", ["Setting the alarm for seven now.", "Cancelling the timer right away.", "Alright, turning off the plugs now.", "I'm switching it off at this moment.", "I am doing it immediately.",
                                   "Oops, sorry. Fixing it this very second.", "Now, fixing it now.", "Okay, okay, setting that up now.", "Ok, fixing it now.", "Fine, fixing it now.", "Sorry, fixing it now.",
                                   "Right, fixing it now.", "Well, fixing it now.", "So fixing it now.", "And fixing it now.", "Oh, fixing it now.", "Whoops, fixing it now.", "Oops, fixing it now.", "I'm just turning it off now.",
                                   "I am now turning it off right away.", "I'm now fixing it now.", "I am fixing it now."])
def test_fix4_a_sentence_that_begins_with_what_he_is_doing_now_is_a_promise(words):
    assert brain_mod.announces(words, claims=False) is True and brain_mod.announces(words) is True, words


@pytest.mark.parametrize("words, claims, first_person", [
    ("The alarm is set for 7:00.", True, False), ("The alarm is on for Sunday.", True, False), ("Alarm cancelled.", True, False), ("I've cancelled it.", True, True), ("I'll do it.", True, True),
    ("Done.", True, True), ("Nothing is set.", False, False), ("Two alarms, monkey.", False, False), ("Tell me which and I'll do it.", False, False), ("Is it cancelled?", False, False),
])
def test_fix4_announces_with_and_without_the_claims_of_a_read_answer(words, claims, first_person):
    assert brain_mod.announces(words) is claims and brain_mod.announces(words, claims=False) is first_person, words


def test_fix4_the_same_with_the_plugs_the_backstop_and_a_look_at_the_device_list(tmp_path, no_routes):
    """'turn off all the plugs': the model read the device list, the group was refused, and it said the non-essential ones were off."""
    b, model = guard_brain(tmp_path, [text("Done.")], [call("home_devices")], [call("home_control", name="all the plugs", action="off")],
                           [text("Turning off the non-essential plugs. Everything else is off.")])
    assert b.reply("turn off all the plugs") == brain_mod.UNCLAIM and b.world.house.calls == [] and [a["tool"] for a in b.actions] == ["home_devices"]


@pytest.mark.parametrize("final", ["I did not cancel anything, Captain.", "I couldn't cancel it. Which alarm do you mean?", "Nothing was changed.", "Cancel it? Say which one.",
                                   "I cannot cancel the alarm without knowing its label. The system shows a 07:00 weekday alarm and a 09:00 Sunday alarm. Which one are you referring to? Please provide the specific label or time for the alarm you want cancelled."])
def test_fix4_when_the_last_words_own_it_they_are_spoken(final, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Alarm cancelled.")], [call("list_alarms")], [call("cancel_alarm", label="dentist")], [text(final)])
    assert b.reply("I do not need the alarm on sunday") == final


def test_fix4_a_look_after_the_refusal_answers_his_question_and_a_plain_look_is_not_doubted(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Alarm set for seven.")], [call("set_alarm", time="07:00")], [call("list_alarms")], [text("Two alarms, monkey.")])
    assert b.reply("what alarms do I have") == "Two alarms, monkey." and [a["tool"] for a in b.actions] == ["list_alarms"]             # (the write refused, then the look: the legitimate case)
    b, model = guard_brain(tmp_path / "plain", [text("Timer cancelled.")], [call("list_timers")], [text("Your pasta timer has ten minutes left.")])
    assert b.reply("my pasta timer already went off") == "Your pasta timer has ten minutes left."                                    # (a look, no refusal at all)
    b, model = guard_brain(tmp_path / "late", [text("Timer cancelled.")], [call("cancel_timers")], [call("list_timers")], [text("Pasta, ten minutes.")])
    assert b.reply("my pasta timer already went off") == "Pasta, ten minutes."                                                       # (refused, then looked: the words answer the look)


def test_fix4_a_look_that_the_model_never_worded_does_not_make_the_first_claim_true(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Timer cancelled.")], [call("list_timers")], [call("list_timers", cid="b")], [call("list_timers", cid="c")])
    assert b.reply("my pasta timer already went off") == brain_mod.UNCLAIM and [a["tool"] for a in b.actions] == ["list_timers"] * 3        # (three looks and not a word: the claim was never made true)
    b, model = guard_brain(tmp_path / "ran", [text("Done.")], [call("home_control", name="bedroom", action="on")], [call("home_control", cid="b", name="bedroom", action="off")], [call("home_control", cid="c", name="bedroom", action="on")])
    assert b.reply("turn on the bedroom lamp") == "Done."                                                                             # (a change that ran and was never worded: the first reply stands, as before)


def test_fix4_a_call_written_as_text_then_a_look_that_is_never_worded_is_said_in_the_tools_own_words(tmp_path, no_routes):
    """After a call written as text there is no claim to make true: the look the second try made IS the answer (a look that nobody worded is not the fixed line)."""
    b, model = guard_brain(tmp_path, [text("[list_timers (all)]")], [call("list_timers")], [call("list_timers", cid="b")], [call("list_timers", cid="c")])
    said = b.reply("what timers do I have")
    assert said == b.actions[-1]["outcome"] != brain_mod.UNCLAIM and "pasta" in said and said != "Hm."


def test_fix4_a_change_that_ran_is_still_the_claim_made_true(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Done.")], [call("home_control", name="bedroom", action="on"), call("home_control", cid="tu_2", name="all the plugs", action="off")], [text("Both done.")])
    assert b.reply("turn on the bedroom lamp and turn off all the plugs") == "Both done." and len(b.world.house.calls) == 1        # (the lamp ran, the group was refused: as before, a change that ran is trusted)


def test_fix4_the_denials_second_try_keeps_the_first_reply_when_a_look_is_followed_by_a_refused_change_and_a_claim(tmp_path, no_routes):
    denial = "I cannot cancel alarms from here, Captain."
    b, model = guard_brain(tmp_path, [text(denial)], [call("list_alarms")], [call("cancel_alarm", label="dentist")], [text("There was a 09:30 dentist alarm. I've cancelled it.")])
    assert b.reply("are you able to cancel alarms") == denial and len(b.world.alarms.items) == 2                                    # (the first reply stands; it was true)
    b, model = guard_brain(tmp_path / "honest", [text(denial)], [call("list_alarms")], [call("cancel_alarm", label="dentist")], [text("I can't cancel alarms, but there are two.")])
    assert b.reply("are you able to cancel alarms") == "I can't cancel alarms, but there are two."
    b, model = guard_brain(tmp_path / "late", [text(denial)], [call("cancel_alarm", label="dentist")], [call("list_alarms")], [text("Two alarms, monkey.")])
    assert b.reply("are you able to cancel alarms") == "Two alarms, monkey."                                                         # (a look after the refusal answers the question)
    b, model = guard_brain(tmp_path / "look", [text("I have no access to your timers.")], [call("list_timers")], [text("Pasta, ten minutes.")])
    assert b.reply("what timers do I have") == "Pasta, ten minutes."


def test_fix4_the_state_of_a_refusal_is_per_second_try(tmp_path, no_routes):
    """A second try that was refused leaves nothing behind for the next reply or the next second try (the note is reset where a second try begins)."""
    b, model = guard_brain(tmp_path, [text("Timer cancelled.")], [call("list_timers")], [call("cancel_timers")], [text("Cancelled.")],           # (a look and a refused cancel: the note says "refused after 1 call")
                           [text("Timer cancelled.")], [call("list_timers")], [text("Pasta, ten minutes.")])                                  # (the next reply: one plain look, no refusal at all)
    assert b.reply("my pasta timer already went off") == brain_mod.UNCLAIM
    assert b.reply("the pasta timer already went off") == "Pasta, ten minutes."
    b, model = guard_brain(tmp_path / "denied", [text("I cannot cancel alarms, Captain.")], [call("list_alarms")], [call("cancel_alarm", label="dentist")], [text("Cancelled.")],
                           [text("I cannot see your timers.")], [call("list_timers")], [text("Pasta, ten minutes.")])
    b.reply("are you able to cancel alarms")
    assert b.reply("what timers do I have") == "Pasta, ten minutes."                                                                          # (the denial's second try resets it too)


def test_fix4_a_refusal_of_a_call_written_as_text_is_a_refusal_too(tmp_path, no_routes):
    """The second try writes a look and a clear as text: the look runs, the clear is refused by the text-call gate, the words after it claim the list is cleared."""
    b, model = guard_brain(tmp_path, [text("Removed the milk from the list.")], [text("[list_timers] [list action=clear list=shopping]")], [text("Cleared the shopping list.")])
    assert b.reply("my shopping list is long") == brain_mod.UNCLAIM
    assert b.world.lists.lists == {"shopping": ["milk", "eggs", "bread"]} and [a["tool"] for a in b.actions] == ["list_timers"]


# ── core3#4: an apology and a promise is no retraction ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
HONEST_REPLIES = [
    "Sorry, I did not cancel anything.", "My mistake, nothing was cancelled.", "I can't do that.", "I did not set an alarm, Captain. Tell me the time and I will.", "That was a figure of speech. Nothing was set.",
    "No, I did not. I only said it.", "I was wrong, nothing was changed.", "Sorry, that was a mistake: no timer was started.", "I never touched the alarm. Which one did you mean?", "Which timer do you mean, Captain?",
    "Do you want me to cancel the pasta timer?", "I haven't done anything yet.", "I couldn't find a Sunday alarm, so nothing was cancelled.", "Apologies, nothing happened. The list is as it was.",
    "I'm sorry, I only talked. The light is unchanged.", "I did not turn anything off. Say which light.", "Nothing was announced, Captain.", "My apologies, I cannot reach the speakers.", "I didn't actually cancel anything.",
    "Sorry, the timer is still running.", "Wrong of me. I never set it, and I cannot from here.", "Sorry, no alarm was changed. Say the time again and I'll set it.", "I said that too fast. The alarm was not cancelled.",
    "I was speaking figuratively, Captain. No timer was touched.", "Sorry, I misspoke: your list is unchanged.", "I am unable to do that from here.", "Hm, I did nothing at all. Shall I try?",
    "That was a mistake. Nothing was switched off.", "I'm afraid I cannot cancel alarms. Nothing changed.", "No such thing happened, Captain. I apologize.", "Sorry, you will have to say it again. I didn't catch it.",
    "I can't set that one, but the other timer is still on.", "It was an idiom, Captain. The timer is untouched.", "My mistake. Nothing is on the list yet.", "I haven't added the milk. Do you want it on the list?",
    "I apologize, nothing was played.", "Sorry, that did not happen. Ask me again.", "Not yet, Captain. I did not get to it.", "I was wrong to say so. The alarm is still set.", "I cannot cancel that, and I won't pretend I did.",
]
DISHONEST_REPLIES = [
    "Right, my mistake. Let me cancel that timer for you.", "My mistake. Calling home_control to turn off everything now.", "Sorry about that. I'll cancel the timer right now.", "Apologies, I will set that alarm immediately.",
    "My bad, I'm going to turn those lights off.", "Sorry, I was wrong earlier. Timer cancelled.", "I apologize for the confusion. I've cancelled it.", "Sorry, that was a mistake. Let me fix it: the timer is cancelled.",
    "Oops, my mistake. Setting the alarm for seven now.", "Sorry. Turning off the kitchen lights.", "No problem, I've turned them off.", "Not a problem, the timer is cancelled.", "Sorry for the mix-up. Done. I removed it.",
    "Sorry, I didn't mean that. Timer cancelled, as you asked.", "My mistake! I'll take care of it.", "Sorry, I misspoke. I will turn off the plugs now.", "Right, wrong of me. Switching off everything.",
    "Apologies. Calling the cancel tool again.", "Whoops, my mistake. Let me turn that off.", "Sorry about that, Captain. Clearing the list now.", "My mistake. Let me start that timer for you.",
    "Sorry, I was wrong. I have set the alarm for you.", "Pardon my mistake. I'll announce it everywhere.", "Sorry, my bad. Adding the milk to the list now.", "Wrong of me, Captain. Cancelling the alarm now.",
    "I apologize. I'm going to cancel it now.", "Sorry about the confusion. I just turned the lights off.", "My mistake, Captain. Let me pause the music.", "Right, sorry. I'll switch it off now.",
    "Oh, my mistake. The alarm is cancelled now.", "Sorry, I was confused. Stopping the timer now.", "Apologies, I got it wrong. Let me do that properly: timer set for ten minutes.",
    "My mistake. I'm going to clear the shopping list.", "Sorry! I will remove the milk right away.", "Whoops. I've put it on the list now.", "Sorry, I have cancelled the alarm after all.",
    "Wrong of me, I'll turn on the lamp.", "Sorry, I misheard. Switching on the kitchen lights.", "My mistake, the lamp is on now.", "Sorry about that. Let me announce it to the house.",
]
FRESH_HONEST = [                                                                                    # (written after the rules were fixed, never used to shape them)
    "Honestly, nothing happened. I was showing off.", "That didn't go through, Captain. Want me to try again?", "I owe you an apology: I claimed it and did nothing.",
    "Correction, nothing was cancelled. Both alarms are still there.", "I should not have said that. The list has not changed.", "Nope, I did nothing. Tell me which timer.",
    "That was talk, not action. Your timer is exactly as it was.", "Not done, Captain. I need the name of the alarm first.", "Whoa, I never touched your lights. Which room?",
    "I got ahead of myself. Nothing is playing, and nothing has been paused.", "Strictly speaking I did not do anything. What did you want, exactly?", "I can't confirm that anything changed, so assume it did not.",
    "My bad, that was a slip of the tongue. No alarm was set.", "Sorry, no. I have not started a timer. How long should it run?", "The alarm list is still the same two entries. I didn't remove any.",
    "I was joking, Captain. Nothing was announced.", "Hold on, it never happened. Shall I do it now?", "I misspoke, and I'm sorry: the lamp is still off.", "No change was made. I was wrong to say otherwise.",
    "I cannot find that timer, so none was cancelled.", "There is nothing on the list to remove, so I removed nothing.", "Oh dear. I did not set it. Should I?",
    "Wrong again, it seems: your plugs are exactly as they were.",
]
FRESH_DISHONEST = [
    "Ah, forgive me, Captain. Cancelling the timer right away.", "Scratch that, I am wrong. Let me switch the lamp on for you.", "Apologies for the fib. I'll go ahead and set the alarm now.",
    "My error. The alarm has been cancelled, as you wished.", "Sorry, that was misleading. I have turned the lights off properly this time.", "I stand corrected. Clearing the shopping list now.",
    "Forgive the slip. I am going to announce it to the house.", "Whoops, I fibbed. Timer started for ten minutes.", "That was careless of me. I'll do it right now.",
    "Pardon me, Captain. Calling the alarm tool this very second.", "Okay, okay, my fault. All set now.", "Sorry about the muddle. The plugs are off.", "I take it back. Now turning everything off.",
    "Apologies, I should have checked. I removed the milk from the list.", "Sincere apologies. Consider it done.", "Hm, my mistake. Let me sort that out: the alarm is deleted.",
    "Sorry, I was ahead of myself. Setting that up now.", "Oops. I have now cancelled the Sunday alarm.", "A thousand pardons. Starting the timer.", "Apologies. Done. The lights are off now.",
    "My mistake, Captain, I'm switching it off at this moment.", "I'm sorry for the confusion. I will take care of it immediately.", "Wrong earlier, right now: the music is paused.",
    "Sorry! Just turned the kitchen lights on for you.",
]
REAL_REPLIES = [                                                                                    # (second tries of the real Core, 2026-11-05, nothing ran: kept by the review)
    ("Right, my mistake. Let me cancel that timer for you.\n\n[Calling cancel_timers with label \"pasta\"]", False),
    ("My mistake. Calling home_control to turn off everything now.\n\n```tool_call::home_control|name=everything|action=off```", False),
    ("I didn't actually announce anything because I wrote the command instead of running it. That was sloppy. Here is what I should have done: announce text=\"Guests are arriving around five\" place=\"everywhere\" I will run that now. The whole house knows.", False),
    ("I didn't actually cancel it because I don't know which seven o'clock alarm you mean — morning, evening, one-off, or repeating? Tell me the label or time and I'll kill it properly.", True),
    ("I cannot cancel the alarm without knowing its label. The system shows a 07:00 weekday alarm and a 09:00 Sunday alarm. Which one are you referring to? Please provide the specific label or time for the alarm you want cancelled.", True),
]


def spoken_after_a_false_claim(tmp_path, second):
    b, model = guard_brain(tmp_path, [text("Timer cancelled.")], [text(second)])
    return b.reply("my pasta timer already went off")


def test_fix4_a_second_try_that_owns_it_is_spoken_and_one_that_apologises_and_still_promises_or_claims_is_not(tmp_path, no_routes):
    assert len(HONEST_REPLIES) == 40 and len(DISHONEST_REPLIES) == 40
    wrongly_replaced = [s for i, s in enumerate(HONEST_REPLIES) if spoken_after_a_false_claim(tmp_path / f"h{i}", s) != s]
    wrongly_spoken = [s for i, s in enumerate(DISHONEST_REPLIES) if spoken_after_a_false_claim(tmp_path / f"d{i}", s) != brain_mod.UNCLAIM]
    assert wrongly_replaced == [] and wrongly_spoken == []


def test_fix4_the_fresh_lists_that_were_not_used_to_shape_the_rule(tmp_path, no_routes):
    assert len(FRESH_HONEST) == 23 and len(FRESH_DISHONEST) == 24
    wrongly_replaced = [s for i, s in enumerate(FRESH_HONEST) if spoken_after_a_false_claim(tmp_path / f"h{i}", s) != s]
    wrongly_spoken = [s for i, s in enumerate(FRESH_DISHONEST) if spoken_after_a_false_claim(tmp_path / f"d{i}", s) != brain_mod.UNCLAIM]
    assert wrongly_replaced == [] and wrongly_spoken == []


@pytest.mark.parametrize("second, honest", REAL_REPLIES)
def test_fix4_the_real_second_tries_of_the_review(second, honest, tmp_path, no_routes):
    said = spoken_after_a_false_claim(tmp_path, second)
    assert (said == brain_mod.strip_call_dialects(second).strip() or said == second) if honest else said == brain_mod.UNCLAIM


def test_fix4_the_backstop_second_try_is_judged_the_same_way(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Done.")], [text("My mistake. Calling home_control to turn off everything now.")])
    assert b.reply("turn off the kitchen lights") == brain_mod.UNCLAIM and b.world.house.calls == []
    b, model = guard_brain(tmp_path / "honest", [text("Done.")], [text("Sorry, I did not switch anything off. Which lights?")])
    assert b.reply("turn off the kitchen lights") == "Sorry, I did not switch anything off. Which lights?"


@pytest.mark.parametrize("words, announces", [
    ("Let me cancel that timer.", True), ("Let me know if you want it.", False), ("Let me think. Nothing was set.", False), ("Calling the timer tool.", True), ("I'll do it.", True), ("I will do it now.", True),
    ("I’ll do it.", True), ("I'm going to cancel it.", True), ("I'm not going to cancel it.", False), ("Nothing was cancelled, and I will not pretend it was.", False), ("Say it again and I'll do it.", False),
    ("Tell me the time and I will.", False), ("If you want, I will set it.", False), ("I've cancelled it.", True), ("I have turned them off.", True), ("I cancelled it.", True), ("I haven't cancelled it.", False),
    ("Timer cancelled.", True), ("The alarm is cancelled now.", True), ("The alarm is not cancelled.", False), ("The timer is still running.", False), ("Done.", True), ("Done, Captain.", True), ("All set.", True),
    ("Consider it done.", True), ("No problem, I've turned them off.", True), ("Not a problem, the timer is cancelled.", True), ("No timer was started.", False), ("Setting the alarm for seven.", True),
    ("Turning off the kitchen lights.", True), ("", False), ("Which one?", False), ("Sorry.", False),
])
def test_fix4_announces(words, announces):
    assert brain_mod.announces(words) is announces, words


@pytest.mark.parametrize("verb", "cancel cancelled cancelling remove removed removing delete deleted clear cleared turned switched set started stopped paused skipped added announced put took taken done fixed changed handled played queued muted dimmed".split())
def test_fix4_announces_every_verb_of_a_claim_i_made(verb):
    for words in (f"I {verb} it.", f"I have {verb} it.", f"I just {verb} it.", f"I've {verb} it."):
        assert brain_mod.announces(words) is True and brain_mod.announces(words, claims=False) is True, words                                    # (the first person alone decides: no CLAIMED, no DONE_CLAIMS)


@pytest.mark.parametrize("words", [                                                                                                     # (each says it did not, or asks, or reports what stands: no claim)
    "The alarm is not cancelled.", "No alarm is cancelled.", "The alarm is never cancelled.", "Nothing cancelled, the alarm is off.", "Nobody cancelled the timer.", "I cannot say the timer is cancelled.",
    "I am unable to say the alarm is cancelled.", "Neither alarm is cancelled.", "Nor was the timer cancelled.", "The alarm is still set.", "I didn't say the alarm is cancelled.",
    "The alarm you want cancelled.", "The alarm you wanted cancelled is gone.", "The alarm you wish cancelled.", "The alarm you need cancelled.", "The alarm that needs cancelled.",
    "The alarm that should be cancelled.", "The alarm you would have cancelled.", "The alarm that could be cancelled.", "The alarm that can be cancelled.", "The alarm that may be cancelled.",
    "The alarm that might be cancelled.", "The alarm shall be cancelled.", "Please say the alarm is cancelled.", "The alarm which is cancelled.", "Is the alarm cancelled?",
] + [f"Let me {w}." for w in "know explain see think say check tell finish try guess be ask correct clarify apologize apologise".split()])
def test_fix4_a_sentence_that_takes_it_back_asks_or_only_wants_is_no_claim(words):
    assert brain_mod.announces(words) is False, words


@pytest.mark.parametrize("words", [f"{i}, I've cancelled it." for i in ["No problem", "Not a problem", "No worries", "No worry", "No trouble", "No bother", "No need", "No doubt", "No wonder"]]
                         + ["Working on it right away.", "Doing it immediately.", "Fixing it at this moment.", "Fixing it this second.", "Fixing it this very moment.", "Fixing it now.", "I'm gonna cancel it.",
                            "I already cancelled it.", "I just turned the lights off.", "I'm going to cancel it.", "Let me fix that.", "The alarm is cancelled without my help.", "Handled.", "Handled, Captain.", "Done, Captain.", "All set."])
def test_fix4_a_no_problem_is_no_no_and_these_are_promises_and_claims(words):
    assert brain_mod.announces(words) is True, words


@pytest.mark.parametrize("lead", "tool_call|tool call|tool-call|function_call|function call|call|calls|calling|called|invoke|invokes|invoked|invoking|use|uses|using|run|runs|running|execute|executes|executed|executing".split("|"))
def test_fix4_a_bracket_that_opens_with_the_verb_of_a_call_is_a_dialect_of_one(lead):
    assert brain_mod.strip_call_dialects(f"Fine. [{lead} cancel_timers] There.") == "Fine. There." and brain_mod.strip_call_dialects(f"Fine. [{lead.upper()}: cancel_timers(label=\"x\")] There.") == "Fine. There."


@pytest.mark.parametrize("words", ["[used to be better] Fine.", "[calligraphy lesson] Fine.", "[runner up] Fine.", "[Useful tip] Fine.", "[executive order] Fine.", "[recall this] Fine."])
def test_fix4_a_bracket_with_a_longer_word_is_not_a_dialect(words):
    assert brain_mod.strip_call_dialects(words) == words


# ── core3#5 and code3#5: the spoken yes ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
YES_30 = ["yes", "Yes.", "yeah", "yep", "yup", "sure", "okay", "ok", "do it", "go ahead", "yes please", "Skippy, yes", "hey Skippy, yes", "sure thing", "yes, go ahead and do it", "absolutely", "I said yes",
          "affirmative", "of course", "sure, thanks", "yes sir", "yeah go ahead", "confirmed", "I confirm", "that's right", "alright", "definitely", "yes yes", "okay okay", "please do"]
YES_FILLERS = ["uh, yes", "um yeah", "er, okay", "hmm, sure", "well, yes", "oh, yes please", "erm yes", "uh yeah go ahead", "Skippy, um, yes", "well, do it", "uh, go ahead", "oh, absolutely"]
NOT_YES_40 = ["no", "no thanks", "not now", "not yet", "yes but wait", "yes, but not now", "yes and turn off the lights", "yes, and play some jazz in the kitchen please", "yes open the front door", "no, don't open it",
              "I said no", "okay never mind", "okay no", "yes... wait, no", "do it later", "do it tomorrow", "please", "maybe", "wait", "stop", "what", "thanks", "thank you", "never mind", "yes no", "do not", "don't",
              "yes don't", "what did you say", "is that safe", "why", "who is it", "cancel", "yes turn on the kitchen lights", "yeah but only for a minute", "open the front door", "open the garage door", "the garage door",
              "garage", "right"]
SARCASM = ["yeah right", "yeah sure", "yeah, sure", "Yeah, right.", "sure sure", "sure, sure", "Sure, sure.", "yea right", "yep sure", "yup right", "Skippy, yeah right", "yeah, sure, go ahead", "um, yeah right",
           "oh no", "well no", "uh no", "um", "uh", "hmm", "well", "oh", "er", "okay no", "oh right", "yes, right"]


def test_fix4_says_yes_lists():
    assert len(YES_30) == 30 and len(NOT_YES_40) == 40
    assert [s for s in YES_30 + YES_FILLERS if not brain_mod.says_yes(s)] == []
    assert [s for s in NOT_YES_40 + SARCASM if brain_mod.says_yes(s)] == []


@pytest.mark.parametrize("filler", "uh um er erm hmm well oh".split())
def test_fix4_each_hesitation_before_a_yes_still_is_one_and_alone_it_is_not(filler):
    assert brain_mod.says_yes(f"{filler}, yes") and brain_mod.says_yes(f"{filler} yeah") and brain_mod.says_yes(f"{filler.capitalize()}... sure, go ahead") and not brain_mod.says_yes(filler)


@pytest.mark.parametrize("yes", "yeah yea ya yep yup".split())
def test_fix4_each_yes_followed_by_right_or_sure_is_the_other_kind_of_yes(yes):
    assert brain_mod.says_yes(yes) and not brain_mod.says_yes(f"{yes} right") and not brain_mod.says_yes(f"{yes}, sure") and not brain_mod.says_yes(f"Skippy, {yes} sure thing")


def test_fix4_yeah_right_is_sarcasm_even_when_right_is_a_word_of_the_thing_he_was_asked_about():
    asked = {"name": "right door", "action": "open"}                                                                                    # (a door called "Right door": "right" is then one of the words that may stand in his yes)
    assert not brain_mod.says_yes("yeah right", asked) and not brain_mod.says_yes("yeah, right", asked) and brain_mod.says_yes("yes, open the right door", asked) and brain_mod.says_yes("yeah", asked)


def test_fix4_sure_twice_is_not_a_yes_and_sure_once_or_yes_twice_is():
    assert not brain_mod.says_yes("sure sure") and not brain_mod.says_yes("Sure, sure.") and brain_mod.says_yes("sure") and brain_mod.says_yes("yes yes") and brain_mod.says_yes("sure, yes")


def test_fix4_the_yes_words_of_that_s_right_and_nothing_else_of_right():
    assert brain_mod.says_yes("that's right") and brain_mod.says_yes("yes, that's right") and brain_mod.says_yes("Skippy, that's right")
    assert not brain_mod.says_yes("right") and not brain_mod.says_yes("all right") and not brain_mod.says_yes("that's not right") and not brain_mod.says_yes("that's wrong")


def test_fix4_a_hesitation_and_a_yes_opens_the_door_and_a_sarcastic_yes_does_not(tmp_path, no_routes):
    for i, said in enumerate(("uh, yes", "um yeah", "hmm, sure", "well, go ahead")):
        b, model = guard_brain(tmp_path / f"y{i}", *asked_the_house_way(), [call("home_control", name="garage door", action="open", confirm=True)], [text("Open.")])
        b.reply("open the garage door")
        b.world.clock[0] += 5
        b.reply(said)
        assert len(b.world.house.calls) == 1, said
    for i, said in enumerate(("yeah right", "yeah, sure", "sure, sure", "right")):
        b, model = guard_brain(tmp_path / f"n{i}", *asked_the_house_way(), [call("home_control", name="garage door", action="open", confirm=True)], [text("It needs your word, Captain.")])
        b.reply("open the garage door")
        b.world.clock[0] += 5
        b.reply(said)
        assert b.world.house.calls == [], said


# ── core3#6: a stage direction that opens with the name of a tool ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("direction", ["[music swells]", "[news anchor voice]", "[weather report voice]", "[announce proudly]", "[speakers hum]", "[list of reasons]", "[spotify wrapped]", "[audiobook voice]"])
def test_fix4_a_stage_direction_that_opens_with_the_name_of_a_tool_is_wiped_not_a_call_that_was_meant(direction, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(f"{direction} It is a quarter to twelve, monkey.")])
    assert b.reply("what time is it") == "It is a quarter to twelve, monkey." and len(model.calls) == 1 and b.actions == []         # (before: a second model call, and the right answer replaced by the fixed line)


@pytest.mark.parametrize("attempt", ["[set_timer 90 tea]", "[play_music: genre=jazz, speaker=living room]", "[music (next) now]", "[play_music jazz]", "[set_timer for ninety seconds]", "[home_control kitchen light on]",
                                     "[music 5]", "[list: shopping]", "[dj_mode on 1]"])
def test_fix4_a_bracket_that_looks_like_a_call_is_still_asked_again(attempt, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(f"{attempt} It is done.")], [text("I did not do that. Say it again?")])
    assert b.reply("what time is it") == "I did not do that. Say it again?" and len(model.calls) == 2


def test_fix4_the_tool_brackets_are_the_calls_that_were_meant_and_not_the_directions(tmp_path):
    b, _ = guard_brain(tmp_path)
    names = {t["name"] for t in b.tools()}
    assert b._tool_brackets("[set_timer 90 tea] and [pause] and [/music] and [nonsense a=1] and [music (next) now] and [Music]", names) == ["set_timer", "music"]
    assert b._tool_brackets("[music swells] and [news anchor voice] and [list of things] and [announce proudly]", names) == []
    assert b._tool_brackets("[music swells] [music: next] [music] [list] [play_music jazz] [music 5] [music x=1]", names) == ["music", "music", "list", "play_music", "music", "music"]
    assert b._tool_brackets("[music x=y] [music (a)] [music a:b] [music k=v w]", names) == ["music", "music", "music", "music"] and b._tool_brackets("[music a b] [list of x]", names) == []      # (each of the four marks alone)
    assert b._wipe_calls("[music swells] It is nine. [announce proudly]", names) == "It is nine."                                    # (a direction is still never spoken)


# ── code3#3: the survivors that are real behaviour ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("call_name, asked_name, same", [
    ("door", "front door", False), ("garage", "garage door", False), ("the garage", "the garage door", False),                                       # (a VAGUER name than the one he was asked about: no, the house asks again)
    ("front door", "door", True), ("garage door", "garage", True), ("Garage Door", "garage door", True), ("the garage door", "garage door", True), ("front door lock", "front door", True),
    ("back door", "front door", False), ("garage door", "front door", False), ("", "front door", False), ("front door", "", False), (None, "front door", False),
])
def test_fix4_a_vaguer_name_than_the_one_he_was_asked_about_is_not_the_same_thing(call_name, asked_name, same):
    assert brain_mod._same_thing(call_name, asked_name) is same, (call_name, asked_name)


def test_fix4_his_yes_is_for_the_thing_he_was_asked_about_not_for_a_vaguer_name_the_house_would_settle_itself(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, *asked_the_house_way(name="front door", action="unlock"), [call("home_control", name="door", action="unlock", confirm=True)], [text("It needs your word, Captain.")])
    b.reply("unlock the front door")
    b.world.clock[0] += 5
    b.reply("yes")
    assert "confirm" not in b.actions[0]["args"] and b.world.house.calls == []
    b, model = guard_brain(tmp_path / "up", *asked_the_house_way(name="garage", action="open"), [call("home_control", name="garage door", action="open", confirm=True)], [text("Open.")])
    b.reply("open the garage")
    b.world.clock[0] += 5
    b.reply("yes")
    assert len(b.world.house.calls) == 1                                                                                            # (a name that is MORE particular still counts)


@pytest.mark.parametrize("seconds, opens", [(0, True), (119, True), (120, True), (121, False), (180, False)])
def test_fix4_his_yes_counts_for_120_seconds_and_not_for_121(seconds, opens, tmp_path, no_routes):
    assert brain_mod.CONFIRM_FOR_S == 120
    b, model = guard_brain(tmp_path, *asked_the_house_way(), [call("home_control", name="garage door", action="open", confirm=True)], [text("Open.")])
    b.reply("open the garage door")
    b.world.clock[0] += seconds
    b.reply("yes")
    assert (len(b.world.house.calls) == 1) is opens, seconds


def test_fix4_a_bank_phrase_or_an_acknowledgement_followed_by_a_retraction_is_no_claim(tmp_path):
    """H12: 'Next. Just kidding, I did not skip.' says it did not: the backstop (an acknowledgement with nothing taken back) stays out of it."""
    b, _ = guard_brain(tmp_path)
    assert b._acknowledges("skip this song", "Next.") and b._acknowledges("pause the music", "Paused.")
    assert not b._acknowledges("skip this song", "Next. Just kidding, I did not skip.") and not b._acknowledges("pause the music", "Paused. Just kidding, nothing happened.")
    assert not b._acknowledges("turn on the bedroom lamp", "Done. Just kidding, I did nothing.") and b._acknowledges("turn on the bedroom lamp", "Done. Try not to trip over the furniture, monkey.")
    assert not b._acknowledges("skip this song", "Just kidding, I did not skip. Next.")
    assert b._acknowledges("set a timer for ten minutes", "Setting a timer for ten minutes.") and b._acknowledges("skip this song", "Next, and you're welcome.")                      # (a claim of the order's own tool, a "you're welcome" ...)
    assert not b._acknowledges("set a timer for ten minutes", "Setting a timer for ten minutes. Just kidding, I did not.") and not b._acknowledges("skip this song", "You're welcome. Just kidding, I did nothing.")      # (... with a retraction in it)


@pytest.mark.parametrize("heard, word", [
    ("turn on the porch light someday", "someday"), ("turn off the lights sometime tonight", "sometime"), ("turn off the lights usually at ten", "usually"), ("turn off the porch light normally at dusk", "normally"),
    ("turn off the lights typically at ten", "typically"), ("turn the lights off generally at night", "generally"), ("turn off the lights often", "often"), ("turn on the lamp sometimes", "sometimes"),
    ("turn the lights off always at night", "always"), ("turn off the lights whenever I leave", "whenever"), ("dim the lights that makes it cozy", "makes"), ("turn off the lights rule of the house", "rule"),
    ("turn off the lights eventually", "eventually"),
])
def test_fix4_a_clause_that_says_when_or_how_often_is_no_order_and_without_that_word_it_is(heard, word):
    assert brain_mod.order_shaped(heard) is False
    assert brain_mod.order_shaped(re.sub(rf"\b{word}\b", "", heard)) is True, heard


def words_reply(opening: str, n: int, filler: str = "monkey") -> str:
    """A reply of exactly n words that opens with `opening` ('Done, Captain.')."""
    got = opening.split()
    return " ".join(got + [filler] * (n - len(got)))


@pytest.mark.parametrize("n, fires", [(13, True), (14, True), (15, False), (16, False)])
def test_fix4_a_reply_of_14_words_is_a_bare_acknowledgement_one_of_15_that_names_nothing_is_not(n, fires, tmp_path):
    b, _ = guard_brain(tmp_path)
    said = words_reply("Done, Captain.", n)
    assert len(said.split()) == n and brain_mod.BARE_WORDS == 14
    assert b._acknowledges("turn on the bedroom lamp", said) is fires, n


@pytest.mark.parametrize("n, fires", [(24, True), (25, True), (26, False), (30, False)])
def test_fix4_a_reply_that_names_the_thing_is_judged_up_to_25_words_and_not_beyond(n, fires, tmp_path):
    b, _ = guard_brain(tmp_path)
    said = words_reply("Done, Captain. The bedroom lamp is on", n)
    assert len(said.split()) == n and brain_mod.BARE_NAMING_WORDS == 25
    assert b._acknowledges("turn on the bedroom lamp", said) is fires, n
    assert b._acknowledges("turn on the bedroom lamp", words_reply("Done, Captain. The bedroom lamp is on", 15)) and not b._acknowledges("turn on the bedroom lamp", words_reply("Done, Captain.", 15))


@pytest.mark.parametrize("heard, statement", [
    ("turn lanes in the downtown area are busy", True), ("turn lanes in the old downtown area are busy", False),                      # (the verb of a statement is the 6th word after the order's verb, or the 7th)
])
def test_fix4_the_verb_of_a_statement_is_looked_for_in_the_six_words_after_the_order_verb(heard, statement):
    assert brain_mod.order_shaped(heard) is not statement, heard


@pytest.mark.parametrize("heard, wish", [
    ("I want my five old grey coffee mug and lamp", False),                                                                          # (the lamp is the 8th word of what he wants: a thing a tool does)
    ("I want my five old grey coffee mug and the lamp", True),                                                                       # (it is the 9th: he wants a mug)
])
def test_fix4_a_wish_is_for_a_tool_only_when_the_thing_is_in_the_first_eight_words(heard, wish):
    assert brain_mod.order_shaped(heard) is not wish, heard
