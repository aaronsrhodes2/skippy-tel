"""review4:core: two live shapes of round 4's announces() (the third review's core3#3 / core3#4 guard) that went wrong on the real Core.

1. "I didn't cancel it because the pasta timer doesn't exist anymore, it already went off. You're all set." (an honest answer to "my pasta timer already went off", 1 of 9 live claim-path second tries) was thrown
   away for the fixed line ("That did not get done, whatever I just claimed. Say it again ...") because "all set" is read as a claim: "you're all set" is the other idiom, it claims nothing.
2. "Right. Cancelled. What alarm was it? The Sunday one?" (1 of 9) was spoken: a question mark made any reply honest, and a sentence that is only "Cancelled." is a claim like "Done." (ANNOUNCES knew only done / handled).
"""
from __future__ import annotations

import pytest

from brain import UNCLAIM, announces
from test_sat_fix4_text import make_brain, no_routes, text, untouched  # noqa: F401


@pytest.mark.parametrize("words", ["You're all set, Captain.", "The lamp was already on. You're all set.", "You are all set.", "It's all set already, monkey.",
                                   "I didn't cancel it because the pasta timer doesn't exist anymore, it already went off. You're all set."])
def test_review4_you_are_all_set_claims_nothing(words):
    assert not announces(words)


@pytest.mark.parametrize("words", ["All set.", "All set, Captain.", "Cancelled.", "Right. Cancelled. What alarm was it? The Sunday one?", "Removed.", "Done.", "Deleted!"])
def test_review4_a_bare_claim_is_a_claim_even_with_a_question_after_it(words):
    assert announces(words)


@pytest.mark.parametrize("words", ["Nothing was cancelled, monkey.", "I did not cancel anything, sorry.", "No alarm was removed.", "Cancelled? Which one, Captain?"])
def test_review4_a_retraction_or_a_question_is_still_no_claim(words):
    assert not announces(words)


def test_review4_the_honest_answer_with_an_all_set_is_spoken(tmp_path, no_routes):
    said = "I didn't cancel it because the pasta timer doesn't exist anymore, it already went off. You're all set."
    b, model = make_brain(tmp_path, [text("Timer cancelled.")], [text(said)])
    assert b.reply("my pasta timer already went off") == said and untouched(b)


def test_review4_a_bare_cancelled_with_a_question_is_not_spoken(tmp_path, no_routes):
    b, model = make_brain(tmp_path, [text("Alarm cancelled.")], [text("Right. Cancelled. What alarm was it? The Sunday one?")])
    assert b.reply("I do not need the alarm on sunday") == UNCLAIM and untouched(b)
