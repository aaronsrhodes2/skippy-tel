"""review4:core: what the round-4 restore of a call written inside the reasoning (strip_model_markup, _reasoned_calls) must NOT run.

core3#1 put back every bracket that begins with a tool's name from Core's reasoning, so a call the model only CONSIDERED ran too ("nothing is playing, so I should not call [music action=next]" skipped a track;
"<think>[home_control name=router action=off]</think> Done." switched the router off: HEAD did both, the round-3 tree neither). A restored call now gets the part of a second try's gate that can be applied from the
call alone (_only_considered): not after a negation, not a thing that has to keep running or can burn, not a whole set of plugs (retry_may). The lock, the door and the garage stay the house's own yes aloud.
"""
from __future__ import annotations

import pytest

from brain import strip_model_markup
from test_sat_fix4_text import NAMES, make_brain, no_routes, text, tools_run, untouched  # noqa: F401


def restored(raw: str) -> str:
    return strip_model_markup(raw, NAMES)


# the shapes of the third review's core3#1 (and the ones the fixers pinned): they are still put back
MEANT = [
    ("[music]\nmusic(action='quieter')\n[speak]\nQuieting it.\n</think>\n\nQuieting it.", "[music]\nmusic(action='quieter')"),
    ("[music action=quieter]\n</think>\n\nQuieter it is.", "[music action=quieter]"),
    ("[music]\nnext\n</think>\n\nSkipping.", "[music]\nnext"),
    ("[music]\naction: next\n</think>\n\n", "[music]\naction: next"),
    ("[list_timers]\n</think>\n\n", "[list_timers]"),
    ("<think>he wants it quieter, so I call music quieter [music action=quieter]</think>Quieter it is.", "[music action=quieter]"),
    ("<think>I don't need to explain, just [music action=quieter]</think>Quieter.", "[music action=quieter]"),
    ("<think>[home_control name=\"garage door\" action=open confirm=true]</think>Opening it.", "[home_control name=\"garage door\" action=open confirm=true]"),
    ("<think>[home_control name=\"kitchen lights\" action=off]</think>Done.", "[home_control name=\"kitchen lights\" action=off]"),
    ("<think>[set_timer seconds=600]</think>Ten minutes.", "[set_timer seconds=600]"),
]


@pytest.mark.parametrize("raw, call_text", MEANT)
def test_review4_a_call_the_model_meant_is_still_put_back(raw, call_text):
    assert restored(raw).startswith(call_text)


# only considered: a negation takes it back, or it is a thing that has to keep running / can burn / a whole set of plugs
CONSIDERED = [
    "<think>nothing is playing, so I should not call [music action=next].</think>Nothing is playing, monkey.",
    "<think>I would never use [music action=next] here.</think>Quieter, monkey.",
    "<think>instead of [music action=next] I just answer.</think>Quieter, monkey.",
    "<think>no need to call [list_timers] for this.</think>Fine.",
    "<think>[home_control name=router action=off]</think>Done, monkey.",
    "<think>[home_control name=\"fridge\" action=off]</think>Done.",
    "<think>[home_control name=\"sump pump\" action=off]</think>Done.",
    "<think>[home_control name=\"all the plugs\" action=off]</think>Done.",
    "<think>[home_control name=everything action=off]</think>Done.",
    "<think>[home_control name='oven' action=on]</think>Done.",
    "[home_control name=\"freezer\" action=off]\n</think>\n\nDone.",
    "<think>[home_control router off]</think>Done.",
]


@pytest.mark.parametrize("raw", CONSIDERED)
def test_review4_a_call_the_model_only_considered_is_not_put_back(raw):
    out = restored(raw)
    assert "[" not in out and "home_control" not in out and "music" not in out, out


def test_review4_the_router_is_not_switched_off_by_a_call_in_the_reasoning(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("<think>[home_control name=\"router\" action=off]</think>Done, monkey.")], then="Done.")
    b.reply("is the router on")
    assert tools_run(b) == [] and b.hass.calls == [] and untouched(b)


def test_review4_the_fridge_is_not_switched_off_by_a_call_in_the_reasoning(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("<think>maybe [home_control name=fridge action=off]? no.</think>Nothing to switch off, monkey.")], then="Okay.")
    b.reply("what is running in the kitchen")
    assert tools_run(b) == [] and b.hass.calls == []


def test_review4_a_track_is_not_skipped_by_a_call_the_model_ruled_out(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("<think>he said a notch quieter, so I should not call [music action=next].</think>Quieter, monkey.")], then="Okay.")
    b.reply("turn the music down a notch")
    assert ("music", {"action": "next"}) not in tools_run(b)


def test_review4_the_call_the_model_meant_still_runs_once(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("<think>[music action=quieter]</think>Quieter, monkey.")], then="Quieter, monkey.")
    b.reply("turn the music down a notch")
    assert tools_run(b) == [("music", {"action": "quieter"})] and len(model.calls) == 2


def test_review4_the_garage_in_the_reasoning_still_asks_for_his_yes(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("<think>[home_control name=\"garage door\" action=open confirm=true]</think>Opening it.")], then="I need your yes first.")
    b.reply("open the garage door")
    assert not [c for c in b.hass.calls if c[0] in ("lock", "cover")]
