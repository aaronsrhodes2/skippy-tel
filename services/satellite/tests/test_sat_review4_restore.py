"""review4:code, problem 2: a call put back from the reasoning (fix4:text, core3#1) is only for a caller that hands the words to the text-call rescue.

Brain._spoken puts "[music]" and the lines that give its arguments ("action: next", "next") in front of the words whenever nothing has run in the reply. The music re-ask (REASK_MUSIC: Brain._second_try with
no gate) never hands its words to _text_calls, so those lines stayed in the words (the bracket and the function-way line were wiped, the rest was not): the Captain heard, and the memory kept,
"action: next I could not find jazz. Which room, Captain?". Before the restore the reasoning was dropped whole and the words were "I could not find jazz. Which room, Captain?".
"""
from __future__ import annotations

import pytest

import brain as brain_mod
from test_sat_fix3_guards import call, guard_brain, no_routes, text  # noqa: F401

SHAPES = {
    "kv-line": "<think>The user wants jazz.\n[music]\naction: next\n</think>I could not find jazz. Which room, Captain?",
    "bare-word": "<think>[music]\nnext\n</think>That did not work. Which room, Captain?",
    "kv-then-level": "<think>[music]\naction: quieter\nlevel: 3\n</think>I did not do it. Which room, Captain?",
    "function": "<think>[music]\nmusic(action='quieter')\n</think>I did not do it. Which room?",
    "one-line": "<think>[music action=next]</think>I did not do it. Which room?",
}
CLEAN = {"kv-line": "I could not find jazz. Which room, Captain?", "bare-word": "That did not work. Which room, Captain?", "kv-then-level": "I did not do it. Which room, Captain?",
         "function": "I did not do it. Which room?", "one-line": "I did not do it. Which room?"}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_review4_the_music_re_ask_never_speaks_or_keeps_the_lines_of_a_call_put_back_from_its_reasoning(shape, tmp_path, no_routes):
    b, model = guard_brain(tmp_path, [text("Playing jazz in the kitchen.")], [text(SHAPES[shape])])
    out = b.reply("play some jazz in the kitchen")
    assert b.guard == ("reask", "claim") and len(model.calls) == 2
    assert out == CLEAN[shape] and b.world.ran == [] and b._turns[-1][-1]["content"] == out


@pytest.mark.parametrize("shape", ["kv-line", "bare-word"])
def test_review4_a_call_put_back_is_still_run_where_the_text_call_rescue_reads_it(shape, tmp_path, no_routes):
    """The liveness side: the first reply, and the second tries that read their words as calls (the denial, the backstop, a claim, a call that could not be read), still get their calls back."""
    reasoned = {"kv-line": "<think>[music]\naction: quieter\n</think>Quieter, Captain.", "bare-word": "<think>[music]\nquieter\n</think>Quieter, Captain."}[shape]
    b, model = guard_brain(tmp_path / "first", [text(reasoned)], [text("Quieter, Captain.")])
    assert b.reply("turn the music down a notch") == "Quieter, Captain." and b.world.ran == [("music", {"action": "quieter"})]
    b, model = guard_brain(tmp_path / "denial", [text("I cannot do that from here, Captain.")], [text(reasoned)], [text("Quieter, Captain.")])
    assert b.reply("turn the music down a notch") == "Quieter, Captain." and b.world.ran == [("music", {"action": "quieter"})]
    b, model = guard_brain(tmp_path / "backstop", [text("Done.")], [text(reasoned)], [text("Quieter, Captain.")])
    assert b.reply("turn the music down a notch") == "Quieter, Captain." and b.world.ran == [("music", {"action": "quieter"})]
    b, model = guard_brain(tmp_path / "again", [text("[music (next) now] Sure.")], [text(reasoned)], [text("Quieter, Captain.")])
    assert b.reply("turn the music down a notch") == "Quieter, Captain." and b.world.ran == [("music", {"action": "quieter"})]


def test_review4_the_class_form_and_the_flag(tmp_path):
    from types import SimpleNamespace as NS
    resp = NS(content=[NS(type="text", text="<think>[music]\nnext\n</think>Skipped.")], stop_reason=None)
    b, _ = guard_brain(tmp_path)
    assert brain_mod.Brain._spoken(resp) == "Skipped." and b._spoken(resp) == "[music]\nnext\nSkipped." and b._spoken(resp, restore=False) == "Skipped." and b._spoken(resp, True) == "[music]\nnext\nSkipped."
