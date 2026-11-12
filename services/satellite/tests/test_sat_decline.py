"""A no to something Skippy just asked (the Captain, 2026-10-31: "short and to the point, 'Okay, no problem Captain' ... without a whole turn"):
one pre-recorded line, no brain, and the conversation ends; anything else, or a no to no question, still goes to the brain."""
import asyncio
import json
import time

import filler
import hub
import pytest
from hub import IDLE, FOLLOWUP, Room
from timers import Timers
from wake import is_decline
from test_sat_starters import Brain, Gate, Link


def room(tmp_path, quips=True):
    bank = None
    if quips:
        (tmp_path / "bank" / "decline").mkdir(parents=True)
        (tmp_path / "bank" / "decline" / "a.mp3").write_bytes(b"mp3")
        index = {"decline": [{"file": "decline/a.mp3", "text": "Fine. Your loss."}], "thinking": []}
        (tmp_path / "bank" / "index.json").write_text(json.dumps(index), encoding="utf-8")
        bank = filler.FillerBank.load(tmp_path / "bank")
    link, brain = Link(), Brain()
    r = Room("office", link, Gate(), brain, Timers(), log=lambda m: None, filler=bank)
    r.mode = FOLLOWUP
    return r, link, brain


@pytest.fixture(autouse=True)
def _one_stream(monkeypatch):
    monkeypatch.setattr(hub, "one_stream", lambda mp3, **k: mp3)


def asked(r, text="Want me to dig up more on that?", ago=5):
    r._last_said = (text, time.monotonic() - ago)


def test_a_no_to_a_question_is_one_quip_and_the_ears_close(tmp_path):
    r, link, brain = room(tmp_path)
    asked(r)
    asyncio.run(r.handle("Nah, I'm good, thanks."))
    assert brain.heard == [] and link.spoken == ["Fine. Your loss."] and r.mode == IDLE and link.released_n == 1
    assert not r.asked_just_now()                                      # the quip is now his last words: a second "no" is an ordinary command


def test_without_quips_it_still_says_one_short_line(tmp_path):
    r, link, brain = room(tmp_path, quips=False)
    asked(r)
    asyncio.run(r.handle("no"))
    assert brain.heard == [] and link.spoken == ["Okay, no problem, Captain."] and r.mode == IDLE and link.released_n == 1


def test_a_no_to_no_question_or_more_than_a_no_goes_to_the_brain(tmp_path):
    r, link, brain = room(tmp_path)
    asked(r, "The lights are off. Sleep well.")
    asyncio.run(r.handle("no"))
    asked(r, "Want some jazz?")
    asyncio.run(r.handle("No, play some Bowie instead."))
    asked(r, "Want some jazz?", ago=200)                               # asked too long ago
    asyncio.run(r.handle("nope"))
    assert brain.heard == ["no", "No, play some Bowie instead.", "nope"]


def test_a_no_to_a_routine_proposal_is_short_too(tmp_path):
    r, link, brain = room(tmp_path)

    class W:
        answers = []
        def answer(self, pid, a, words): self.answers.append(a)
    r.workspace, r.pending = W(), {"id": "p1", "weekday": "Monday", "time": "7:00"}
    asyncio.run(r.handle("no thanks"))
    assert W.answers == ["no"] and link.spoken == ["Fine. Your loss."] and brain.heard == [] and r.mode == IDLE


def test_the_decline_words():
    for t in ("No.", "nope", "No, thanks.", "No, that's okay, thanks.", "not now", "Maybe later.", "I'm good", "pass", "Um, no thank you, Skippy."):
        assert is_decline(t), t
    for t in ("No, play jazz instead", "no I want the weather", "yes", "not now, turn on the lights", "know what?", "nobody asked"):
        assert not is_decline(t), t
