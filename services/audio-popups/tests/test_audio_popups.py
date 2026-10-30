"""audio-popups without a GPU: the musical arithmetic (bars to seconds, keys and tempo in the prompt), the audio plumbing that
needs only numpy, what /health says about missing weights, and every route's refusals. The models themselves are timed by
bench.py against the real service (docs/audio-popups.md)."""
import importlib.util
import io
import sys
import wave
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("flask")
# Loaded under its own name: CI runs this in one session with the portal's tests, whose `app` is another module.
_spec = importlib.util.spec_from_file_location("audio_popups_app", Path(__file__).resolve().parents[1] / "app.py")
popups = importlib.util.module_from_spec(_spec)
sys.modules["audio_popups_app"] = popups
_spec.loader.exec_module(popups)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(popups, "WEIGHTS", str(tmp_path))
    return popups.app.test_client()


def seed_weights(root: Path, *names: str):
    """Fake the files /health looks for."""
    for name in names:
        for rel in popups.NEEDS[name]:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
        if name == "melody_variations":
            (root / popups.PATHS[name] / "model-00001-of-00002.safetensors").write_bytes(b"x")
        if name == "stems":
            (root / popups.PATHS[name] / "955717e8-8726e21a.th").write_bytes(b"x")


@pytest.mark.parametrize("bpm, bars, seconds", [
    (128, 4, 7.5), (120, 1, 2.0), (174, 8, 11.034483), (90, 2, 5.333333), ("140", "16", 27.428571),
])
def test_bars_become_exact_seconds_so_a_loop_lands_on_the_grid(bpm, bars, seconds):
    assert popups.bar_seconds(bpm, bars) == pytest.approx(seconds, abs=1e-6)


@pytest.mark.parametrize("bpm, bars", [(None, 4), (128, None), (20, 4), (300, 4), (128, 0), (128, 100), ("fast", 4)])
def test_silly_tempo_or_bars_fall_back_to_seconds(bpm, bars):
    assert popups.bar_seconds(bpm, bars) is None


@pytest.mark.parametrize("key, said", [
    ("A minor", "A minor"), ("am", "A minor"), ("F#m", "F# minor"), ("Bb major", "Bb major"), ("c", "C major"),
    ("e min", "E minor"), ("G♯m", "G# minor"), ("Dmaj", "D major"), ("H minor", None), ("", None), (None, None),
])
def test_keys_are_spelled_the_way_freesound_tags_spell_them(key, said):
    assert popups.tidy_key(key) == said


def test_tempo_and_key_join_the_prompt_once():
    assert popups.loop_prompt("tech-house hat loop", 128, "am") == "tech-house hat loop, 128 BPM, A minor"
    assert popups.loop_prompt("128 BPM tech-house hat loop, A minor", 128, "A minor") == "128 BPM tech-house hat loop, A minor"
    assert popups.loop_prompt("  rolling   dnb  break ", None, None) == "rolling dnb break"
    assert popups.loop_prompt("pad", "slow", "nope") == "pad"
    assert len(popups.loop_prompt("x" * 1000)) == 400


@pytest.mark.parametrize("model, value, want", [
    ("sfx_quick", None, 3.0), ("sfx_quick", 0.1, 0.5), ("sfx_quick", 30, 11.0), ("sfx_quick", "2.5", 2.5),
    ("sfx_loop", 60, 47.0), ("sfx_loop", "nan", 8.0), ("sfx_loop", "abc", 8.0),
    ("melody_variations", 1, 4.0), ("melody_variations", 45, 30.0),
])
def test_lengths_are_held_to_what_each_model_can_make(model, value, want):
    assert popups.clamp_seconds(model, value) == want


def test_seeds_are_kept_when_sane_and_made_up_otherwise():
    assert popups.pick_seed(42) == 42 and popups.pick_seed("7") == 7
    for bad in (None, -1, 2**40, "x"):
        assert 0 <= popups.pick_seed(bad) < 2**31


def test_wav_is_16_bit_interleaved_stereo():
    np = pytest.importorskip("numpy")
    left, right = np.full(100, 0.5, np.float32), np.full(100, -0.25, np.float32)
    data = popups.pcm16_wav(np.stack([left, right]), 44100)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (2, 2, 44100, 100)
        frames = np.frombuffer(w.readframes(2), "<i2")
    assert list(frames) == [16384, -8192, 16384, -8192]


def test_a_loop_is_cut_sample_exact_and_never_faded():
    np = pytest.importorskip("numpy")
    raw = np.full((2, 1000), 0.25, np.float32)
    loop = popups.finish(raw, 1000, frames=750, loop=True)
    assert loop.shape == (2, 750)
    assert np.allclose(loop, 10 ** (-1 / 20))                                 # peak at -1 dBFS, every sample, no fade
    shot = popups.finish(raw, 1000, frames=1200, loop=False)
    assert shot.shape == (2, 1200) and shot[0, -1] == 0.0 and shot[0, 0] > 0.8  # padded, and the tail fades to silence


def test_stems_come_as_one_zip_of_named_files():
    data = popups.stem_zip({"drums": b"d", "bass": b"b", "other": b"o", "vocals": b"v"}, "flac")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert names == ["drums.flac", "bass.flac", "other.flac", "vocals.flac"]


def test_health_lists_every_model_and_says_which_weights_are_missing(client, tmp_path):
    body = client.get("/health").get_json()
    assert body["ready"] is False and body["loaded"] is False and body["loaded_models"] == []
    assert set(body["models"]) == {"sfx_quick", "sfx_loop", "melody_variations", "stems", "audio_to_midi"}
    assert body["models"]["audio_to_midi"]["gpu"] is False and body["models"]["stems"]["gpu"] is True
    seed_weights(tmp_path, "sfx_loop", "stems", "melody_variations")
    models = client.get("/health").get_json()["models"]
    assert {n for n, m in models.items() if m["ready"]} == {"sfx_loop", "stems", "melody_variations"}
    assert client.get("/health").get_json()["ready"] is True


def test_sfx_quick_is_not_ready_without_t5_even_with_its_own_weights(client, tmp_path):
    seed_weights(tmp_path, "sfx_quick")
    (tmp_path / popups.PATHS["t5"] / "model.safetensors").unlink()
    assert client.get("/health").get_json()["models"]["sfx_quick"]["ready"] is False


@pytest.mark.parametrize("route", ["/sfx_quick", "/sfx_loop"])
def test_generators_want_a_prompt_then_their_weights(client, route):
    assert client.post(route, json={}).status_code == 400
    r = client.post(route, json={"prompt": "808 kick, punchy"})
    assert r.status_code == 503 and "weights" in r.get_json()["error"]


@pytest.mark.parametrize("route", ["/melody_variations", "/stems", "/audio_to_midi"])
def test_clip_routes_want_a_clip(client, route):
    r = client.post(route, data={"prompt": "deep house"})
    assert r.status_code == 400 and "attach" in r.get_json()["error"]


def test_melody_wants_a_prompt_too(client):
    r = client.post("/melody_variations", data={"audio": (io.BytesIO(b"RIFF"), "clip.wav")})
    assert r.status_code == 400 and "prompt" in r.get_json()["error"]


@pytest.mark.parametrize("form, word", [({"format": "ogg"}, "format"), ({"stem": "guitar"}, "stem")])
def test_stems_refuses_formats_and_stems_it_does_not_make(client, form, word):
    r = client.post("/stems", data={"audio": (io.BytesIO(b"RIFF"), "track.wav"), **form})
    assert r.status_code == 400 and word in r.get_json()["error"]


def test_a_raw_audio_body_counts_as_the_clip(client):
    r = client.post("/audio_to_midi", data=b"RIFF....", content_type="audio/wav")
    assert r.status_code == 503                                                # got past "attach", stopped at the weights


def test_unknown_pop_ups_are_404(client):
    assert client.post("/run/make_song", json={"args": {}}).status_code == 404
    assert client.post("/make_song", json={}).status_code == 404


def test_the_contract_carries_args_and_a_base64_clip(client):
    import base64
    r = client.post("/run/sfx_loop", json={"args": {}})
    assert r.status_code == 400 and "prompt" in r.get_json()["error"]
    r = client.post("/run/sfx_quick", json={"args": {"prompt": "rimshot"}})
    assert r.status_code == 503
    r = client.post("/run/stems", json={"args": {}, "input": {"type": "audio/mpeg", "name": "t.mp3", "b64": "@@not base64@@"}})
    assert r.status_code == 400 and "whole" in r.get_json()["error"]
    clip = base64.b64encode(b"ID3....").decode()
    r = client.post("/run/audio_to_midi", json={"args": {"bpm": 124}, "input": {"type": "audio/mpeg", "name": "t.mp3", "b64": clip}})
    assert r.status_code == 503                                                # past "attach": the clip arrived
    r = client.post("/run/melody_variations", json={"args": {"prompt": "acid house"}})
    assert r.status_code == 400 and "attach" in r.get_json()["error"]


def test_the_chat_stems_job_is_mp3_so_the_zip_fits(client, monkeypatch):
    seen = {}

    def fake(body, clip):
        seen.update(body)
        return popups.Result(b"mp3", "audio/mpeg", "x.mp3", {})

    monkeypatch.setitem(popups.WORK, "stems", fake)
    import base64
    r = client.post("/run/stems", json={"args": {"format": "wav"}, "input": {"b64": base64.b64encode(b"x").decode()}})
    assert r.status_code == 200
    assert seen == {"format": "mp3"}


def test_stems_takes_instrumental_as_a_stem(client):
    r = client.post("/stems", data={"audio": (io.BytesIO(b"RIFF"), "track.wav"), "stem": "instrumental"})
    assert r.status_code == 503                                                # accepted; stopped only at the weights


def test_notes_read_like_a_piano_roll():
    assert popups.note_name(60) == "C4" and popups.note_name(69) == "A4" and popups.note_name(34) == "A#1"
    rows = popups.note_list([(1.0, 1.5, 45, 0.5, []), (0.0, 0.25, 33, 1.0, [])], 120)
    assert rows == [{"note": "A1", "beat": 0.0, "beats": 0.5, "velocity": 127},
                    {"note": "A2", "beat": 2.0, "beats": 1.0, "velocity": 64}]


def test_only_the_running_service_ends_itself_after_a_job(client, monkeypatch):
    ended = []
    monkeypatch.setattr(popups, "_end_after_job", lambda: ended.append(1))
    monkeypatch.setitem(popups.WORK, "audio_to_midi", lambda b, c: popups.Result(b"MThd", "audio/midi", "x.mid", {}))
    monkeypatch.setitem(popups._loaded, "audio_to_midi", object())
    client.post("/audio_to_midi", data=b"RIFF", content_type="audio/wav").close()
    assert ended == []                                                         # an import (these tests) never exits
    monkeypatch.setitem(popups.app.config, "SERVING", True)
    client.post("/audio_to_midi", data=b"RIFF", content_type="audio/wav").close()
    assert ended == [1]                                                        # the service does, once the reply is sent
