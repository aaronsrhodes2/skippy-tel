"""tts-studio's text side, without a GPU: what F5 is handed (numbers as words), how a reply is cut for time to first audio,
and the routes' refusals. The voices themselves are timed by tools/tts_bench.py against the real service."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as studio  # noqa: E402


def test_numbers_are_words_for_f5():
    pytest.importorskip("num2words")
    assert studio.say_numbers("At 0.9 c it is 2.294, so 44% of 1,284 ticks.") == \
        "At zero point nine c it is two point two nine four, so forty-four percent of one thousand, two hundred and eighty-four ticks."
    assert studio.say_numbers("No numbers here.") == "No numbers here."


def test_the_first_chunk_is_one_sentence_and_the_rest_are_packed():
    text = "Done. The tests pass and the pill is green. " + " ".join(f"Sentence number {i} is here." for i in range(20))
    parts = studio.chunks(text)
    assert parts[0] == "Done. The tests pass and the pill is green."          # a two-word first sentence takes its neighbour along
    assert all(len(p) <= 260 for p in parts[1:]) and len(parts) < 10
    assert " ".join(parts) == " ".join(text.split())
    assert studio.chunks("  One line without a stop  ") == ["One line without a stop"]


def test_speak_refuses_what_it_should_without_touching_a_model(monkeypatch):
    monkeypatch.setattr(studio, "ready", lambda: {"stock": False, "skippy": False})
    c = studio.app.test_client()
    assert c.post("/speak", json={"text": "hi", "voice": "elevenlabs"}).status_code == 400
    assert c.post("/speak", json={"text": " ", "voice": "stock"}).status_code == 400
    assert c.post("/speak", json={"text": "x" * 1001, "voice": "stock"}).status_code == 413
    assert c.post("/speak", json={"text": "hi", "voice": "skippy"}).status_code == 503       # files missing: says so, loads nothing
    assert not studio._models and c.get("/health").get_json()["ready"] is False
