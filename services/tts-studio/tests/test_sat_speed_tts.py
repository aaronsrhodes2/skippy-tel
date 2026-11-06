"""tts-studio, 2026-11-05 ("streamline it so it becomes snappy like Amazon Echo"): what a line spends outside F5's 16 flow steps, and how a reply is cut for a
caller that plays nothing until it has all of it. No GPU here: F5 is a fake that records what it is handed. The same-seed audio comparison with the real
F5 (old path against new) is a run in a throwaway container, see the commit message."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")
np = pytest.importorskip("numpy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as studio  # noqa: E402


class FakeF5:
    """The parts of f5_tts that Skippy.say touches."""

    def __init__(self):
        self.infers, self.batch_calls, self.seeds = [], [], []
        self.ema_model, self.vocoder, self.mel_spec_type, self.device = object(), object(), "vocos", "cuda"

    def infer(self, **kw):                                  # F5TTS.infer: the long way (it reads the reference from disk on every line)
        self.infers.append(kw)
        return np.full(240, 0.5, "float32"), 24000, None

    def prepared(self, fail=None):
        audio = object()                                    # (a tensor in real life: held, never copied or re-read)

        def batches(ref, ref_text, texts, model, vocoder, **kw):
            if fail:
                raise fail
            self.batch_calls.append({"ref": ref, "ref_text": ref_text, "texts": list(texts), "model": model, "vocoder": vocoder, **kw})
            yield np.full(480, 0.25, "float32"), 24000, None

        return {"chunk_text": lambda text, max_chars: [text], "batches": batches, "seed_everything": self.seeds.append, "audio": audio, "sr": 24000,
                "ref_text": "Prepared reference words. ", "max_chars": 186}


def skippy(f5, prepared="fast"):
    s = studio.Skippy.__new__(studio.Skippy)                # (without loading a model)
    s.m, s.ref_text = f5, "Reference words."
    s.prepared = f5.prepared() if prepared == "fast" else prepared
    return s


def test_speedtts_a_line_never_reads_the_reference_from_disk_again():
    f5 = FakeF5()
    s = skippy(f5)
    audio = s.prepared["audio"]
    for n in range(3):
        wav = s.say("Hello there", seed=5 + n)
        assert wav.dtype == np.float32 and len(wav) == 480
    assert f5.infers == []                                  # F5TTS.infer (a read, an md5 and a decode of the reference per line) was never asked
    assert [c["ref"][0] for c in f5.batch_calls] == [audio] * 3 and f5.batch_calls[0]["ref"][1] == 24000      # the same held reference every time
    assert f5.seeds == [5, 6, 7]
    call = f5.batch_calls[0]
    assert call["ref_text"] == "Prepared reference words. " and call["texts"] == ["Hello there."]          # (F5's text rules: a closing stop)
    # F5TTS.infer's own arguments, so the voice is the voice (and nfe_step stays the service's setting)
    assert (call["nfe_step"], call["cfg_strength"], call["sway_sampling_coef"], call["speed"], call["target_rms"]) == (studio.F5_NFE, 2, -1, 1.0, 0.1)
    assert (call["cross_fade_duration"], call["fix_duration"], call["progress"], call["device"], call["mel_spec_type"]) == (0.15, None, None, "cuda", "vocos")
    assert call["model"] is f5.ema_model and call["vocoder"] is f5.vocoder


def test_speedtts_each_line_is_a_new_random_take_unless_a_seed_is_given():
    f5 = FakeF5()
    s = skippy(f5)
    for _ in range(3):
        s.say("Another take")
    s.say("Fixed", seed=1234)
    assert len(set(f5.seeds[:3])) == 3 and all(isinstance(x, int) and x >= 0 for x in f5.seeds) and f5.seeds[3] == 1234


def test_speedtts_a_different_f5_goes_back_to_the_long_way_for_good():
    f5 = FakeF5()
    s = skippy(f5, prepared=f5.prepared(fail=TypeError("unexpected keyword argument")))
    wav = s.say("Hello there", seed=9)
    assert len(wav) == 240 and s.prepared is None           # what F5TTS.infer said, and the fast path is off from now on
    assert f5.infers[0]["ref_file"].endswith("ref.wav") and f5.infers[0]["gen_text"] == "Hello there." and f5.infers[0]["seed"] == 9
    assert f5.infers[0]["nfe_step"] == studio.F5_NFE and f5.infers[0]["remove_silence"] is False
    s.say("Again")
    assert len(f5.infers) == 2 and not f5.batch_calls


def test_speedtts_a_reference_that_cannot_be_prepared_is_not_a_crash():
    s = studio.Skippy.__new__(studio.Skippy)
    s.m, s.ref_text = FakeF5(), "Reference words."
    assert s._prepare() is None                              # (no f5_tts, or no /ref here: it says so and keeps the long way)
    s.prepared = None
    assert s.batch_chars == 180


def test_speedtts_a_warm_resident_voice_is_ready_without_asking_for_all_its_files(monkeypatch):
    def no_disk():
        raise AssertionError("files() is seven stat() calls on the bind mounts, on every line and every health check")
    monkeypatch.setattr(studio, "files", no_disk)
    monkeypatch.setattr(studio, "line_file", lambda v: __file__)               # (it still looks at the ONE file a line may read: test_sat_fix_tts.py pins that)
    monkeypatch.setattr(studio, "RESIDENT", ["skippy", "stock"])
    monkeypatch.setattr(studio, "_warm", {"skippy", "stock"})
    assert studio.ready() == {"stock": True, "skippy": True}
    assert studio.app.test_client().get("/health").get_json()["voices"] == {"stock": True, "skippy": True}


def test_speedtts_ready_still_asks_the_disk_for_a_voice_that_is_not_warm_yet(monkeypatch):
    monkeypatch.setattr(studio, "line_file", lambda v: __file__)               # (a warm voice's one file is there)
    monkeypatch.setattr(studio, "RESIDENT", ["skippy", "stock"])
    monkeypatch.setattr(studio, "_warm", {"stock"})
    monkeypatch.setattr(studio, "files", lambda: {"stock": False, "skippy": True})
    assert studio.ready() == {"stock": True, "skippy": False}           # a loaded voice needs no files; a resident one still loading is not ready
    monkeypatch.setattr(studio, "RESIDENT", ["stock"])
    assert studio.ready() == {"stock": True, "skippy": True}            # not resident: it loads on its first line, so its files are what count
    monkeypatch.setattr(studio, "files", lambda: {"stock": False, "skippy": False})
    assert studio.ready() == {"stock": True, "skippy": False}


def test_speedtts_chunks_whole_packs_up_to_the_batch_size_and_never_splits_a_sentence():
    text = "The bedroom lamp is on and the porch light is dim. I left the kitchen alone, since you did not ask."
    assert studio.chunks(text) == ["The bedroom lamp is on and the porch light is dim.", "I left the kitchen alone, since you did not ask."]      # as before
    assert studio.chunks_whole(text, 186) == [text]
    assert studio.chunks_whole("Done. Yes.", 186) == ["Done. Yes."]
    long_text = " ".join(f"Sentence number {i} is right here." for i in range(12))
    parts = studio.chunks_whole(long_text, 100)
    assert len(parts) > 1 and all(len(p) <= 100 for p in parts) and " ".join(parts) == long_text
    assert all(p.endswith(".") for p in parts)                                                                                  # cut between sentences only
    giant = "word " * 60 + "end."
    assert studio.chunks_whole(giant, 100) == [giant]                                                                           # one sentence longer than the limit stays whole
    assert studio.chunks_whole("   ", 186) == []


class Voice:
    batch_chars = 186

    def __init__(self):
        self.said = []

    def say(self, text, seed=None):
        self.said.append(text)
        return np.full(2400, 0.1, "float32")


def speak(monkeypatch, body, voice=None):
    voice = voice or Voice()
    monkeypatch.setattr(studio, "files", lambda: {"stock": True, "skippy": True})
    monkeypatch.setattr(studio, "line_file", lambda v: __file__)               # (the warm voice's one file is there)
    monkeypatch.setattr(studio, "RESIDENT", ["skippy"])
    monkeypatch.setattr(studio, "_warm", {"skippy"})
    monkeypatch.setattr(studio, "_models", {"skippy": voice})
    monkeypatch.setattr(studio, "mp3", lambda wav: b"mp3")
    r = studio.app.test_client().post("/speak", json=body)
    data = r.get_data()
    r.close()                                                # a streamed reply gives the studio's lock back on close
    return voice.said, r, data


def test_speedtts_whole_renders_a_short_reply_in_one_run_and_the_default_is_unchanged(monkeypatch):
    text = "The bedroom lamp is on and the porch light is dim. I left the kitchen alone, since you did not ask."
    said, r, data = speak(monkeypatch, {"text": text, "voice": "skippy", "whole": True})
    assert r.status_code == 200 and said == [text] and data == b"mp3"
    said, r, _ = speak(monkeypatch, {"text": text, "voice": "skippy"})
    assert said == ["The bedroom lamp is on and the porch light is dim.", "I left the kitchen alone, since you did not ask."]       # nothing changes unless asked
    said, _, _ = speak(monkeypatch, {"text": text, "voice": "skippy", "whole": "yes"})
    assert len(said) == 2                                    # only a real true asks
    long_text = " ".join(f"Sentence number {i} is right here and goes on a little." for i in range(8))
    said, _, _ = speak(monkeypatch, {"text": long_text, "voice": "skippy", "whole": True})
    assert 1 < len(said) < 8 and all(len(p) <= 186 for p in said) and " ".join(said) == long_text


def test_speedtts_a_chunk_with_no_audio_is_skipped_not_a_broken_mp3(monkeypatch):
    class Silent(Voice):
        def say(self, text, seed=None):
            self.said.append(text)
            return np.zeros(0, "float32")
    said, r, data = speak(monkeypatch, {"text": "Hello there.", "voice": "skippy"}, Silent())
    assert r.status_code == 200 and said == ["Hello there."] and data == b""
    assert not studio._lock.locked()
