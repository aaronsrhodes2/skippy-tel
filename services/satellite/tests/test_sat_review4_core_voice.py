"""review4:core: a voice request has no home since the persona's annotation sections were cut (house_persona, core3#9).

"Keyword Memory" and "TTS Voice Adjustment" used to catch "your voice is too fast" with an annotation the house stripped. Without them the model looks for a tool: on the real Core, 1 of 9 voice-speed requests
("your voice is too fast, slow it down") called the audiobook tool's `speed` (0.8) and said "I just lowered the audiobook playback speed": a persisted change to the book player, not the voice. The house prompt now says a
tool does not change its voice, and the audiobook tool's `speed` says it is the BOOK's.
"""
from __future__ import annotations

import audiobooks
import brain as brain_mod


def test_review4_the_audiobook_speed_is_the_books_not_his_voice():
    speed = next(t for t in audiobooks.BOOK_TOOLS if t["name"] == "audiobook")["input_schema"]["properties"]["speed"]["description"]
    assert "BOOK" in speed and "voice" in speed


def test_review4_the_house_prompt_says_a_tool_cannot_change_its_voice():
    assert "voice" in brain_mod.SPOKEN and "call no tool" in brain_mod.SPOKEN and "book only" in brain_mod.SPOKEN
