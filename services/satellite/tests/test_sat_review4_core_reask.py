"""review4:core: a call put back from the reasoning (strip_model_markup, fix4:text) is only for a caller that goes on to READ the text calls of the words.

The music re-ask (REASK_MUSIC, `_second_try` with ordered=None) runs no text call and wipes only the brackets: a second reply "<think>[music]\\naction: next</think>Skipping. Want something else?" came out as
"action: next\\nSkipping. Want something else?" (the question made it "honest", so it was spoken). The same for the wording call of _text_calls after calls the second-try gate refused (nothing had run, so the
reply looked like one before any tool). Brain._spoken(resp, restore=False) puts nothing back; those two callers pass it.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from brain import Brain
from test_sat_fix4_text import make_brain, no_routes, text, tools_run  # noqa: F401

REASONED = "<think>[music]\naction: next</think>Skipping. Want something else?"


def test_review4_restore_is_for_the_callers_that_read_the_calls(tmp_path):
    b, model = make_brain(tmp_path)
    resp = NS(content=[text("<think>[music]\naction: next</think>Skipping.")], stop_reason=None)
    assert b._spoken(resp) == "[music]\naction: next\nSkipping."                 # (the first reply: _text_calls reads it)
    assert b._spoken(resp, restore=False) == "Skipping."
    assert Brain._spoken(resp) == "Skipping."                                     # (the class form restores none, as before)


def test_review4_the_music_reask_speaks_no_argument_line_of_a_call_in_the_reasoning(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("Consider it done. Playing your jazz collection on the Enclave.")], [text(REASONED)], then="Okay.")
    said = b.reply("play some jazz")
    assert "action" not in said and "next\n" not in said and "[" not in said, said
    assert tools_run(b) == []


def test_review4_the_first_reply_still_gets_its_call_from_the_reasoning(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("<think>[music action=quieter]</think>Quieter, monkey.")], then="Quieter, monkey.")
    b.reply("turn the music down a notch")
    assert tools_run(b) == [("music", {"action": "quieter"})]


@pytest.mark.parametrize("second", ["<think>[music action=quieter]</think>Quieter, monkey.", "[music]\nquieter\n</think>\n\nQuieter."])
def test_review4_a_second_try_that_reads_text_calls_still_gets_one_from_the_reasoning(second, tmp_path, no_routes):
    # the backstop's second try (a bare "Done." to an order) reads the calls written as text: ordered is not None
    b, model = make_brain(tmp_path, [text("Done.")], [text(second)], then="Done, monkey.")
    b.reply("turn the music down a notch")
    assert ("music", {"action": "quieter"}) in tools_run(b)
