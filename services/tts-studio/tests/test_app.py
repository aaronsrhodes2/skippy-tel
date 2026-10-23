"""tts-studio's text side, without a GPU: what F5 is handed (numbers as words), how a reply is cut for time to first audio,
and the routes' refusals. The voices themselves are timed by tools/tts_bench.py against the real service."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as studio  # noqa: E402


@pytest.mark.parametrize("text, said", [
    ("The tests pass. All 1,284 of them.", "The tests pass. All one thousand two hundred eighty-four of them."),
    ("Travelling at 0.9 c, your clock runs at 44 percent speed.",
     "Travelling at zero point nine times the speed of light, your clock runs at forty-four percent speed."),
    ("Escape speed is 11.19 km/s.", "Escape speed is eleven point one nine kilometres per second."),
    ("Disk is 91% full; Ollama holds 7.8 GB.", "Disk is ninety-one percent full; Ollama holds seven point eight gigabytes."),
    ("It is 1 km away, 4x faster, the 3rd try, the 21st.", "It is one kilometre away, four times faster, the third try, the twenty-first."),
    ("Save $12.50, or $1. It is -3 °C.", "Save twelve dollars and fifty cents, or one dollar. It is minus three degrees Celsius."),
    ("In 2026, 1905 and 2005. Agent 007.", "In twenty twenty-six, nineteen oh five and two thousand five. Agent zero zero seven."),
    ("Wait 3-5 minutes. On 2026-10-23 at 13:19.", "Wait three to five minutes. On October twenty-third, twenty twenty-six at thirteen nineteen."),
    ("Version 1.2.3 is out. 1,000,000 stars.", "Version one point two point three is out. one million stars."),
    ("It saves you 12 cents and 37 minutes, 3 players.", "It saves you twelve cents and thirty-seven minutes, three players."),
    ("No numbers here, Captain.", "No numbers here, Captain."),
])
def test_numbers_are_words_for_f5(text, said):
    assert studio.say_numbers(text) == said


def test_cardinals_and_ordinals():
    import spoken_numbers as sn
    assert sn.cardinal(0) == "zero" and sn.cardinal(40) == "forty" and sn.cardinal(1_002_003) == "one million two thousand three"
    assert sn.ordinal(12) == "twelfth" and sn.ordinal(20) == "twentieth" and sn.ordinal(53) == "fifty-third"


def test_tags_and_markdown_never_reach_a_voice_and_f5_gets_a_closing_stop():
    assert studio.clean("[whispers] Hello **there**  `x`") == "Hello there x"
    assert studio.clean("[sarcastic]") == ""
    assert studio.for_f5("Hello there 2 times") == "Hello there two times." and studio.for_f5("Done!") == "Done!"


def test_the_first_chunk_is_one_sentence_and_the_rest_are_packed():
    text = "Done. The tests pass and the pill is green. " + " ".join(f"Sentence number {i} is here." for i in range(20))
    parts = studio.chunks(text)
    assert parts[0] == "Done. The tests pass and the pill is green."          # a two-word first sentence takes its neighbour along
    assert all(len(p) <= 260 for p in parts[1:]) and len(parts) < 10
    assert " ".join(parts) == " ".join(text.split())
    assert studio.chunks("  One line without a stop  ") == ["One line without a stop"]


def test_speak_refuses_what_it_should_without_touching_a_model(monkeypatch):
    monkeypatch.setattr(studio, "files", lambda: {"stock": False, "skippy": False})
    c = studio.app.test_client()
    assert c.post("/speak", json={"text": "hi", "voice": "elevenlabs"}).status_code == 400
    assert c.post("/speak", json={"text": " ", "voice": "stock"}).status_code == 400
    assert c.post("/speak", json={"text": "[whispers]", "voice": "stock"}).status_code == 400              # only a tag: nothing to say
    assert c.post("/speak", json={"text": "x" * 1001, "voice": "stock"}).status_code == 413
    assert c.post("/speak", json={"text": "hi", "voice": "skippy"}).status_code == 503       # files missing: says so, loads nothing
    assert not studio._models and c.get("/health").get_json()["ready"] is False


def test_a_resident_voice_is_not_ready_until_it_is_warm_so_the_gate_falls_back_meanwhile(monkeypatch):
    monkeypatch.setattr(studio, "files", lambda: {"stock": True, "skippy": True})
    monkeypatch.setattr(studio, "RESIDENT", ["skippy"])
    monkeypatch.setattr(studio, "_warm", set())
    assert studio.ready() == {"stock": True, "skippy": False}                 # stock loads on demand; skippy is still loading
    c = studio.app.test_client()
    assert c.post("/speak", json={"text": "hi", "voice": "skippy"}).status_code == 503
    studio._warm.add("skippy")
    assert studio.ready()["skippy"] and c.get("/health").get_json()["resident"] == ["skippy"]


def test_preload_warms_the_resident_voices_and_a_failure_is_reported_not_raised(monkeypatch):
    said = []

    class Fine:
        def say(self, text):
            said.append(text)

    class Broken:
        def __init__(self):
            raise RuntimeError("no cuda")

    monkeypatch.setattr(studio, "files", lambda: {"stock": True, "skippy": True})
    monkeypatch.setattr(studio, "VOICES", {"stock": Broken, "skippy": Fine})
    monkeypatch.setattr(studio, "RESIDENT", ["stock", "skippy"])
    monkeypatch.setattr(studio, "_models", {})
    monkeypatch.setattr(studio, "_warm", set())
    monkeypatch.setattr(studio, "_status", {"vram_reserved_gib": None, "errors": {}})
    monkeypatch.setattr(studio, "reserve", lambda gb: gb)
    studio.preload()
    assert studio._warm == {"skippy"} and said == [studio.WARMUP] and "no cuda" in studio._status["errors"]["stock"]
    assert studio._status["vram_reserved_gib"] == studio.VRAM_GB and not studio._lock.locked()
