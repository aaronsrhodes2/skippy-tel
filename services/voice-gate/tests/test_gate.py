"""The voice gate: no pass, no service; a listen pass hears but cannot make Skippy speak; the other gates' passes open nothing."""
import hashlib
import hmac
import subprocess
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
        self.stt = Resp(200, {"english_text": " hello there ", "detected_lang": "en", "confidence": 0.987})
        self.ollama = Resp(200, {"response": "Done: the pill is orange and the tests pass."})
        self._speak = None  # set after Stream is defined

    @property
    def speak(self):
        if self._speak is None:
            self._speak = Stream(200, (b"mp3-one", b"mp3-two"))
        return self._speak

    @speak.setter
    def speak(self, val):
        self._speak = val

    def post(self, url, **kw):
        self.posts.append((url, kw))
        if url.endswith("/speak"):
            return self.speak
        return self.ollama if "/api/generate" in url else self.stt

    def get(self, url, **kw):
        self.gets.append((url, kw))
        return Resp(200, {"ready": True, "voices": {"stock": True, "skippy": True}})


@pytest.fixture
def stack():
    fake = Fake()
    app = gate.create_app(KEY, "http://w", "http://studio", clock=lambda: NOW, post=fake.post, get=fake.get, ollama_url="http://o")
    return app.test_client(), fake


def listen():
    return gate.sign(KEY, NOW + 3600)


def test_the_pass_scheme_is_pinned_so_the_portal_can_mirror_it():
    sha = hashlib.sha256(b"test-home-token").hexdigest()
    key = hmac.new(sha.encode(), b"voice-gate-v1", hashlib.sha256).digest()
    assert gate.gate_key("test-home-token") == key
    assert gate.sign(key, 1800003600) == "1800003600." + hmac.new(key, b"voice:1800003600", hashlib.sha256).hexdigest()[:32]
    assert gate.valid(key, gate.sign(key, NOW + 5), NOW) == "listen"


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
    assert not fake.posts and not fake.gets


def test_health_needs_no_pass(stack):
    c, _ = stack
    j = c.get("/health").get_json()
    assert j["ok"] and j["ready"] and j["whisper"]
    assert j["accepting"] is True and j["jobs"]["running"] == 0 and j["service"] == "voice-gate" and j["detail"] == ""   # PLATINUM section 24


def test_stt_returns_the_text_and_validates_input(stack):
    c, fake = stack
    r = c.post(f"/t/{listen()}/stt?lang=en", data=b"abc", content_type="audio/webm;codecs=opus")
    assert r.status_code == 200 and r.get_json() == {"text": "hello there", "lang": "en", "sure": 0.99}
    assert fake.posts[0][1]["data"] == {"hint_lang": "en"}
    assert c.post(f"/t/{listen()}/stt", data=b"abc", content_type="text/plain").status_code == 415
    assert c.post(f"/t/{listen()}/stt?lang=../x", data=b"abc", content_type="audio/webm").status_code == 400
    assert c.post(f"/t/{listen()}/stt", data=b"", content_type="audio/webm").status_code == 400
    assert c.post(f"/t/{listen()}/stt", data=b"x" * (gate.MAX_AUDIO + 8192), content_type="audio/webm").status_code == 413


def test_stt_says_how_sure_whisper_is_of_the_language(stack):
    c, fake = stack
    fake.stt = Resp(200, {"english_text": "where is the station", "detected_lang": "es", "confidence": 0.93})
    assert c.post(f"/t/{listen()}/stt", data=b"abc", content_type="audio/webm").get_json() == {"text": "where is the station", "lang": "es", "sure": 0.93}
    for odd in (None, "x", 7):                                            # missing or nonsense: never more than 1, never an error
        fake.stt = Resp(200, {"english_text": "hi", "detected_lang": "fr", "confidence": odd})
        assert c.post(f"/t/{listen()}/stt", data=b"abc", content_type="audio/webm").get_json()["sure"] in (0.0, 1.0)


def test_stt_survives_a_dead_whisper(stack):
    c, fake = stack
    fake.stt = Resp(500)
    assert c.post(f"/t/{listen()}/stt", data=b"abc", content_type="audio/ogg").status_code == 503
    fake.stt = Resp(200, None)
    assert c.post(f"/t/{listen()}/stt", data=b"abc", content_type="audio/ogg").status_code == 502


def test_only_a_voice_pass_makes_skippy_talk(stack):
    c, fake = stack
    assert c.post(f"/t/{listen()}/tts", json={"text": "hi"}).status_code == 403
    assert not fake.gets
    for voice in gate.VOICES:
        r = c.post(f"/t/{gate.sign(KEY, NOW + 3600, voice)}/tts", json={"text": " I am Skippy "})
        assert r.status_code == 200 and r.mimetype == "audio/mpeg"
        assert r.data == b"mp3-one" + b"mp3-two"
    assert c.post(f"/t/{gate.sign(KEY, NOW + 3600, 'stock')}/tts", json={"text": ""}).status_code == 400
    assert c.post(f"/t/{gate.sign(KEY, NOW + 3600, 'stock')}/tts", json={"text": "x" * 501}).status_code == 413


def test_the_same_words_are_cached(stack):
    c, fake = stack
    first = c.post(f"/t/{gate.sign(KEY, NOW + 3600, 'stock')}/tts", json={"text": "I am Skippy."})
    again = c.post(f"/t/{gate.sign(KEY, NOW + 3600, 'stock')}/tts", json={"text": " I am Skippy. "})
    assert first.data == again.data
    assert again.headers["X-Voice-Cache"] == "hit"


def test_the_summary_works_for_any_pass(stack):
    c, fake = stack
    for token in (listen(), gate.sign(KEY, NOW + 3600, "stock")):
        r = c.post(f"/t/{token}/summary", json={"text": "## Done\n\nI changed `portal/app.py`, ran the tests and deployed. " * 20})
        assert r.status_code == 200 and r.get_json() == {"text": "Done: the pill is orange and the tests pass."}
    body = fake.posts[0][1]["json"]
    assert body["model"] == "qwen2.5:7b" and body["stream"] is False and "ONLY the spoken words" in body["prompt"]
    assert "THE REPLY:" in body["prompt"] and body["options"]["num_predict"] <= 200


def test_the_summary_is_made_safe_to_speak(stack):
    c, fake = stack
    fake.ollama = Resp(200, {"response": "**Done.** See https://x.test/a and `code`!\n\n```py\nprint(1)\n```\nAll green."})
    out = c.post(f"/t/{listen()}/summary", json={"text": "long"}).get_json()["text"]
    assert out == "Done. See and code! All green." and "*" not in out and "http" not in out
    fake.ollama = Resp(200, {"response": ("A sentence that goes on. " * 40)})
    assert len(c.post(f"/t/{listen()}/summary", json={"text": "long"}).get_json()["text"]) <= 400


def test_the_summary_refuses_junk_and_survives_a_dead_ollama(stack):
    c, fake = stack
    assert c.post(f"/t/{listen()}/summary", json={"text": ""}).status_code == 400
    assert c.post(f"/t/{listen()}/summary", json={"nope": 1}).status_code == 400
    assert c.post(f"/t/{listen()}/summary", json={"text": "x" * 8001}).status_code == 413
    assert c.post("/t/nope/summary", json={"text": "hi"}).status_code == 403                         # a pass is still needed
    assert not fake.posts
    fake.ollama = Resp(500)
    assert c.post(f"/t/{listen()}/summary", json={"text": "hi"}).status_code == 503
    fake.ollama = Resp(200, {"response": "   "})
    assert c.post(f"/t/{listen()}/summary", json={"text": "hi"}).status_code == 502                  # nothing to say: the client falls back
    fake.ollama = Resp(200, None)
    assert c.post(f"/t/{listen()}/summary", json={"text": "hi"}).status_code == 502


def test_no_ollama_configured_means_no_summary_not_a_crash():
    fake = Fake()
    app = gate.create_app(KEY, "http://w", "http://studio", clock=lambda: NOW, post=fake.post, get=fake.get)    # no ollama_url
    assert app.test_client().post(f"/t/{listen()}/summary", json={"text": "hi"}).status_code == 503


# --- the voice a pass names: stock (Kokoro) for everyone, skippy (F5, local) ---------------------------

class Stream(Resp):
    def __init__(self, status=200, parts=(b"mp3-one", b"mp3-two"), broken=False):
        super().__init__(status)
        self.parts, self.broken, self.closed = list(parts), broken, False

    def iter_content(self, chunk_size=None):
        for p in self.parts:
            yield p
        if self.broken:
            raise gate.requests.ConnectionError("cut")

    def close(self):
        self.closed = True


class Studio(Fake):
    def __init__(self):
        super().__init__()
        self.speak = Stream()
        self.health = Resp(200, {"ready": True, "voices": {"stock": True, "skippy": True}})

    def post(self, url, **kw):
        if url.endswith("/speak"):
            self.posts.append((url, kw))
            return self.speak
        return super().post(url, **kw)

    def get(self, url, **kw):
        if url == "http://studio/health":
            self.gets.append((url, kw))
            return self.health
        return super().get(url, **kw)


@pytest.fixture
def studio():
    fake = Studio()
    app = gate.create_app(KEY, "http://w", "http://studio", clock=lambda: NOW, post=fake.post, get=fake.get)
    return app.test_client(), fake


def voice_pass(v):
    return gate.sign(KEY, NOW + 3600, v)


def say(c, token, body):
    """A /tts call read to the end and closed, as gunicorn does: a streamed reply gives its slot back on close."""
    r = c.post(f"/t/{token}/tts", json=body)
    r.get_data()
    r.close()
    return r


def speaks(fake):
    return [kw["json"] for url, kw in fake.posts if url.endswith("/speak")]


def test_each_voice_pass_is_its_own_scope():
    passes = {v: voice_pass(v) for v in gate.VOICES}
    assert len(set(passes.values())) == len(gate.VOICES)
    assert {v: gate.valid(KEY, p, NOW) for v, p in passes.items()} == {v: v for v in gate.VOICES}
    assert gate.voice_of("listen") is None and gate.voice_of("skippy") == "skippy"
    sha = hashlib.sha256(b"test-home-token").hexdigest()
    key = hmac.new(sha.encode(), b"voice-gate-v1", hashlib.sha256).digest()
    want = hmac.new(key, f"voice-tts-skippy:{NOW + 60}".encode(), hashlib.sha256).hexdigest()[:32]
    assert gate.sign(key, NOW + 60, "skippy") == f"{NOW + 60}.{want}"            # pinned: portal/voice.py mirrors it


def test_a_stock_pass_speaks_in_the_stock_voice_and_never_in_a_cloned_one(studio):
    c, fake = studio
    r = say(c, voice_pass('stock'), {"text": " Hello there. "})
    assert r.status_code == 200 and r.data == b"mp3-onemp3-two" and r.headers["X-Voice"] == "stock"
    assert speaks(fake) == [{"text": "Hello there.", "voice": "stock"}]
    assert say(c, voice_pass('stock'), {"text": "hi", "voice": "skippy"}).status_code == 403
    assert say(c, listen(), {"text": "hi", "voice": "stock"}).status_code == 403     # a listen pass still only hears
    assert say(c, voice_pass('stock'), {"text": "hi", "voice": "kaelen"}).status_code == 400
    assert len(speaks(fake)) == 1


def test_the_captains_skippy_pass_is_the_local_clone_and_may_drop_to_stock(studio):
    c, fake = studio
    assert say(c, voice_pass('skippy'), {"text": "I am Skippy."}).headers["X-Voice"] == "skippy"
    assert say(c, voice_pass('skippy'), {"text": "I am Skippy.", "voice": "stock"}).headers["X-Voice"] == "stock"
    assert [s["voice"] for s in speaks(fake)] == ["skippy", "stock"]


def test_local_voices_are_cached_and_a_broken_render_is_not_kept():
    fake = Studio()
    app = gate.create_app(KEY, "http://w", "http://studio", clock=lambda: NOW, post=fake.post, get=fake.get)
    c = app.test_client()
    assert say(c, voice_pass('skippy'), {"text": "Remember me."}).status_code == 200
    again = say(c, voice_pass('skippy'), {"text": "Remember me."})
    assert again.headers.get("X-Voice-Cache") == "hit" and again.data == b"mp3-onemp3-two" and len(speaks(fake)) == 1
    assert say(c, voice_pass('stock'), {"text": "Remember me."}).headers.get("X-Voice-Cache") is None
    fake.speak = Stream(broken=True)
    say(c, voice_pass('skippy'), {"text": "Cut off."}).get_data()
    fake.speak = Stream()
    assert say(c, voice_pass('skippy'), {"text": "Cut off."}).headers.get("X-Voice-Cache") is None


def test_a_dead_or_busy_studio_is_a_503_and_every_slot_comes_back(studio):
    c, fake = studio
    fake.speak = Stream(503)
    assert say(c, voice_pass('stock'), {"text": "hi"}).status_code == 503 and fake.speak.closed
    fake.speak = Stream()
    for i in range(5):                                         # 3 slots: a leak would turn the fourth into a 429
        assert say(c, voice_pass('stock'), {"text": f"line {i}"}).status_code == 200
    # no studio: post that raises ConnectionError so the gate returns 503
    def boom(*a, **kw):
        raise gate.requests.ConnectionError("no studio")
    no_studio = gate.create_app(KEY, "http://w", "http://nowhere", clock=lambda: NOW, post=boom, get=fake.get).test_client()
    assert say(no_studio, voice_pass('stock'), {"text": "hi"}).status_code == 503


def test_health_says_which_voices_can_speak(studio):
    c, fake = studio
    assert c.get("/health").get_json()["voices"] == {"stock": True, "skippy": True}
    fake.health = Resp(200, {"voices": {"stock": True, "skippy": False}})
    assert c.get("/health").get_json()["voices"] == {"stock": True, "skippy": False}
    fake.health = Resp(500)
    assert c.get("/health").get_json()["voices"]["stock"] is False
