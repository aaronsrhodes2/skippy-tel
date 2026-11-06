"""final5: two small things the last live trials on the real Core showed (2026-11-06), after the fifth review's fixes.

1. The music re-ask (REASK_MUSIC) was answered, on the real Core, with "[music volume 4] Quieter now. Try not to strain your tiny ears." and the words were SPOKEN as an honest second try with nothing
   done: _honest looked for a retraction with HONEST, and the "not" of a quip's "try not to" is one (QUIP_NEGATIONS takes it out first everywhere else). A claim with a quip is the fixed line now.
2. "turn the music down a notch" (and "a touch", "a tad", "one notch", "slightly") went to the model: 2 of 24 live trials of it claimed it in words only. music_route strips the degree word like it
   does "a bit" / "a little", so the phrase is the plain quieter / louder, with no model call.
"""
from __future__ import annotations

import pytest

import brain as brain_mod
from brain import music_route
from test_sat_fix3_guards import call, guard_brain, no_routes, text  # noqa: F401


@pytest.mark.parametrize("heard, action", [
    ("turn the music down a notch", "quieter"), ("turn the music up a notch", "louder"), ("turn it down a notch", "quieter"), ("turn it up a notch", "louder"),
    ("could you turn the music down a notch", "quieter"), ("Skippy, turn the music down a notch please", "quieter"), ("turn the volume down one notch", "quieter"),
    ("turn it down a touch", "quieter"), ("turn it up a tad", "louder"), ("turn the music down slightly", "quieter"), ("turn the volume up slightly", "louder"),
    ("turn it down a bit", "quieter"), ("turn it up a little", "louder"),                                               # (what worked before)
])
def test_final5_a_degree_word_after_a_volume_command_is_the_plain_command(heard, action):
    assert music_route(heard) == {"action": action}


@pytest.mark.parametrize("heard", ["take it down a notch", "turn the lights down a notch", "turn the heat up a notch", "that was a notch above the rest", "what is a notch", "set the thermostat up a notch"])
def test_final5_a_degree_word_alone_is_no_music_command(heard):
    assert music_route(heard) is None


def test_final5_a_notch_is_routed_the_model_only_words_it(tmp_path):
    """The route runs the tool; the one model call left (the harness's tool answer is not the closed bank's "Volume four.") offers no tools, so it can claim nothing."""
    b, model = guard_brain(tmp_path)
    out = b.reply("turn the music down a notch")
    assert b.world.ran == [("music", {"action": "quieter"})] and not any(c.get("tools") for c in model.calls) and out


@pytest.mark.parametrize("words", [
    "Quieter now. Try not to strain your tiny ears.",
    "Volume lowered. Try not to shout at me about it.",
    "There. Remember not to complain about my taste in music.",
])
def test_final5_a_quip_with_a_not_in_it_is_no_retraction(words, tmp_path, no_routes):
    b, _ = guard_brain(tmp_path)
    assert b._honest(words) is False


@pytest.mark.parametrize("words", ["I did not do it. Try not to worry.", "Sorry, I set nothing. Try not to shout.", "That was a mistake, monkey. Try not to gloat.", "I can't from here. Try not to sulk.",
                                   "Which speaker? Try not to take all day.", "Quieter now. Try not to strain your ears. Or did I get that wrong?"])
def test_final5_a_real_retraction_or_question_next_to_a_quip_still_stands(words, tmp_path, no_routes):
    b, _ = guard_brain(tmp_path)
    assert b._honest(words) is True


def test_final5_the_music_re_ask_that_claims_again_with_a_quip_is_the_fixed_line(tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Playing jazz in the kitchen.")], [text("[music volume 4] Quieter now. Try not to strain your tiny ears.")])
    out = b.reply("play some jazz in the kitchen")
    assert b.guard == ("reask", "claim") and len(model.calls) == 2 and b.world.ran == []
    assert out == brain_mod.UNCLAIM_MUSIC and b._turns[-1][-1]["content"] == out
