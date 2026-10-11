"""The gate client signs passes the REAL voice gate accepts, and the timers keep time."""
import importlib.util
import io
import wave
from pathlib import Path

import pytest
import requests

import voicegate
from timers import Timers

GATE_PY = Path(__file__).resolve().parents[2] / "voice-gate" / "gate.py"


def real_gate():
    spec = importlib.util.spec_from_file_location("voice_gate_real", GATE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_passes_are_exactly_what_the_real_voice_gate_signs_and_accepts():
    real = real_gate()
    assert voicegate.gate_key("test-home-token") == real.gate_key("test-home-token")
    key = voicegate.gate_key("test-home-token")
    for scope in ("speak", "listen"):
        token = voicegate.sign(key, 1_800_003_600, scope)
        assert token == real.sign(real.gate_key("test-home-token"), 1_800_003_600, scope)
        assert real.valid(real.gate_key("test-home-token"), token, 1_800_000_000) == scope


class Resp:
    def __init__(self, status=200, js=None, content=b""):
        self.status_code, self._js, self.content = status, js, content

    def json(self):
        if self._js is None:
            raise ValueError("no json")
        return self._js


def gate_with(resp):
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        if isinstance(resp, Exception):
            raise resp
        return resp
    return voicegate.Gate("http://127.0.0.1:3007/", "tok", post=post, clock=lambda: 1_800_000_000), calls


def test_stt_posts_a_wav_with_a_speak_pass_and_returns_the_words():
    g, calls = gate_with(Resp(200, {"text": " hello there "}))
    assert g.stt(b"RIFFxxxx") == "hello there"
    url, kw = calls[0]
    assert url.startswith("http://127.0.0.1:3007/t/") and "/stt?lang=en" in url and kw["headers"]["Content-Type"] == "audio/wav" and kw["data"] == b"RIFFxxxx"
    assert real_gate().valid(real_gate().gate_key("tok"), url.split("/t/")[1].split("/")[0], 1_800_000_000) == "speak"


def test_tts_and_summary_and_every_way_the_gate_can_fail():
    g, calls = gate_with(Resp(200, content=b"ID3audio"))
    assert g.tts("hi " * 400) == b"ID3audio" and len(calls[0][1]["json"]["text"]) == 500                 # capped like the gate does
    g, _ = gate_with(Resp(200, {"text": "Short version."}))
    assert g.summary("long") == "Short version."
    with pytest.raises(voicegate.BudgetSpent):
        gate_with(Resp(429, {"budget": True}))[0].tts("hi")
    with pytest.raises(voicegate.GateError) as busy:
        gate_with(Resp(429, {"error": "busy"}))[0].tts("hi")
    assert not isinstance(busy.value, voicegate.BudgetSpent)
    for bad in (Resp(500), Resp(200, content=b""), requests.ConnectionError("down")):
        with pytest.raises(voicegate.GateError):
            gate_with(bad)[0].tts("hi")
    with pytest.raises(voicegate.GateError):
        gate_with(Resp(403))[0].stt(b"x")
    with pytest.raises(voicegate.GateError):
        gate_with(Resp(200, {"text": "  "}))[0].summary("x")


def test_wav_bytes_is_a_mono_16_bit_16k_wav():
    w = wave.open(io.BytesIO(voicegate.wav_bytes([0.0, 0.5, -0.5, 1.0, -1.0, 2.0])), "rb")
    assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 6)


def test_timers_count_down_announce_once_cancel_by_label_and_have_limits():
    now = [1000.0]
    t = Timers(clock=lambda: now[0])
    t.add(600, "pasta")
    t.add(60)
    assert [lab for lab, _ in t.remaining()] == ["1 minute", "pasta"] and t.remaining()[0][1] == 60
    now[0] += 61
    assert [x.label for x in t.due()] == ["1 minute"] and t.due() == []                                    # announced once
    assert t.cancel("PAS") == 1 and t.remaining() == []
    t.add(5)
    t.add(5, "tea")
    assert t.cancel() == 2                                                                                  # blank = all
    for bad in (0, 86401, -3):
        with pytest.raises(ValueError):
            t.add(bad)
    for _ in range(Timers.MAX):
        t.add(10)
    with pytest.raises(ValueError):
        t.add(10)
    assert Timers._name(3600) == "1 hour" and Timers._name(7200) == "2 hours" and Timers._name(90) == "90 seconds"
