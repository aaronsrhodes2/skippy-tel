"""The popup-gate (docs/popups.md "Big files"): one file per pass, at most the pass's bytes, a picture or a sound by its own first bytes,
kept with its owner for a day; no pass, a bent pass or another gate's pass opens nothing. Run in its own session:
`cd services/popup-gate && python -m pytest tests -q` (it has a `gate.py`, like the other gates)."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate  # noqa: E402

NOW = int(time.time())       # real time: the sweep reads the files' own mtimes
KEY = gate.gate_key("test-home-token")
OWNER = "0123456789abcdef"
WAV = b"RIFF" + bytes(4) + b"WAVEfmt " + bytes(64)


def a_pass(file_id="a" * 32, max_bytes=1000, owner=OWNER, exp=NOW + 600, key=KEY):
    return gate.sign(key, exp, file_id, max_bytes, owner)


@pytest.fixture
def world(tmp_path):
    clock = [NOW]
    app = gate.create_app(KEY, str(tmp_path), clock=lambda: clock[0])
    return app.test_client(), tmp_path, clock


def test_a_sound_is_kept_with_its_owner_and_said_back(world):
    client, root, _ = world
    r = client.put(f"/t/{a_pass()}/file", data=WAV, headers={"X-File-Name": "my track.wav"})
    assert r.status_code == 200 and r.get_json() == {"id": "a" * 32, "type": "audio/wav", "bytes": len(WAV)}
    assert (root / ("a" * 32 + ".wav")).read_bytes() == WAV
    meta = json.loads((root / ("a" * 32 + ".json")).read_text())
    assert meta["owner"] == OWNER and meta["type"] == "audio/wav" and meta["name"] == "my track.wav"
    assert r.headers["Access-Control-Allow-Origin"] == "*"


def test_one_file_per_pass(world):
    client, _, _ = world
    assert client.put(f"/t/{a_pass()}/file", data=WAV).status_code == 200
    assert client.put(f"/t/{a_pass()}/file", data=WAV).status_code == 409


@pytest.mark.parametrize("body, code", [(WAV + bytes(2000), 413), (b"<html>not a sound</html>", 415), (b"", 400)])
def test_too_big_not_a_picture_or_sound_or_empty_is_refused_and_nothing_kept(world, body, code):
    client, root, _ = world
    assert client.put(f"/t/{a_pass()}/file", data=body).status_code == code
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("token", ["", "nonsense", a_pass(exp=NOW - 1), a_pass(exp=NOW + 3 * 3600),
                                   a_pass(key=gate.gate_key("another-token")), a_pass().replace(".1000.", ".9999.")])
def test_no_pass_an_old_one_or_a_bent_one_opens_nothing(world, token):
    client, _, _ = world
    assert client.put(f"/t/{token}/file", data=WAV).status_code in (403, 404)


def test_another_gates_pass_opens_nothing(world):
    import hashlib
    import hmac
    voice_key = hmac.new(hashlib.sha256(b"test-home-token").hexdigest().encode(), b"voice-gate-v1", hashlib.sha256).digest()
    client, _, _ = world
    assert client.put(f"/t/{a_pass(key=voice_key)}/file", data=WAV).status_code == 403


def test_funnels_in_prefix_is_understood_and_health_is_the_readiness_contract(world):
    client, _, _ = world
    assert client.put(f"/in/t/{a_pass()}/file", data=WAV).status_code == 200
    h = client.get("/health").get_json()
    assert h["ready"] is True and h["service"] == "popup-gate" and h["files"] == 1 and h["max_bytes"] == gate.MAX_BYTES


def test_files_are_swept_after_a_day(world, tmp_path):
    import os
    client, root, _ = world
    client.put(f"/t/{a_pass()}/file", data=WAV)
    for p in root.iterdir():
        os.utime(p, (NOW - gate.KEEP_S - 10, NOW - gate.KEEP_S - 10))
    assert client.get("/health").get_json()["files"] == 0 and list(root.iterdir()) == []


@pytest.mark.parametrize("head, ctype", [(b"\x89PNG\r\n\x1a\n" + bytes(8), "image/png"), (b"\xff\xd8\xff\xe0" + bytes(8), "image/jpeg"),
                                         (b"fLaC" + bytes(12), "audio/flac"), (b"OggS" + bytes(12), "audio/ogg"),
                                         (b"ID3" + bytes(13), "audio/mpeg"), (b"RIFF" + bytes(4) + b"WEBP" + bytes(4), "image/webp")])
def test_the_type_comes_from_the_files_own_first_bytes(head, ctype):
    assert gate.sniff(head) == ctype
