"""tts-studio, the fixes after the adversarial review of the speed change (2026-11-05): what ready() says about a warm voice whose files vanished (P15), the
speed/quality knob the fast path hands F5 (P13), and what "whole": true does to a reply of every length. No GPU here: F5 is a fake that records what it is handed;
the files the studio looks at are real files in a temporary folder, so nothing here mocks the disk."""
import importlib.util
import itertools
import os
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")
np = pytest.importorskip("numpy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as studio  # noqa: E402

ALL = ["stock", "skippy"]


def lay(root, stock, skippy):
    """The folders the studio reads (Kokoro, F5, Vocos, the reference) under `root`, with every file of a voice there, or none of them."""
    kokoro, f5, vocos, ref = (root / n for n in ("kokoro", "f5", "vocos", "ref"))
    for d in (kokoro / "voices", f5, vocos, ref):
        d.mkdir(parents=True, exist_ok=True)
    if stock:
        for p in (kokoro / "kokoro-v1_0.pth", kokoro / "voices" / f"{studio.STOCK_VOICE}.pt"):
            p.write_bytes(b"x")
    if skippy:
        for p in (f5 / "model_1250000.safetensors", f5 / "vocab.txt", ref / "ref.wav", ref / "ref.txt", vocos / "config.yaml"):
            p.write_bytes(b"x")
    return {"KOKORO": kokoro, "F5_DIR": f5, "VOCOS": vocos, "REF": ref}


def point_at(monkeypatch, dirs):
    for name, folder in dirs.items():
        monkeypatch.setattr(studio, name, str(folder))


def subsets(items):
    return [list(c) for n in range(len(items) + 1) for c in itertools.combinations(items, n)]


def head_ready(files, resident, warm):
    """ready() as it was at git HEAD, before the speed change: a voice is ready when ALL its files are there and, if it is resident, it is warm."""
    return {v: ok and (v not in resident or v in warm) for v, ok in files.items()}


# --- P15: ready() --------------------------------------------------------------------------------------------------------------------------------------

def test_fixtts_ready_agrees_with_the_logic_before_the_speed_change_in_all_64_combinations(tmp_path, monkeypatch):
    """The review's differential: resident or not x warm or not x its files there or not, for both voices (4 x 4 x 4 = 64). 15 of them used to differ, every one
    of the kind 'resident, warm, and its files gone' (ready() said True without looking, F5's long way would then fail on a missing /ref/ref.wav)."""
    trees = {(s, k): lay(tmp_path / f"stock{int(s)}-skippy{int(k)}", s, k) for s in (True, False) for k in (True, False)}
    checked = 0
    for (s, k), dirs in trees.items():
        point_at(monkeypatch, dirs)
        for resident in subsets(ALL):
            for warm in subsets(ALL):
                monkeypatch.setattr(studio, "RESIDENT", resident)
                monkeypatch.setattr(studio, "_warm", set(warm))
                assert studio.ready() == head_ready({"stock": s, "skippy": k}, resident, set(warm)), (resident, warm, s, k)
                checked += 1
    assert checked == 64


def test_fixtts_a_warm_voice_whose_reference_vanished_is_not_ready_and_is_again_when_it_is_back(tmp_path, monkeypatch):
    dirs = lay(tmp_path, True, True)
    point_at(monkeypatch, dirs)
    monkeypatch.setattr(studio, "RESIDENT", ALL)
    monkeypatch.setattr(studio, "_warm", {"stock", "skippy"})
    c = studio.app.test_client()
    assert studio.ready() == {"stock": True, "skippy": True}
    (dirs["REF"] / "ref.wav").unlink()                                       # the bind mount lost the reference
    assert studio.ready() == {"stock": True, "skippy": False}
    assert c.get("/health").get_json()["voices"] == {"stock": True, "skippy": False}
    assert c.post("/speak", json={"text": "hi", "voice": "skippy"}).status_code == 503         # says so up front, instead of failing inside a streamed reply
    (dirs["REF"] / "ref.wav").write_bytes(b"x")
    assert studio.ready() == {"stock": True, "skippy": True}


def test_fixtts_a_warm_stock_voice_whose_voice_pack_vanished_is_not_ready_either(tmp_path, monkeypatch):
    dirs = lay(tmp_path, True, True)
    point_at(monkeypatch, dirs)
    monkeypatch.setattr(studio, "RESIDENT", ALL)
    monkeypatch.setattr(studio, "_warm", {"stock", "skippy"})
    (dirs["KOKORO"] / "voices" / f"{studio.STOCK_VOICE}.pt").unlink()      # (Kokoro is handed its pack as a path)
    assert studio.ready() == {"stock": False, "skippy": True}
    assert studio.app.test_client().post("/speak", json={"text": "hi", "voice": "stock"}).status_code == 503


def test_fixtts_a_warm_voice_costs_one_stat_each_not_seven_and_never_asks_files(tmp_path, monkeypatch):
    dirs = lay(tmp_path, True, True)
    point_at(monkeypatch, dirs)
    monkeypatch.setattr(studio, "RESIDENT", ALL)
    monkeypatch.setattr(studio, "_warm", {"stock", "skippy"})

    def no_seven_stats():
        raise AssertionError("files() is seven stat() calls on the bind mounts; a warm voice is in memory")
    monkeypatch.setattr(studio, "files", no_seven_stats)
    seen, real = [], os.path.isfile

    def counting(path):
        seen.append(Path(path).name)
        return real(path)
    monkeypatch.setattr(os.path, "isfile", counting)
    assert studio.ready() == {"stock": True, "skippy": True}
    monkeypatch.undo()
    assert sorted(seen) == sorted([f"{studio.STOCK_VOICE}.pt", "ref.wav"])      # the one file each voice's next line may still read: its pack, its reference


def test_fixtts_the_weights_are_in_memory_so_a_warm_voice_needs_only_what_a_line_may_still_read(tmp_path, monkeypatch):
    dirs = lay(tmp_path, True, True)
    point_at(monkeypatch, dirs)
    monkeypatch.setattr(studio, "RESIDENT", ALL)
    monkeypatch.setattr(studio, "_warm", {"stock", "skippy"})
    for gone in (dirs["F5_DIR"] / "model_1250000.safetensors", dirs["F5_DIR"] / "vocab.txt", dirs["REF"] / "ref.txt", dirs["VOCOS"] / "config.yaml",
                 dirs["KOKORO"] / "kokoro-v1_0.pth"):
        gone.unlink()                                                          # loaded at start: nothing reads them again
    assert studio.ready() == {"stock": True, "skippy": True}


def test_fixtts_a_voice_that_is_not_warm_resident_still_has_all_its_files_asked(tmp_path, monkeypatch):
    """Only the warm resident voices get the one-stat shortcut: a voice that loads on its first line, or is still loading, is judged by all its files."""
    dirs = lay(tmp_path, True, True)
    point_at(monkeypatch, dirs)
    monkeypatch.setattr(studio, "RESIDENT", ["skippy"])
    monkeypatch.setattr(studio, "_warm", {"skippy"})
    assert studio.ready() == {"stock": True, "skippy": True}
    (dirs["VOCOS"] / "config.yaml").unlink()                                   # skippy is warm: in memory
    (dirs["KOKORO"] / "kokoro-v1_0.pth").unlink()                              # stock loads on demand: its files decide
    assert studio.ready() == {"stock": False, "skippy": True}
    monkeypatch.setattr(studio, "_warm", set())                                # resident and still loading: not ready
    assert studio.ready() == {"stock": False, "skippy": False}


# --- P13: the speed/quality knob -----------------------------------------------------------------------------------------------------------------------

class F5:
    """The parts of f5_tts that Skippy.say touches, with a splitter modelled on F5's own chunker (split at clause punctuation, pack the pieces up to max_chars, never
    cut inside a clause; written from what f5-tts 1.1.x does, the real one needs torch and cannot run here). The tests below pin what THIS service hands it and renders:
    a run's audio is 8 samples per character of the text it was given, so 'nothing dropped' is a sum."""

    MAX_CHARS = 186

    def __init__(self):
        self.infers, self.batch_calls = [], []
        self.ema_model, self.vocoder, self.mel_spec_type, self.device = object(), object(), "vocos", "cuda"

    def infer(self, **kw):                                                     # F5TTS.infer: the long way
        self.infers.append(kw)
        return np.full(8 * len(kw["gen_text"]), 0.5, "float32"), 24000, None

    @staticmethod
    def chunk_text(text, max_chars):
        pieces = [p for p in re.split(r"(?<=[;:,.!?])\s+", text.strip()) if p]
        out = []
        for p in pieces:
            if out and len(out[-1]) + 1 + len(p) <= max_chars:
                out[-1] += " " + p
            else:
                out.append(p)
        return out

    def prepared(self):
        def batches(ref, ref_text, texts, model, vocoder, **kw):
            self.batch_calls.append({"texts": list(texts), **kw})
            yield np.full(8 * len(" ".join(texts)), 0.25, "float32"), 24000, None

        return {"chunk_text": self.chunk_text, "batches": batches, "seed_everything": lambda seed: None, "audio": object(), "sr": 24000,
                "ref_text": "Prepared reference words. ", "max_chars": self.MAX_CHARS}


def skippy_on(f5, prepared=True):
    s = studio.Skippy.__new__(studio.Skippy)                                   # (without loading a model)
    s.m, s.ref_text = f5, "Reference words."
    s.prepared = f5.prepared() if prepared else None
    return s


def test_fixtts_the_fast_path_hands_f5_the_nfe_knob_it_is_not_a_constant(monkeypatch):
    f5 = F5()
    s = skippy_on(f5)
    for nfe in (7, 11, 16):
        monkeypatch.setattr(studio, "F5_NFE", nfe)
        s.say("Hello there.", seed=1)
        assert f5.batch_calls[-1]["nfe_step"] == nfe
    s.prepared = None                                                          # the long way takes the same knob
    monkeypatch.setattr(studio, "F5_NFE", 13)
    s.say("Hello there.", seed=1)
    assert f5.infers[-1]["nfe_step"] == 13


def fresh_copy(monkeypatch, nfe=None):
    """app.py loaded again as its own module, with F5_NFE in the environment (or not): the knob as the container's compose file sets it."""
    if nfe is None:
        monkeypatch.delenv("F5_NFE", raising=False)
    else:
        monkeypatch.setenv("F5_NFE", nfe)
    spec = importlib.util.spec_from_file_location("studio_fresh_copy", Path(studio.__file__))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fixtts_the_knob_comes_from_the_environment_defaults_to_16_and_health_says_it(monkeypatch):
    assert fresh_copy(monkeypatch).F5_NFE == 16                                # the Captain's chosen quality (16 flow steps)
    nine = fresh_copy(monkeypatch, "9")
    assert nine.F5_NFE == 9 and nine.app.test_client().get("/health").get_json()["f5_nfe"] == 9


# --- whole mode: a reply of every length ----------------------------------------------------------------------------------------------------------------

@pytest.fixture
def studio_up(tmp_path, monkeypatch):
    """The Skippy voice resident and warm, its files there, the MP3 replaced by a marker (the number of samples in the run, 4 bytes): the body is one marker per run."""
    point_at(monkeypatch, lay(tmp_path, True, True))
    monkeypatch.setattr(studio, "RESIDENT", ["skippy"])
    monkeypatch.setattr(studio, "_warm", {"skippy"})
    monkeypatch.setattr(studio, "_models", {})
    monkeypatch.setattr(studio, "mp3", lambda wav: len(wav).to_bytes(4, "big"))
    return studio.app.test_client()


def speak(client, voice, model, body):
    studio._models[voice] = model
    r = client.post("/speak", json={"voice": voice, **body})
    data = r.get_data()
    r.close()                                                                  # a streamed reply gives the studio's lock back on close
    assert r.status_code == 200 and not studio._lock.locked()
    return [int.from_bytes(data[i:i + 4], "big") for i in range(0, len(data), 4)]


ORDINALS = "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth".split()
PLACES = "porch kitchen hall garage attic cellar study patio den loft shed yard".split()


def test_fixtts_whole_a_short_reply_is_one_run_and_the_default_is_one_per_sentence(studio_up):
    text = "The bedroom lamp is on and the porch light is dim. I left the kitchen alone, since you did not ask."
    assert len(text) < F5.MAX_CHARS
    f5 = F5()
    runs = speak(studio_up, "skippy", skippy_on(f5), {"text": text, "whole": True})
    assert len(runs) == 1 and len(f5.batch_calls) == 1 and f5.batch_calls[0]["texts"] == [text]
    assert runs == [8 * len(text)]                                             # all of it was rendered
    f5 = F5()
    assert len(speak(studio_up, "skippy", skippy_on(f5), {"text": text})) == 2          # without "whole": a run per sentence, as before


def test_fixtts_whole_a_reply_longer_than_the_batch_is_several_runs_in_order_none_dropped(studio_up):
    text = " ".join(f"The {o} light, the one in the {p}, is on and it stays on for the night." for o, p in zip(ORDINALS, PLACES))
    assert 2 * F5.MAX_CHARS < len(text) <= studio.MAX_TEXT
    f5 = F5()
    runs = speak(studio_up, "skippy", skippy_on(f5), {"text": text, "whole": True})
    handed = [t for call in f5.batch_calls for t in call["texts"]]
    assert len(runs) == len(f5.batch_calls) > 1                                 # several runs, not one giant one
    assert all(len(" ".join(call["texts"])) <= F5.MAX_CHARS for call in f5.batch_calls)       # none longer than F5's batch
    assert " ".join(handed) == text                                            # in order, nothing dropped or cut
    assert sum(runs) == 8 * sum(len(" ".join(call["texts"])) for call in f5.batch_calls) and all(r > 0 for r in runs)


def test_fixtts_whole_a_sentence_longer_than_the_batch_is_one_run_that_f5_splits_inside_never_dropped_or_cut(studio_up):
    clauses = ", ".join(f"the {p} light is on in the {o} room" for o, p in zip(ORDINALS, PLACES))
    sentence = f"Right now {clauses}."
    assert F5.MAX_CHARS * 1.5 < len(sentence) < studio.MAX_TEXT and sentence.count(". ") == 0
    f5 = F5()
    runs = speak(studio_up, "skippy", skippy_on(f5), {"text": sentence, "whole": True})
    assert len(runs) == 1 and len(f5.batch_calls) == 1                         # (one run of the studio: the sentence is not cut at a character)
    batches = f5.batch_calls[0]["texts"]
    assert len(batches) > 1 and all(len(b) <= F5.MAX_CHARS for b in batches)     # F5 split it at its commas, as it does for every reply
    assert " ".join(batches) == sentence and runs == [8 * len(sentence)]       # the whole sentence was rendered


def test_fixtts_whole_a_sentence_with_no_punctuation_at_all_goes_to_f5_whole_not_truncated(studio_up):
    sentence = " ".join(["then the lamp in the hall turned on by itself while nobody was home"] * 5)
    assert len(sentence) > F5.MAX_CHARS and "," not in sentence and "." not in sentence
    f5 = F5()
    runs = speak(studio_up, "skippy", skippy_on(f5), {"text": sentence, "whole": True})
    assert f5.batch_calls[0]["texts"] == [sentence + "."] and runs == [8 * (len(sentence) + 1)]     # (F5 gets the closing stop it wants, and every word)


def test_fixtts_whole_two_sentences_that_each_fill_a_batch_are_two_runs(studio_up):
    first = "Right now the " + ", the ".join(["porch light is on"] * 9) + "."
    second = "And later the " + ", the ".join(["kitchen light is off"] * 8) + "."
    assert len(first) > F5.MAX_CHARS and len(second) > F5.MAX_CHARS
    f5 = F5()
    runs = speak(studio_up, "skippy", skippy_on(f5), {"text": f"{first} {second}", "whole": True})
    assert len(runs) == 2 and " ".join(t for call in f5.batch_calls for t in call["texts"]) == f"{first} {second}"


def test_fixtts_whole_a_short_reply_full_of_numbers_grows_in_words_and_is_still_rendered_to_the_last_one(studio_up):
    """The reply is counted as written (52 characters); spelled out for F5 it is several times longer. It is still ONE run of the studio, F5 splits it inside, nothing is lost."""
    text = "The totals are 1,284, 2,357, 9,876, 4,321 and 8,765."
    spelled = studio.for_f5(text)
    assert len(text) < F5.MAX_CHARS < len(spelled)
    f5 = F5()
    runs = speak(studio_up, "skippy", skippy_on(f5), {"text": text, "whole": True})
    assert len(runs) == 1 and len(f5.batch_calls) == 1
    batches = f5.batch_calls[0]["texts"]
    assert len(batches) > 1 and " ".join(batches) == spelled and runs == [8 * len(spelled)]


def test_fixtts_whole_a_voice_with_no_batch_size_still_renders_every_word(studio_up):
    class Stock:                                                               # (Kokoro cuts a long text itself and joins it: no batch_chars)
        def __init__(self):
            self.said = []

        def say(self, text, seed=None):
            self.said.append(text)
            return np.full(8 * len(text), 0.1, "float32")

    text = " ".join(f"The {o} light in the {p} is a short sentence of its own." for o, p in zip(ORDINALS, PLACES))
    assert len(text) <= studio.MAX_TEXT
    voice = Stock()
    speak(studio_up, "stock", voice, {"text": text, "whole": True})
    assert " ".join(voice.said) == text
