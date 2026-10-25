"""diarize: stretches become turns, each turn goes to whisper (in pieces when long, with the last words as its prompt), and the
transcript reads "Speaker n [m:ss-m:ss]: ...". The models themselves are measured in docs/search-speech-popups.md."""
import base64
import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

import diarize as D


def test_turns_merge_one_speaker_and_renumber_in_order_of_first_words():
    segs = [{"start": 0.0, "end": 2.0, "speaker": 4}, {"start": 2.5, "end": 4.0, "speaker": 4},   # one turn: 0.5 s gap
            {"start": 4.2, "end": 6.0, "speaker": 0}, {"start": 6.1, "end": 6.3, "speaker": 4},   # a cough: too short
            {"start": 9.0, "end": 12.0, "speaker": 0}]
    plan = D.turns(segs)
    assert [(t["speaker"], t["start"], t["end"]) for t in plan] == [(1, 0.0, 4.0), (2, 4.2, 6.0), (2, 9.0, 12.0)]


def test_transcribe_cuts_long_turns_and_sends_no_prompt():
    calls = []

    def ask(clip, prompt):
        calls.append((len(clip), prompt))
        return f"part{len(calls)}"
    samples = np.zeros(int(70 * D.RATE), np.float32)
    plan = D.transcribe(samples, [{"start": 0.0, "end": 65.0, "speaker": 1}, {"start": 66.0, "end": 69.0, "speaker": 2}], ask)
    assert [t["text"] for t in plan] == ["part1 part2 part3", "part4"]   # 65 s = 30 + 30 + 5
    assert {c[1] for c in calls} == {" "}                             # no words as a prompt, and never the voice-command primer
    assert calls[0][0] == 44 + int(30 * D.RATE) * 2                         # a 16-bit mono WAV of exactly the piece


def test_text_layout():
    plan = [{"speaker": 1, "start": 3.2, "end": 11.0, "text": "Welcome back."},
            {"speaker": 2, "start": 3700, "end": 3710, "text": ""}]
    out = D.as_text(plan, 3720)
    assert out.startswith("2 speakers, 2 turns, 1:02:00")
    assert "Speaker 1 [0:03-0:11]: Welcome back." in out and "Speaker 2 [1:01:40-1:01:50]: (nothing whisper" in out


def test_run_json_and_too_long(monkeypatch):
    monkeypatch.setattr(D, "decode", lambda data: np.zeros(10 * D.RATE, np.float32))
    monkeypatch.setattr(D, "who_spoke_when", lambda s, k=None: [{"start": 0.0, "end": 4.0, "speaker": 7},
                                                                {"start": 5.0, "end": 9.0, "speaker": 3}])
    monkeypatch.setattr(D, "whisper", lambda clip, prompt="": "hello")
    body, ctype = D.run(b"x", {"format": "json"})
    got = json.loads(body)
    assert ctype == "application/json" and got["speakers"] == 2 and [t["speaker"] for t in got["turns"]] == [1, 2]
    body, ctype = D.run(b"x", {})
    assert ctype == "text/plain" and b"Speaker 2 [0:05-0:09]: hello" in body
    monkeypatch.setattr(D, "decode", lambda data: np.zeros(int(D.MAX_SECONDS + 1) * D.RATE, np.int8))
    with pytest.raises(ValueError):
        D.run(b"x", {})


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(D, "run", lambda data, args: (b"Speaker 1 [0:00-0:02]: " + data, "text/plain"))
    srv = D.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers.get_content_type(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get_content_type(), e.read()


def test_http_contract(server, monkeypatch):
    status, ctype, body = post(server + "/run", {"args": {}, "input": {"type": "audio/wav", "name": "a.wav",
                                                                       "b64": base64.b64encode(b"hi").decode()}})
    assert status == 200 and ctype == "text/plain" and body.endswith(b"hi")
    status, _, body = post(server + "/run", {"args": {}})
    assert status == 400 and "attach a recording" in json.loads(body)["error"]
    monkeypatch.setattr(D, "WHISPER", "http://127.0.0.1:9")          # nothing listens there
    with urllib.request.urlopen(server + "/health") as r:
        h = json.loads(r.read())
    assert h["ready"] is False and h["whisper"] is False


def test_registry_entries_are_valid():
    """The framework's own check (portal/popups.py) on this family's entries, when the framework is in this checkout."""
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    spec_path = root / "portal" / "popups.py"
    if not spec_path.is_file():
        pytest.skip("the pop-up framework isn't in this checkout yet")
    spec = importlib.util.spec_from_file_location("popups_fw", spec_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    entries = json.loads((root / "services" / "search-popups" / "popups.json").read_text(encoding="utf-8"))["popups"]
    assert [mod.check(e) for e in entries] == [None, None]
