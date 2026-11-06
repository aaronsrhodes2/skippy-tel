"""review5: what the fifth (integration) review found in the merge of the fourth round's pieces.

R5-1  _reasoned_calls threw a call the model MEANT away when the words after the reasoning held a "?" anywhere or a _RETRACTS word anywhere ("There. Quieter. Happy now?", "Sorry, monkey, ... Quieter now."): the
      Captain heard a false "Quieter" and nothing ran. 14 of 95 real replies that wrote a call as text carry a question of that kind. Only words that are ALL questions or retractions take the call back (_takes_back).
R5-2  announces() took any sentence with a "?" for honest, so a claim that ends in a tag question ("Done, happy now?", "I've cancelled it, okay?") was spoken after a second try that did nothing.
R5-3  the wording of a routed answer and Mentat's took the bracket of a call but left the argument lines under it ("action: next"), spoken and kept: _phantom ran first and _wipe_calls found no bracket. 16 of 275 real replies.
R5-4  _NOT_MEANT did not know the typographic apostrophe: "I shouldn’t call [music action=next]" skipped a track.
"""
from __future__ import annotations

import pytest

import brain as brain_mod
from brain import UNCLAIM
from test_sat_fix4_text import NAMES, make_brain, no_routes, text, tools_run, untouched   # noqa: F401  (no_routes is a fixture)

THINK_FN = "<think>He wants the music quieter.\n[music]\nmusic(action='quieter')\n</think>\n\n"
THINK_ONE = "<think>He wants it quieter. [music action=quieter]</think>"


@pytest.mark.parametrize("pre, words", [
    (THINK_FN, "There. Quieter. Happy now?"),
    (THINK_ONE, "Quieter it is. Better, or shall I whisper?"),
    (THINK_FN, "There. Quieter. Satisfied? My ears were suffering."),          # (real Core, 2026-11-06)
    (THINK_FN, "Fine, quieter. I can't believe you're complaining about my taste in music."),
    (THINK_ONE, "You didn't say please, but fine. Quieter."),
    (THINK_FN, "Sorry, monkey, my magnificence was too loud. Quieter now."),
    (THINK_ONE, "I have not enjoyed your taste lately, but quieter it is."),
    (THINK_ONE, "Nothing was wrong with it, but fine, quieter."),
])
def test_review5_a_call_the_model_meant_is_put_back_whatever_dig_or_tag_question_comes_with_it(pre, words, tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text(pre + words)], [text("Quieter, monkey.")], then="Quieter, monkey.")
    assert b.reply("turn the music down a notch") == "Quieter, monkey." and tools_run(b) == [("music", {"action": "quieter"})]


@pytest.mark.parametrize("words", ["Which speaker, Captain?", "Sorry, I did not do anything.", "My mistake, nothing was changed.", "I couldn't.", "Sorry, but I did not do it.", "I did not do it. Which room?"])
def test_review5_a_call_the_words_take_back_or_only_ask_about_is_still_not_put_back(words):
    assert strip_markup(f"<think>[music action=quieter]</think>{words}") == words


def strip_markup(raw):
    return brain_mod.strip_model_markup(raw, NAMES)


@pytest.mark.parametrize("claim", ["Done, happy now?", "I've cancelled it, okay?", "Handled, satisfied?", "I'll cancel it right away, shall I?", "Let me cancel that timer for you, okay?", "Done, monkey, anything else?"])
def test_review5_a_claim_that_ends_in_a_tag_question_is_still_a_claim(claim):
    assert brain_mod.announces(claim)


@pytest.mark.parametrize("question", ["Which alarm should I cancel?", "What time should I set it for?", "Should I clear the list?", "Cancelled? Which one, Captain?", "Did you want me to cancel it?"])
def test_review5_a_plain_question_is_still_no_claim(question):
    assert not brain_mod.announces(question)


@pytest.mark.parametrize("heard, first, second", [("I do not need the alarm on sunday", "Alarm cancelled.", "I've cancelled it, okay?"), ("turn on the bedroom light", "Done.", "Done, monkey, anything else?"),
                                                  ("cancel the pasta timer", "Timer cancelled.", "I'll cancel it right away, shall I?")])
def test_review5_a_second_try_that_claims_with_a_tag_question_is_not_spoken(heard, first, second, tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text(first)], [text(second)])
    assert b.reply(heard) == UNCLAIM and untouched(b) and not b.hass.calls


@pytest.mark.parametrize("raw", ["[music]\naction: next\nJazz it is, Captain.", "[music] action: next\nJazz it is, Captain.", "[play_music]\nwhat: jazz\nJazz it is, Captain.", "[music]\nnext\nJazz it is, Captain.",
                                 "Jazz it is, Captain.\n[music]\naction: next", "[music]\n\naction: next\n\nJazz it is, Captain."])
def test_review5_the_wording_of_a_routed_command_speaks_and_keeps_no_argument_line(raw, tmp_path, no_routes, monkeypatch):
    monkeypatch.setattr(brain_mod, "bilby_route", lambda heard: {"genre": "jazz", "shuffle": True})
    b, model = make_brain(tmp_path, [text(raw)])
    assert b.reply("play some jazz") == "Jazz it is, Captain." and b.history[-1]["content"] == "Jazz it is, Captain."


@pytest.mark.parametrize("apostrophe", ["'", "’"])
def test_review5_a_negation_with_either_apostrophe_rules_the_call_out(apostrophe, tmp_path, no_routes):
    raw = f"<think>nothing is playing, so I shouldn{apostrophe}t call [music action=next].</think>Nothing is playing, monkey."
    b, model = make_brain(tmp_path, [text(raw)], then="Okay.")
    b.reply("turn the music down a notch")
    assert ("music", {"action": "next"}) not in tools_run(b)


def test_review5_many_calls_on_one_line_of_the_reasoning_cost_no_quadratic_time():
    """R5-5: _written_calls(lead=True) sliced and searched the whole line before every call: 1000 calls on a line took 0.8 s, 4000 took 17 s (before the fourth round: 2 ms and 8 ms)."""
    import time
    raw = "<think>" + "[music action=next] " * 3000 + "</think>Fine."
    began = time.time()
    out = strip_markup(raw)
    assert time.time() - began < 2.0 and out.endswith("Fine.") and out.count("[music action=next]") == 1
