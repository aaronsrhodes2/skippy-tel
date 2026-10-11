"""The voice gate: no pass, no service; a listen pass hears but cannot make Skippy speak; the other gates' passes open nothing."""
import hashlib
import hmac
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate  # noqa: E402

NOW = 1_800_000_000
KEY = gate.gate_key("test-home-token")


class Resp:
    def __init__(self, status=200, js=None, content=b""):
        self.status_code, self._js, self.content = status, js, content

    def json(self):
        if self._js is None:
            raise ValueError("no json")
        return self._js


class Fake:
    def __init__(self):
        self.posts, self.gets = [], []
        self.stt = Resp(200, {"english_text": " hello there ", "detected_lang": "en"})
        self.tts = Resp(200, content=b"ID3audio")

    def post(self, url, **kw):
        self.posts.append((url, kw))
        return self.stt

    def get(self, url, **kw):
        self.gets.append((url, kw))
        return self.tts if "/tts/stream" in url else Resp(200, {})


@pytest.fixture
def stack():
    fake = Fake()
    app = gate.create_app(KEY, "http://w", "http://t", clock=lambda: NOW, post=fake.post, get=fake.get)
    return app.test_client(), fake


def listen():
    return gate.sign(KEY, NOW + 3600)


def speak():
    return gate.sign(KEY, NOW + 3600, "speak")


def test_the_pass_scheme_is_pinned_so_the_portal_can_mirror_it():
    sha = hashlib.sha256(b"test-home-token").hexdigest()
    key = hmac.new(sha.encode(), b"voice-gate-v1", hashlib.sha256).digest()
    assert gate.gate_key("test-home-token") == key
    assert gate.sign(key, 1800003600) == "1800003600." + hmac.new(key, b"voice:1800003600", hashlib.sha256).hexdigest()[:32]
    assert gate.sign(key, 1800003600, "speak") == "1800003600." + hmac.new(key, b"voice-speak:1800003600", hashlib.sha256).hexdigest()[:32]
    assert gate.valid(key, gate.sign(key, NOW + 5), NOW) == "listen"
    assert gate.valid(key, gate.sign(key, NOW + 5, "speak"), NOW) == "speak"


def test_other_gates_passes_and_bad_passes_open_nothing(stack):
    c, fake = stack
    sha = hashlib.sha256(b"test-home-token").hexdigest()
    for label, text in ((b"music-gate-v1", f"music:{NOW + 60}"), (b"video-gate-v1", f"video:{NOW + 60}")):
        k = hmac.new(sha.encode(), label, hashlib.sha256).digest()
        p = f"{NOW + 60}." + hmac.new(k, text.encode(), hashlib.sha256).hexdigest()[:32]
        assert c.post(f"/t/{p}/stt", data=b"x", content_type="audio/webm").status_code == 403
    assert c.post("/t/nope/stt", data=b"x", content_type="audio/webm").status_code == 403
    assert c.post(f"/t/{gate.sign(KEY, NOW - 1)}/stt", data=b"x", content_type="audio/webm").status_code == 403
    assert c.get("/anything").status_code == 404
    assert not fake.posts and not [g for g in fake.gets if "/tts/stream" in g[0]]


def test_health_needs_no_pass(stack):
    c, _ = stack
    j = c.get("/health").get_json()
    assert j["ok"] and j["ready"] and j["tts"]


def test_stt_returns_the_text_and_validates_input(stack):
    c, fake = stack
    r = c.post(f"/t/{listen()}/stt?lang=en", data=b"abc", content_type="audio/webm;codecs=opus")
    assert r.status_code == 200 and r.get_json() == {"text": "hello there", "lang": "en"}
    assert fake.posts[0][1]["data"] == {"hint_lang": "en"}
    assert c.post(f"/t/{listen()}/stt", data=b"abc", content_type="text/plain").status_code == 415
    assert c.post(f"/t/{listen()}/stt?lang=../x", data=b"abc", content_type="audio/webm").status_code == 400
    assert c.post(f"/t/{listen()}/stt", data=b"", content_type="audio/webm").status_code == 400
    assert c.post(f"/t/{listen()}/stt", data=b"x" * (gate.MAX_AUDIO + 8192), content_type="audio/webm").status_code == 413


def test_stt_survives_a_dead_whisper(stack):
    c, fake = stack
    fake.stt = Resp(500)
    assert c.post(f"/t/{listen()}/stt", data=b"abc", content_type="audio/ogg").status_code == 503
    fake.stt = Resp(200, None)
    assert c.post(f"/t/{listen()}/stt", data=b"abc", content_type="audio/ogg").status_code == 502


def test_only_a_speak_pass_makes_skippy_talk(stack):
    c, fake = stack
    assert c.post(f"/t/{listen()}/tts", json={"text": "hi"}).status_code == 403
    assert not [g for g in fake.gets if "/tts/stream" in g[0]]
    r = c.post(f"/t/{speak()}/tts", json={"text": " I am Skippy "})
    assert r.status_code == 200 and r.mimetype == "audio/mpeg" and r.data == b"ID3audio"
    assert [g for g in fake.gets if "/tts/stream" in g[0]][0][1]["params"] == {"text": "I am Skippy"}
    assert c.post(f"/t/{speak()}/tts", json={"text": ""}).status_code == 400
    assert c.post(f"/t/{speak()}/tts", json={"text": "x" * 501}).status_code == 413
    fake.tts = Resp(503)
    assert c.post(f"/t/{speak()}/tts", json={"text": "hi"}).status_code == 503
