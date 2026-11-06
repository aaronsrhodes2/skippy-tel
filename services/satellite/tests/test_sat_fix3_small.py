"""The third review of the house speed change (2026-11-05, evening), the small pins (fix3:small): behaviour that was right and that nothing pinned. The tests below pass on the code as it
is, and the ones that pin a rule FAIL on the one change that takes the rule out (the review's surviving mutants T4, T11 and X3, tried in a scratch copy of the tree: 25, 67 and 16 of the
tests here fail on them, and the 1,800 older tests of the satellite suite pass on every one of the three). The others are controls: the other side of each boundary, which must keep passing.

T4   strip_stage_directions, the title rule (`_titled`): "the first word of the span must start with a capital letter too". It matters when that first word is a small joining word
     ("the", "a", "in", "and" ...), which the count of the capitalised words does not look at: "*the Careless Whisper*" has a lower-case "the", so it is a sentence (a direction,
     deleted, because "whisper" is a direction word), where "*The Careless Whisper*" is a song and is spoken. Without the first-word clause both are songs.
T11  the same function, the exclusion `_NOT_ACTIONS`: a span of two words or more whose first word ends in -s, -ing or -ed is taken for an action ("*rolls eyes*", "*adjusts the dial*")
     unless that word is one of the words that only look like a verb ("this", "is", "was", "his", "its", "yes", "plus", "less", "morning", "being" ...). "Take *this one*, it is mine."
     must not lose "this one". Every word of the set has its own case here (a test that took its words from the set itself could not notice one being taken out of it).
X3   timers.spoken_length: a count of zero is no length of time ("zero minutes" -> None, not 0). A timer is between one second and a day (`Timers.add` refuses 0), so a label that says
     "zero minutes" is a NAME: cancelling "zero minutes" cancels the timer with that name. Read as a length it would be 0 seconds, match no timer and cancel nothing.

Self-contained (its own cases), so a change to another test file's helpers cannot break it."""
import pytest

import brain as brain_mod
from timers import Timers, spoken_length

strip = brain_mod.strip_stage_directions
SAID = "It is nine. {star}{span}{star} Fine."


# ═══ T4: the title rule needs the first word of the span in capitals too ═══
# (joining word in lower case first + capitalised words + a direction word at the end: verified one by one against the code)
LOWER_FIRST = ["the Careless Whisper", "a Dramatic Pause", "the Rolling Beat", "an Awkward Cough", "the Big Sigh", "a Slow Nod", "the Long Pause", "a Quick Beat",
               "in Rolling Beat", "to Careless Whisper", "and Dramatic Pause", "or Dramatic Sigh"]


@pytest.mark.parametrize("star", ["*", "**"])
@pytest.mark.parametrize("span", LOWER_FIRST)
def test_fix3small_t4_a_span_that_starts_in_lower_case_is_a_sentence_and_its_direction_word_makes_it_a_direction(span, star):
    """The same words in Title Case but led by a lower-case joining word are not a title: the direction word at the end decides, and the span is deleted."""
    assert strip(SAID.format(star=star, span=span)) == "It is nine. Fine."


@pytest.mark.parametrize("star", ["*", "**"])
@pytest.mark.parametrize("span", LOWER_FIRST)
def test_fix3small_t4_the_same_words_begun_with_a_capital_are_a_title_and_are_spoken(span, star):
    """The other side of the boundary: one letter (the first of the span) turns the direction into a title, and a title's words are all spoken."""
    title = span[0].upper() + span[1:]
    assert title != span
    assert strip(SAID.format(star=star, span=title)) == f"It is nine. {title} Fine."


@pytest.mark.parametrize("span", ["an Awkward Silence", "the Living Room", "of Mice and Men", "the Rolling Stones", "the Wall", "a Quiet Night"])
def test_fix3small_t4_a_lower_case_start_with_no_direction_word_in_it_is_spoken_as_ever(span):
    """Control: it is the direction word (or an action verb first) that makes a lower-case-led span a direction, never the lower case alone."""
    assert strip(SAID.format(star="*", span=span)) == f"It is nine. {span} Fine."


def test_fix3small_t4_where_the_span_stands_in_the_reply_does_not_change_it():
    assert strip("*the Careless Whisper* Fine, then.") == "Fine, then."
    assert strip("Fine, then. *a Dramatic Pause*") == "Fine, then."
    assert strip("Now playing *The Careless Whisper* on the Enclave.") == "Now playing The Careless Whisper on the Enclave."
    assert strip("Now playing *the Careless Whisper* on the Enclave.") == "Now playing on the Enclave."


# ═══ T11: the words that look like a verb and are none ═══
# Every word of the code's `_NOT_ACTIONS`, with a phrase a model would put between asterisks. All of them end in -s or -ing, so without the exclusion each phrase would be an "action".
NOT_ACTIONS_SPANS = {
    "this": "this one", "is": "is it", "was": "was it", "has": "has it", "as": "as if", "us": "us two", "his": "his kitchen", "its": "its door", "yes": "yes please", "plus": "plus tax",
    "less": "less sugar", "unless": "unless it rains", "always": "always on", "perhaps": "perhaps later", "sometimes": "sometimes yes", "besides": "besides that",
    "towards": "towards noon", "does": "does it", "thus": "thus far", "lots": "lots more", "nothing": "nothing else", "something": "something else", "anything": "anything else",
    "everything": "everything else", "morning": "morning light", "evening": "evening news", "during": "during dinner", "according": "according to", "concerning": "concerning that",
    "regarding": "regarding that", "being": "being honest", "having": "having said that", "doing": "doing fine", "saying": "saying hello",
}


@pytest.mark.parametrize("star", ["*", "**"])
@pytest.mark.parametrize("word", sorted(NOT_ACTIONS_SPANS))
def test_fix3small_t11_a_phrase_whose_first_word_only_looks_like_a_verb_keeps_all_its_words(word, star):
    span = NOT_ACTIONS_SPANS[word]
    assert strip(SAID.format(star=star, span=span)) == f"It is nine. {span} Fine."


def test_fix3small_t11_this_one_is_mine():
    assert strip("*this one* is mine.") == "this one is mine."
    assert strip("Take *this one*, it is mine.") == "Take this one, it is mine."
    assert strip("It is *his kitchen*, not mine.") == "It is his kitchen, not mine."


def test_fix3small_t11_every_word_the_code_excludes_has_a_case_here():
    """The cases above were written out by hand (not taken from the code's own set, which could not notice a word being taken out of it). A word added to the set later needs its case."""
    assert set(NOT_ACTIONS_SPANS) >= set(brain_mod._NOT_ACTIONS), sorted(set(brain_mod._NOT_ACTIONS) - set(NOT_ACTIONS_SPANS))


@pytest.mark.parametrize("span", ["rolls eyes", "adjusts the dial", "taps the can", "leans in", "raises an eyebrow", "clearing throat", "adjusting imaginary dial", "sighed heavily"])
def test_fix3small_t11_the_other_side_an_action_verb_first_is_still_a_direction(span):
    """Control: the exclusion lets nothing through that is a real action (the clause it guards still deletes these)."""
    assert strip(SAID.format(star="*", span=span)) == "It is nine. Fine."


# ═══ X3: a count of zero is no length of time ═══
ZERO_COUNTS = ["zero minutes", "0 minutes", "zero seconds", "0 seconds", "zero hours", "0 hours", "Zero Minutes", "a zero minute", "the 0 minute", "0 min", "zero secs", "0 hrs", "zero second",
               "00 seconds"]
LENGTHS = {"one minute": 60, "5 minutes": 300, "Ninety-Second": 90, "an hour": 3600, "a second": 1, "twenty five minutes": 1500, "ten secs": 10, "1 hr": 3600, "nineteen minutes": 1140,
           "a five minute": 300, "the 5 minute": 300}


@pytest.mark.parametrize("words", ZERO_COUNTS)
def test_fix3small_x3_zero_of_anything_is_no_length(words):
    assert spoken_length(words) is None                       # (a timer is between one second and a day: 0 would be a length no timer has)


@pytest.mark.parametrize("words, seconds", sorted(LENGTHS.items(), key=lambda kv: kv[0]))
def test_fix3small_x3_a_real_length_is_still_read(words, seconds):
    assert spoken_length(words) == seconds


def test_fix3small_x3_what_is_not_a_length_is_none_as_ever():
    for words in ("pasta", "second", "minutes", "half an hour", "five", "five pasta", "", None, "twenty zero minutes"):
        assert spoken_length(words) is None, words


def test_fix3small_x3_a_timer_named_zero_minutes_is_cancelled_by_that_name_and_nothing_else_goes():
    now = [0.0]
    timers = Timers(clock=lambda: now[0])
    timers.add(300, "zero minutes")
    timers.add(300)                                           # (named "5 minutes")
    timers.add(60, "pasta")
    assert timers.cancel("zero minutes") == 1
    assert sorted(label for label, _ in timers.remaining()) == ["5 minutes", "pasta"]
    timers.add(30, "0 seconds")
    assert timers.cancel("0 seconds") == 1
    assert sorted(label for label, _ in timers.remaining()) == ["5 minutes", "pasta"]


def test_fix3small_x3_a_zero_length_never_cancels_the_timers_of_a_real_length():
    now = [0.0]
    timers = Timers(clock=lambda: now[0])
    timers.add(300)
    timers.add(1)
    assert timers.cancel("zero minutes") == 0 and timers.cancel("0 seconds") == 0
    assert sorted(seconds for _, seconds in timers.remaining()) == [1, 300]
