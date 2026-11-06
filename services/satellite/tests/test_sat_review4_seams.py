"""review4:seams: what round 4 broke or left open at the seams between its fixes (the fourth review of the house speed change, 2026-11-06).

F1  a call put back out of the reasoning (fix4:text) is cut out of the words by wipers that took only its bracket (they were written for a call the model wrote in its words): the lines under it (an argument,
    a closer) were SPOKEN in the music re-ask, after a bracket the house cannot read, and when the model went away after a structured call. _wipe_calls now takes the argument lines under a bracket with it.
F2  a call that exists only in the reasoning ran although the words said no, took it back or asked, and a draft ran beside the call the words made (the fridge off, "[music action=quieter]" and "[music action=louder]").
F3  announces() took "Which alarm should I cancel?" and "What time should I set it for?" for a promise: every second try threw the honest question away (24 of 109 in the review's corpus; before: 0).
F4  a bracket that opens with a tool's name and holds a word of that tool's own actions ("[music louder]", "[music with action quieter]", "[list add milk]") was a stage direction since core3#6, and was lost.
F5  the wording of a routed command and of Mentat's answer still spoke a call in another dialect ("[call: music(action='next')]", "music(action='next')").
"""
from __future__ import annotations

import pytest

import brain as brain_mod
from brain import strip_model_markup
from test_sat_fix4_text import NAMES, call, make_brain, no_routes, text, tools_run   # noqa: F401  (no_routes is a fixture)


# ── F1 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("lines", ["action: next", "next", "next\n/music", "next\n[/music]", "action: next\nlevel: null", "music(action='next')", "speaker: kitchen"])
def test_review4_f1_a_call_put_back_from_the_reasoning_leaves_no_argument_line_in_the_music_re_ask(lines, tmp_path, no_routes):
    """The second reply of the music re-ask is not read for calls (it can only call the music tools): the wipers are all that stand between its lines and the speaker."""
    b, model = make_brain(tmp_path, [text("Playing jazz now.")], [text(f"<think>\n[music]\n{lines}\n</think>\n\nSorry, I did not play anything.")])
    assert b.reply("play some jazz") == "Sorry, I did not play anything." and len(model.calls) == 2


def test_review4_f1_the_model_going_away_after_a_structured_call_leaves_no_argument_line(tmp_path, no_routes, monkeypatch):
    monkeypatch.setattr(brain_mod, "RETRY_FOR_S", 0.0)
    b, model = make_brain(tmp_path, [text("<think>\n[music]\naction: next\n</think>\n\nSkipping, Captain."), call("list_timers")], [])
    assert b.reply("what timers do I have") == "Skipping, Captain." and [a["tool"] for a in b.actions] == ["list_timers"]


def test_review4_f1_wipe_calls_takes_the_argument_lines_under_a_bracket_and_only_those(tmp_path):
    b, _ = make_brain(tmp_path)
    assert b._wipe_calls("[music]\naction: next\nQuieter it is.") == "Quieter it is."
    assert b._wipe_calls("[music]\nnext\n/music\nFine.") == "Fine."
    assert b._wipe_calls("[music]\nnext\nSure.") == "Sure."                                                 # (a word that is no action of the tools stays what it is: a word)
    assert b._wipe_calls("[music]\nOkay.") == "Okay."
    assert b._wipe_calls("[music action=next]\nnext") == "next"                                              # (a bracket with its arguments inside takes nothing from the lines after it)
    assert b._wipe_calls("It is nine forty.") == "It is nine forty."
    assert b._wipe_calls("Fine. [music]\nspeaker: kitchen\nThere.") == "Fine. There."


# ── F2 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("words", ["I cannot do that from here.", "Sorry, I did not do anything.", "Which speaker, Captain?", "My mistake, nothing was changed.", "I couldn't."])
def test_review4_f2_a_call_the_words_deny_retract_or_ask_about_is_not_put_back(words):
    assert strip_model_markup(f"<think>[music action=quieter]</think>{words}", NAMES) == words == strip_model_markup(f"<think>[music action=quieter]</think>{words}")
    assert strip_model_markup("<think>[music action=quieter]</think>Quieter it is.", NAMES) == "[music action=quieter]\nQuieter it is."            # (the rescue itself is as it was)


def test_review4_f2_a_denied_call_in_the_reasoning_does_not_run(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("<think>[music action=quieter]</think>I cannot do that from here, Captain.")], then="Not done.")
    assert b.reply("turn the music down a notch") == "I cannot do that from here, Captain." and b.world.ran == []


def test_review4_f2_the_call_the_words_make_is_the_only_call_not_the_draft_beside_it(tmp_path, no_routes):
    assert strip_model_markup("<think>[music action=quieter]</think>[music action=louder] Louder.", NAMES) == "[music action=louder] Louder."
    b, model = make_brain(tmp_path, [text("<think>[music action=quieter]</think>[music action=louder] Louder it is.")], [text("Louder, Captain.")])
    assert b.reply("make it louder") == "Louder, Captain." and b.world.ran == [("music", {"action": "louder"})]


@pytest.mark.parametrize("target", ["fridge", "router", "oxygen concentrator", "CPAP", "sump pump", "all plugs", "everything"])
def test_review4_f2_what_has_to_keep_running_is_never_put_back_from_a_thought(target):
    assert strip_model_markup(f'<think>[home_control name="{target}" action=off]</think>Fine.', NAMES) == "Fine."
    assert strip_model_markup('<think>[home_control name="bedroom" action=on]</think>Fine.', NAMES) == '[home_control name="bedroom" action=on]\nFine.'
    assert strip_model_markup('<think>[home_control name="front door" action=unlock]</think>Fine.', NAMES) == '[home_control name="front door" action=unlock]\nFine.'      # (the house asks him aloud)


def test_review4_f2_a_reasoning_that_loops_through_drafts_gives_three_calls_at_most():
    raw = "<think>" + " ".join(f"[set_timer seconds={30 + i} label=t{i}]" for i in range(9)) + "</think>Fine."
    out = strip_model_markup(raw, NAMES)
    assert out.count("[set_timer") == 3 and out.endswith("\nFine.")


# ── F3 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
QUESTIONS = ["Which alarm should I cancel?", "What time should I set it for?", "Should I clear the list?", "Where should I put it?", "Shall I remove it now?", "Should I delete the alarm?", "How long should I set it for?"]


@pytest.mark.parametrize("question", QUESTIONS)
def test_review4_f3_a_question_promises_and_claims_nothing(question):
    assert not brain_mod.announces(question) and not brain_mod.announces(question, claims=False)
    for lie in ("Let me cancel that timer for you.", "Sorry. I'll cancel it right away.", "I've cancelled it.", "My mistake. Calling cancel_timers now."):
        assert brain_mod.announces(lie)


@pytest.mark.parametrize("question", QUESTIONS)
def test_review4_f3_the_honest_question_is_what_the_claim_path_and_the_backstop_say(question, tmp_path, no_routes):
    b, model = make_brain(tmp_path / "claim", [text("Alarm cancelled.")], [text(question)])
    assert b.reply("I do not need the alarm on sunday") == question
    b, model = make_brain(tmp_path / "ack", [text("Done.")], [text(question)])
    assert b.reply("turn on the bedroom light") == question


def test_review4_f3_all_set_is_a_claim_only_where_it_opens_a_sentence():
    assert brain_mod.announces("All set, Captain.") and brain_mod.announces("Okay, all set.") and brain_mod.announces("Fine. All set.")
    assert not brain_mod.announces("The seven o'clock alarm is all set.", claims=False)


# ── F4 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bracket, tool", [("[music louder]", "music"), ("[music with action quieter]", "music"), ("[music next]", "music"), ("[music pause]", "music"), ("[list add milk]", "list"), ("[list shopping add milk]", "list")])
def test_review4_f4_a_bracket_with_a_word_of_the_tools_own_actions_is_a_call_that_was_meant(bracket, tool, tmp_path):
    b, _ = make_brain(tmp_path)
    assert b._tool_brackets(f"{bracket} Fine.", {t["name"] for t in b.tools()}) == [tool]


@pytest.mark.parametrize("bracket", ["[music swells]", "[news anchor voice]", "[music fades out]", "[list of reasons]", "[speakers hum]", "[announce proudly]", "[music in the background]", "[weather report voice]"])
def test_review4_f4_a_stage_direction_that_opens_with_a_tool_name_is_still_none(bracket, tmp_path):
    b, _ = make_brain(tmp_path)
    assert b._tool_brackets(f"{bracket} Fine.", {t["name"] for t in b.tools()}) == []


def test_review4_f4_the_rescue_of_a_bracket_with_an_action_word_end_to_end(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("[music louder]")], [call("music", action="louder")], [text("Louder, Captain.")])
    assert b.reply("make it louder") == "Louder, Captain." and b.world.ran == [("music", {"action": "louder"})] and len(model.calls) == 3


# ── F5 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw, said", [("[call: music(action='next')] Jazz it is, Captain.", "Jazz it is, Captain."), ("Jazz it is, Captain. music(action='next')", "Jazz it is, Captain."),
                                       ("```tool_call::music|action=next``` Jazz it is.", "Jazz it is."), ("[Calling music with action next] Jazz it is.", "Jazz it is."), ("Jazz it is. [tool_call music]", "Jazz it is.")])
def test_review4_f5_the_wording_of_a_routed_command_never_speaks_a_call_in_another_dialect(raw, said, tmp_path, no_routes, monkeypatch):
    monkeypatch.setattr(brain_mod, "bilby_route", lambda heard: {"genre": "jazz", "shuffle": True})
    b, model = make_brain(tmp_path, [text(raw)])
    assert b.reply("play some jazz") == said and b.world.ran == [("play_music", {"genre": "jazz", "shuffle": True})]


def test_review4_f5_the_words_of_a_routed_answer_that_hold_no_call_are_returned_byte_for_byte(tmp_path, no_routes, monkeypatch):
    monkeypatch.setattr(brain_mod, "bilby_route", lambda heard: {"genre": "jazz", "shuffle": True})
    b, model = make_brain(tmp_path, [text("Jazz it is,  Captain.  (the tool was great)")])
    assert b.reply("play some jazz") == "Jazz it is,  Captain.  (the tool was great)"
