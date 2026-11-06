"""review4:code, problem 1: a tool call written in PLAIN WORDS inside its bracket ("[music with quieter]", "[music next]") is a call that was meant, not a stage direction.

fix4:guards (core3#6) stopped "[music swells]" from costing a second model call and the right answer, by calling a bracket that opens with a tool's name a call only when what follows looks like arguments (a colon, an
equals sign, a digit, a parenthesis) or is nothing. The real Core writes the call in plain words too: 6 of the 43 real replies to "turn the music down a notch" (textcall4_live1 / 2, 2026-11-06) were
"[music with quieter]", "[music with quieter action]", "[music with action \"quieter\"]". With that rule alone they were wiped as directions, no second try was made, and the words that came with them ("There. I suppose
that won't deafen you now.") were spoken with the volume unchanged: the Captain's oldest complaint. A bracket alone ("[music with action next]", "[music next]") became "Hm. I had nothing to say, which is unlike me."
"""
from __future__ import annotations

import pytest

import brain as brain_mod
from test_sat_fix3_guards import call, guard_brain, no_routes, text  # noqa: F401

# The real Core's replies (the fixers' live runs of 2026-11-06), the first words of each.
REAL = [
    "[music with quieter]\n\nThere. I suppose that won't deafen you now.",
    "[music with quieter]\nThere. Less noise for your delicate primate ears.",
    "[music with quieter action]\nThe volume's been reduced. You're welcome for the courtesy. [chuckles]",
    '[music with action "quieter"]\n\n[sighs] Fine. I will turn it down before you complain about the noise pollution of my magnificent taste in music.',
    "[music with quieter] [chuckles] Consider it done. I'll try not to let my magnificence disturb your slumber any more than necessary.",
]
PLAIN = ["[music with action next]", "[music next]", "[music louder]", "[music quieter] Done.", "[music action quieter]", "[list add milk] Added.", "[spotify play jazz]"]


@pytest.mark.parametrize("raw", REAL + PLAIN)
def test_review4_a_call_written_in_plain_words_in_its_bracket_is_asked_again_and_runs(raw, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(raw)], [call("music", action="quieter")], [text("Quieter.")])
    assert b.reply("turn the music down a notch") == "Quieter."
    assert b.world.ran == [("music", {"action": "quieter"})] and len(model.calls) == 3          # (the first reply, the second try with the nudge, the wording)


@pytest.mark.parametrize("raw", REAL + PLAIN)
def test_review4_and_when_the_second_try_does_not_do_it_the_claim_is_never_spoken(raw, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text(raw)], [text("There. Done.")])
    out = b.reply("turn the music down a notch")
    assert out == brain_mod.UNCLAIM and b.world.ran == [] and len(model.calls) == 2


@pytest.mark.parametrize("direction", ["[music swells]", "[news anchor voice]", "[weather report voice]", "[announce proudly]", "[speakers hum]", "[list of reasons]", "[spotify wrapped]", "[audiobook voice]",
                                       "[music starts with a flourish]", "[music with a flourish]", "[music with strings]", "[music fades out]", "[music plays softly]", "[music pauses]", "[music in the background]", "[music sting]"])
def test_review4_a_stage_direction_that_opens_with_the_name_of_a_tool_is_still_wiped(direction, tmp_path, no_routes):
    """The side core3#6 was right about: one model call, the right answer."""
    b, model = guard_brain(tmp_path, [text(f"{direction} It is a quarter to twelve, monkey.")])
    assert b.reply("what time is it") == "It is a quarter to twelve, monkey." and len(model.calls) == 1 and b.world.ran == []


def test_review4_the_brackets_that_were_meant(tmp_path):
    b, _ = guard_brain(tmp_path)
    names = {t["name"] for t in b.tools()}
    assert b._tool_brackets("[music with quieter] [music next] [music louder] [list add milk] [music action quieter] [spotify play jazz]", names) == ["music", "music", "music", "list", "music", "spotify"]
    assert b._tool_brackets("[music swells] [music starts with a flourish] [music with a flourish] [music fades out] [list of reasons] [news anchor voice] [audiobook voice] [spotify wrapped] [music a b]", names) == []
    assert b._tool_brackets("[music with action \"next\"] [music With Quieter]", names) == ["music", "music"]                        # (the tool's own action words, any case)
