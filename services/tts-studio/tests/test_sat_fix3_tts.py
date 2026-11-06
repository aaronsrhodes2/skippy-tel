"""tts-studio, the third review of the house speed change (2026-11-05, evening), the pin of problem U8 (fix3:small): where a reply of exactly the batch length goes.

chunks_whole(text, limit) packs sentences into runs of at most `limit` characters (F5's own batch size for the reference: a run costs a whole pass over the 9 s reference, so a reply
that fits one batch is ONE run). The boundary is `<= limit`: a reply of EXACTLY `limit` characters is one run, one character more is two. The review tried `<` in a scratch copy and no
test noticed (the other tests use replies far under or far over the limit): a reply of exactly 192 characters would have cost a second run, 0.5 to 1 s, for no reason. With these tests
17 of the 19 fail on `<` (and on `<= limit + 1`, and on a sum that forgets the joining space); the other 2 are the controls (one sentence is one run, whatever its length).

Self-contained (its own voice that records what it is asked to say). No GPU, no model: the real chunks_whole, the real /speak, and the real Skippy.batch_chars with its model unloaded.
numpy is needed only by the 6 tests that make audio (marked needs_numpy); the 13 chunks_whole tests are pure Python and run in CI's python:3.12-slim image too (the fourth review, 2026-11-06: the
whole file used to skip there, so the boundary pins never ran in CI)."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")
try:
    import numpy as np
except ImportError:                    # CI's python:3.12-slim has none: only the tests that make audio skip (chunks_whole is pure Python, and app.py imports numpy only when a voice renders)
    np = None
needs_numpy = pytest.mark.skipif(np is None, reason="numpy is not installed: this test makes audio (CI's services image has none; the pure chunks_whole tests run there)")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as studio  # noqa: E402


def sentence(n, letter):
    """A sentence of exactly n characters: n - 1 letters and the full stop. (The packing counts characters, so what the words are does not matter.)"""
    assert n >= 2
    return letter * (n - 1) + "."


# --- the function: sentences packed up to the limit, the limit included -----------------------------------------------------------------------------------

@pytest.mark.parametrize("limit", [30, 100, 180, 186, 192, 260, 500])
def test_fix3tts_u8_two_sentences_that_fill_the_batch_exactly_are_one_run_and_one_character_more_is_two(limit):
    first = sentence(limit // 2, "a")
    exact = sentence(limit - len(first) - 1, "b")                              # first + " " + exact is `limit` characters
    assert len(f"{first} {exact}") == limit
    assert studio.chunks_whole(f"{first} {exact}", limit) == [f"{first} {exact}"]                      # exactly the batch: ONE run
    one_less = sentence(len(exact) - 1, "b")
    assert studio.chunks_whole(f"{first} {one_less}", limit) == [f"{first} {one_less}"]               # under it: one run
    one_more = sentence(len(exact) + 1, "b")
    assert len(f"{first} {one_more}") == limit + 1
    assert studio.chunks_whole(f"{first} {one_more}", limit) == [first, one_more]                      # one character over: two runs, cut between the sentences


@pytest.mark.parametrize("limit", [40, 186, 192])
def test_fix3tts_u8_three_sentences_the_same_way(limit):
    a, b = sentence(limit // 4, "a"), sentence(limit // 4, "b")
    c = sentence(limit - len(a) - len(b) - 2, "c")                             # a + " " + b + " " + c is `limit` characters
    assert len(f"{a} {b} {c}") == limit
    assert studio.chunks_whole(f"{a} {b} {c}", limit) == [f"{a} {b} {c}"]
    c_more = sentence(len(c) + 1, "c")
    assert studio.chunks_whole(f"{a} {b} {c_more}", limit) == [f"{a} {b}", c_more]                     # the one that does not fit starts the next run, and the first two stay together


def test_fix3tts_u8_a_run_that_is_full_to_the_limit_does_not_swallow_the_next_sentence():
    a, b, c = sentence(60, "a"), sentence(60, "b"), sentence(10, "c")
    assert studio.chunks_whole(f"{a} {b} {c}", 121) == [f"{a} {b}", c]         # a + b is exactly 121: full
    assert studio.chunks_whole(f"{a} {b} {c}", 120) == [a, f"{b} {c}"]         # 120: b cannot join a, and then b + c (71) is one run
    assert studio.chunks_whole(f"{a} {b} {c}", 132) == [f"{a} {b} {c}"]        # a + b + c is exactly 132


@pytest.mark.parametrize("limit", [30, 186])
def test_fix3tts_u8_one_sentence_is_one_run_whatever_its_length_it_is_never_cut(limit):
    for n in (limit - 1, limit, limit + 1, 3 * limit):
        one = sentence(n, "x")
        assert studio.chunks_whole(one, limit) == [one]


# --- the service: /speak with "whole" -----------------------------------------------------------------------------------------------------------------

class Voice:
    """A voice that records every chunk it is asked to say, and has a batch size of its own."""

    def __init__(self, batch_chars):
        self.batch_chars, self.said = batch_chars, []

    def say(self, text, seed=None):
        self.said.append(text)
        return np.full(2400, 0.1, "float32")


def speak(monkeypatch, voice, text):
    monkeypatch.setattr(studio, "files", lambda: {"stock": True, "skippy": True})
    monkeypatch.setattr(studio, "line_file", lambda v: __file__)               # (the warm voice's one file is there)
    monkeypatch.setattr(studio, "RESIDENT", ["skippy"])
    monkeypatch.setattr(studio, "_warm", {"skippy"})
    monkeypatch.setattr(studio, "_models", {"skippy": voice})
    monkeypatch.setattr(studio, "mp3", lambda wav: b"mp3")
    r = studio.app.test_client().post("/speak", json={"text": text, "voice": "skippy", "whole": True})
    r.get_data()
    r.close()                                                                  # a streamed reply gives the studio's lock back on close
    assert r.status_code == 200 and not studio._lock.locked()
    return voice.said


@needs_numpy
@pytest.mark.parametrize("limit", [60, 186, 400])
def test_fix3tts_u8_a_reply_of_exactly_the_voices_batch_size_is_one_run_through_speak_and_one_character_more_is_two(monkeypatch, limit):
    first = sentence(limit // 2, "a")
    exact = f"{first} {sentence(limit - len(first) - 1, 'b')}"
    assert len(exact) == limit
    assert speak(monkeypatch, Voice(limit), exact) == [exact]
    over = f"{first} {sentence(limit - len(first), 'b')}"
    assert len(over) == limit + 1
    assert speak(monkeypatch, Voice(limit), over) == [first, over[len(first) + 1:]]


@needs_numpy
@pytest.mark.parametrize("prepared", [{"max_chars": 192}, {"max_chars": 120}, None])
def test_fix3tts_u8_the_skippy_voices_own_batch_size_is_the_limit_it_is_packed_to(monkeypatch, prepared):
    """The real Skippy.batch_chars (the F5 reference's batch size, or its fallback when the reference was not prepared), the model never loaded: a reply of that exact length is one say()."""
    voice = studio.Skippy.__new__(studio.Skippy)                               # (without loading a model)
    voice.prepared, voice.said = prepared, []
    voice.say = lambda text, seed=None: (voice.said.append(text), np.full(2400, 0.1, "float32"))[1]
    limit = voice.batch_chars
    assert 100 <= limit <= 400
    first = sentence(limit // 2, "a")
    exact = f"{first} {sentence(limit - len(first) - 1, 'b')}"
    assert len(exact) == limit
    assert speak(monkeypatch, voice, exact) == [exact]
    voice.said.clear()
    over = f"{first} {sentence(limit - len(first), 'b')}"
    assert speak(monkeypatch, voice, over) == [first, over[len(first) + 1:]]
