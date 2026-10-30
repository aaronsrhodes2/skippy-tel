"""Heard speech (Hands-off as a translator, the Captain 2026-10-29): someone near him speaks another language, Whisper on skippy-pc
translates it, and the chat writes it down as a `heard` turn. It is kept with the chat, and it reaches the model ahead of his next
message as context, never as his own words and never as a turn of its own."""
import pytest

pytest.importorskip("flask")

import chat  # noqa: E402
import chat_history  # noqa: E402
import tabletop  # noqa: E402

HEARD = {"role": "heard", "content": "Where is the train station?", "lang": "es"}


def test_heard_lines_ride_ahead_of_the_next_message_as_context():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Obviously."}, HEARD]
    got = chat.build_messages(history, "what did she say?")
    assert [m["role"] for m in got] == ["user", "assistant", "user"]                 # still strictly alternating
    last = got[-1]["content"]
    assert last.startswith("[Heard near me") and "not me" in last
    assert "- (es) Where is the train station?" in last and last.endswith("what did she say?")


def test_heard_before_an_earlier_question_goes_with_that_question():
    history = [HEARD, {"role": "user", "content": "translate that"}, {"role": "assistant", "content": "She asked for the station."}]
    got = chat.build_messages(history, "thanks")
    assert "train station" in got[0]["content"] and got[0]["content"].endswith("translate that")
    assert got[-1] == {"role": "user", "content": "thanks"}                            # nothing new heard: the message as typed


def test_a_bad_language_tag_and_empty_or_endless_heard_lines_are_tamed():
    junk = [{"role": "heard", "content": "x" * 5000, "lang": "<script>"}, {"role": "heard", "content": "  "}] + \
           [{"role": "heard", "content": f"line {i}", "lang": "fr"} for i in range(30)]
    last = chat.build_messages(junk, "ok")[-1]["content"]
    assert "<script>" not in last and "line 29" in last and "line 19" not in last   # the last ten only
    assert len(last) <= chat.MAX_CHARS


def test_a_kept_chat_keeps_its_heard_lines_and_their_language():
    h = chat_history.History(tabletop.MemoryStore())
    h.save("friend@example.com", "abc123", "t", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"},
                                                 {**HEARD, "secret": 1}])
    (c,) = h.chats("friend@example.com")
    assert c["history"][-1] == HEARD


def test_a_chat_of_only_heard_lines_is_not_kept():
    h = chat_history.History(tabletop.MemoryStore())
    with pytest.raises(chat_history.Refused):
        h.save("friend@example.com", "abc123", "t", [HEARD])
