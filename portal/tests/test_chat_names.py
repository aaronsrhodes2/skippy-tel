"""Chat names (chat_names.py): four to six words, like a short sentence, from the first prompt, by Skippy Core, with a fallback; the portal and
the bridge carry the same file; a portal chat is named once, on its first save."""
from pathlib import Path

import pytest

import chat_names
import chat_history

HERE = Path(__file__).resolve().parents[1]


def test_the_portal_and_the_bridge_carry_the_same_file():
    bridge = HERE.parent / "services" / "claude-bridge" / "chat_names.py"
    assert (HERE / "chat_names.py").read_bytes() == bridge.read_bytes()


@pytest.mark.parametrize("reply, name", [
    ("voice-needs-simpler-modes", "voice-needs-simpler-modes"),
    ("<think>maybe voice-is-a-mess</think>\nvoice-needs-simpler-modes", "voice-needs-simpler-modes"),
    ("Name: Portal-Needs-Faster-Pictures.", "portal-needs-faster-pictures"),
    ("Sure! I'd call it that", None),
    ("three-word-name", None),
    ("chat-names-get-longer-and-smarter", "chat-names-get-longer-and-smarter"),
    ("pod-face-streams-smoothly-from-pc", "pod-face-streams-smoothly-from-pc"),
    ("seven-words-is-one-too-many-here", None),
])
def test_the_name_is_read_from_the_reply(reply, name):
    assert chat_names.from_reply(reply) == name


def test_core_names_it_and_without_core_it_is_the_first_content_words():
    asked = []
    got = chat_names.name("The voice keys are a mess", lambda system, text: asked.append(text) or "voice-needs-simpler-modes")
    assert got == "voice-needs-simpler-modes" and asked[0].endswith("/no_think") and "noun-verb-adjective-noun" in chat_names.SYSTEM and "six" in chat_names.SYSTEM
    assert chat_names.name("The voice keys are a mess", lambda s, t: 1 / 0) == "voice-keys-mess-chat"
    assert chat_names.fallback("Please make the portal chat names longer and smarter for phones") == "portal-chat-names-longer-smarter"
    assert chat_names.name("hi", lambda s, t: "nope") == "chat-chat-chat-chat"
    assert chat_names.valid(chat_names.fallback("Can you make me a picture of a red barn at dusk?"))


def test_a_portal_chat_is_named_once_and_keeps_it():
    names = iter(["barn-gets-red-picture", "should-never-be-used"])
    h = chat_history.History(namer=lambda message: next(names))
    turns = [{"role": "user", "content": "draw a red barn"}, {"role": "assistant", "content": "Done."}]
    assert h.save("a@b.c", "abc123", "draw a red barn", turns)["title"] == "barn-gets-red-picture"
    assert h.save("a@b.c", "abc123", "draw a red barn", turns + turns)["title"] == "barn-gets-red-picture"
    assert [c["title"] for c in h.chats("a@b.c")] == ["barn-gets-red-picture"]
