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
        self.stt = Resp(200, {"english_text": " hello there ", "detected_lang": "en"})
        self.tts = Resp(200, content=b"ID3audio")
        self.ollama = Resp(200, {"response": "Done: the pill is orange and the tests pass."})

    def post(self, url, **kw):
        self.posts.append((url, kw))
        return self.ollama if "/api/generate" in url else self.stt

    def get(self, url, **kw):
        self.gets.append((url, kw))
        return self.tts if "/tts/stream" in url else Resp(200, {})


@pytest.fixture
def stack():
    fake = Fake()
    app = gate.create_app(KEY, "http://w", "http://t", clock=lambda: NOW, post=fake.post, get=fake.get, ollama_url="http://o")
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


def test_the_voice_goes_through_the_polish_chain_and_a_failure_returns_the_raw_voice():
    fake = Fake()
    seen = []
    app = gate.create_app(KEY, "http://w", "http://t", clock=lambda: NOW, post=fake.post, get=fake.get, fx_chain="CHAIN",
                          polisher=lambda audio, chain: seen.append((audio, chain)) or b"POLISHED")
    r = app.test_client().post(f"/t/{speak()}/tts", json={"text": "hi"})
    assert r.data == b"POLISHED" and seen == [(b"ID3audio", "CHAIN")]
    plain = gate.create_app(KEY, "http://w", "http://t", clock=lambda: NOW, post=fake.post, get=fake.get)          # no chain: untouched
    assert plain.test_client().post(f"/t/{speak()}/tts", json={"text": "hi"}).data == b"ID3audio"


def test_polish_hands_ffmpeg_the_chain_and_never_loses_the_voice():
    class P:
        def __init__(self, code=0, out=b"x" * 2000): self.returncode, self.stdout = code, out
    calls = []
    run = lambda argv, **kw: calls.append((argv, kw)) or P()                                                        # noqa: E731
    assert gate.polish(b"raw", "afftdn", run=run) == b"x" * 2000
    argv, kw = calls[0]
    assert argv[0] == "ffmpeg" and "afftdn" in argv and "pipe:0" in argv and kw["input"] == b"raw" and "shell" not in kw
    assert gate.polish(b"raw", "", run=run) == b"raw"                                                               # no chain: nothing runs
    assert gate.polish(b"raw", "c", run=lambda a, **k: P(1)) == b"raw"                                              # ffmpeg failed
    assert gate.polish(b"raw", "c", run=lambda a, **k: P(0, b"tiny")) == b"raw"                                     # it produced nothing usable
    def missing(a, **k): raise FileNotFoundError("ffmpeg")
    def slow(a, **k): raise subprocess.TimeoutExpired("ffmpeg", 1)
    assert gate.polish(b"raw", "c", run=missing) == b"raw" and gate.polish(b"raw", "c", run=slow) == b"raw"


def tts_calls(fake):
    return [g for g in fake.gets if "/tts/stream" in g[0]]


def test_the_same_words_are_never_paid_for_twice():
    fake = Fake()
    app = gate.create_app(KEY, "http://w", "http://t", clock=lambda: NOW, post=fake.post, get=fake.get, daily_chars=1000)
    c = app.test_client()
    first = c.post(f"/t/{speak()}/tts", json={"text": "I am Skippy."})
    again = c.post(f"/t/{speak()}/tts", json={"text": " I am Skippy. "})
    assert first.data == again.data == b"ID3audio" and again.headers["X-Voice-Cache"] == "hit"
    assert len(tts_calls(fake)) == 1 and c.get("/health").get_json()["voice_chars_today"] == len("I am Skippy.")


def test_a_spent_daily_budget_answers_429_with_a_flag_so_the_page_uses_the_device_voice_and_tomorrow_it_is_back():
    fake = Fake()
    now = [NOW]
    app = gate.create_app(KEY, "http://w", "http://t", clock=lambda: now[0], post=fake.post, get=fake.get, daily_chars=30)
    c = app.test_client()
    sp = lambda: gate.sign(KEY, now[0] + 3600, "speak")                                                          # noqa: E731
    assert c.post(f"/t/{sp()}/tts", json={"text": "Twenty characters ok"}).status_code == 200
    r = c.post(f"/t/{sp()}/tts", json={"text": "This will not fit today."})
    assert r.status_code == 429 and r.get_json()["budget"] is True and r.headers["Retry-After"]
    assert len(tts_calls(fake)) == 1                                                                             # ElevenLabs was not called
    assert c.post(f"/t/{sp()}/tts", json={"text": "Twenty characters ok"}).status_code == 200                   # but what was said before is still free
    now[0] += 24 * 3600
    assert c.post(f"/t/{sp()}/tts", json={"text": "This will not fit today."}).status_code == 200             # a new day


def test_the_budget_survives_a_restart_and_the_cache_is_bounded(tmp_path):
    fake = Fake()
    mk = lambda: gate.create_app(KEY, "http://w", "http://t", clock=lambda: NOW, post=fake.post, get=fake.get, state_dir=str(tmp_path), daily_chars=100)   # noqa: E731
    mk().test_client().post(f"/t/{speak()}/tts", json={"text": "Remember me."})
    assert mk().test_client().get("/health").get_json()["voice_chars_today"] == len("Remember me.")
    v = gate.Voice(str(tmp_path), 0, lambda: NOW, cache_keep=3)
    for i in range(8):
        v.remember(f"line {i}", b"x")
    assert len(list((tmp_path / "tts-cache").glob("*.mp3"))) == 3


def test_the_delivery_style_tag_goes_in_front_unless_the_text_brings_its_own():
    fake = Fake()
    app = gate.create_app(KEY, "http://w", "http://t", clock=lambda: NOW, post=fake.post, get=fake.get, style="[whispers]")
    c = app.test_client()
    c.post(f"/t/{speak()}/tts", json={"text": " Hello there "})
    c.post(f"/t/{speak()}/tts", json={"text": "[sarcastic] Oh, wonderful."})
    tts_calls = [g[1]["params"]["text"] for g in fake.gets if "/tts/stream" in g[0]]
    assert tts_calls == ["[whispers] Hello there", "[sarcastic] Oh, wonderful."]


def test_the_polish_chain_uses_rnnoise_when_its_model_is_there_and_never_a_gate():
    with_model = gate.fx_chain_for("/m/bd.rnnn", 1, exists=lambda p: True)
    assert with_model.count("arnndn=m=/m/bd.rnnn") == 1 and with_model.startswith("highpass=f=70,") and "equalizer=f=140" in with_model
    assert gate.fx_chain_for("/m/bd.rnnn", 2, exists=lambda p: True).count("arnndn") == 2                      # the stronger setting
    assert gate.fx_chain_for("/m/bd.rnnn", 9, exists=lambda p: True).count("arnndn") == 2                      # never more than two passes
    without = gate.fx_chain_for("/m/bd.rnnn", 1, exists=lambda p: False)
    assert "arnndn" not in without and without.startswith("highpass=f=70")                                    # no model mounted: still safe
    for chain in (with_model, without):
        for banned in ("agate", "loudnorm", "acompressor"):                                                     # measured: each made the flare or the floor worse
            assert banned not in chain


def ollama_calls(fake):
    return [c for c in fake.posts if "/api/generate" in c[0]]


def test_a_long_reply_becomes_a_few_spoken_sentences_for_any_pass(stack):
    c, fake = stack
    for token in (listen(), speak()):                                      # the Captain's pass and everyone else's
        r = c.post(f"/t/{token}/summary", json={"text": "## Done\n\nI changed `portal/app.py`, ran the tests and deployed. " * 20})
        assert r.status_code == 200 and r.get_json() == {"text": "Done: the pill is orange and the tests pass."}
    body = ollama_calls(fake)[0][1]["json"]
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
    assert not ollama_calls(fake)
    fake.ollama = Resp(500)
    assert c.post(f"/t/{listen()}/summary", json={"text": "hi"}).status_code == 503
    fake.ollama = Resp(200, {"response": "   "})
    assert c.post(f"/t/{listen()}/summary", json={"text": "hi"}).status_code == 502                  # nothing to say: the client falls back
    fake.ollama = Resp(200, None)
    assert c.post(f"/t/{listen()}/summary", json={"text": "hi"}).status_code == 502


def test_no_ollama_configured_means_no_summary_not_a_crash():
    fake = Fake()
    app = gate.create_app(KEY, "http://w", "http://t", clock=lambda: NOW, post=fake.post, get=fake.get)    # no ollama_url
    assert app.test_client().post(f"/t/{listen()}/summary", json={"text": "hi"}).status_code == 503


# --- the voice a pass names: stock (Kokoro) for everyone, skippy (F5, local) and elevenlabs for the Captain ---------------------------

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
    app = gate.create_app(KEY, "http://w", "http://t", clock=lambda: NOW, post=fake.post, get=fake.get, studio_url="http://studio")
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


def test_each_voice_pass_is_its_own_scope_and_the_older_passes_keep_their_meaning():
    passes = {v: voice_pass(v) for v in gate.VOICES}
    assert len(set(passes.values()) | {listen(), speak()}) == 5
    assert {v: gate.valid(KEY, p, NOW) for v, p in passes.items()} == {v: v for v in gate.VOICES}
    assert gate.voice_of("listen") is None and gate.voice_of("speak") == "elevenlabs" and gate.voice_of("skippy") == "skippy"
    sha = hashlib.sha256(b"test-home-token").hexdigest()
    key = hmac.new(sha.encode(), b"voice-gate-v1", hashlib.sha256).digest()
    want = hmac.new(key, f"voice-tts-skippy:{NOW + 60}".encode(), hashlib.sha256).hexdigest()[:32]
    assert gate.sign(key, NOW + 60, "skippy") == f"{NOW + 60}.{want}"            # pinned: portal/voice.py mirrors it


def test_a_stock_pass_speaks_in_the_stock_voice_and_never_in_a_cloned_one(studio):
    c, fake = studio
    r = say(c, voice_pass('stock'), {"text": " Hello there. "})
    assert r.status_code == 200 and r.data == b"mp3-onemp3-two" and r.headers["X-Voice"] == "stock"
    assert speaks(fake) == [{"text": "Hello there.", "voice": "stock"}]
    for wanted in ("skippy", "elevenlabs"):
        assert say(c, voice_pass('stock'), {"text": "hi", "voice": wanted}).status_code == 403
    assert say(c, listen(), {"text": "hi", "voice": "stock"}).status_code == 403     # a listen pass still only hears
    assert say(c, voice_pass('stock'), {"text": "hi", "voice": "kaelen"}).status_code == 400
    assert len(speaks(fake)) == 1 and not tts_calls(fake)


def test_the_captains_skippy_pass_is_the_local_clone_and_may_drop_to_stock_but_not_up_to_elevenlabs(studio):
    c, fake = studio
    assert say(c, voice_pass('skippy'), {"text": "I am Skippy."}).headers["X-Voice"] == "skippy"
    assert say(c, voice_pass('skippy'), {"text": "I am Skippy.", "voice": "stock"}).headers["X-Voice"] == "stock"
    assert say(c, voice_pass('skippy'), {"text": "hi", "voice": "elevenlabs"}).status_code == 403
    assert [s["voice"] for s in speaks(fake)] == ["skippy", "stock"] and not tts_calls(fake)


def test_an_elevenlabs_pass_is_the_cloud_voice_with_its_tag_budget_and_polish_and_the_old_speak_pass_is_the_same(studio):
    c, fake = studio
    r = say(c, voice_pass('elevenlabs'), {"text": "Auto mode."})
    assert r.status_code == 200 and r.headers["X-Voice"] == "elevenlabs" and tts_calls(fake)[-1][1]["params"] == {"text": "Auto mode."}
    assert say(c, speak(), {"text": "Old pass."}).headers["X-Voice"] == "elevenlabs"
    assert say(c, speak(), {"text": "hi", "voice": "skippy"}).status_code == 403     # an Auto pass is not a Private one
    assert not speaks(fake)


def test_local_voices_cost_no_budget_are_cached_and_a_broken_render_is_not_kept():
    fake = Studio()
    app = gate.create_app(KEY, "http://w", "http://t", clock=lambda: NOW, post=fake.post, get=fake.get, studio_url="http://studio",
                          daily_chars=5)
    c = app.test_client()
    assert say(c, voice_pass('skippy'), {"text": "Far more than five characters."}).status_code == 200
    again = say(c, voice_pass('skippy'), {"text": "Far more than five characters."})
    assert again.headers.get("X-Voice-Cache") == "hit" and again.data == b"mp3-onemp3-two" and len(speaks(fake)) == 1
    assert say(c, voice_pass('stock'), {"text": "Far more than five characters."}).headers.get("X-Voice-Cache") is None
    assert c.get("/health").get_json()["voice_chars_today"] == 0
    fake.speak = Stream(broken=True)
    say(c, voice_pass('skippy'), {"text": "Cut off."}).get_data()
    fake.speak = Stream()
    assert say(c, voice_pass('skippy'), {"text": "Cut off."}).headers.get("X-Voice-Cache") is None


def test_a_dead_or_busy_studio_is_a_503_or_429_and_every_slot_comes_back(studio):
    c, fake = studio
    fake.speak = Stream(503)
    assert say(c, voice_pass('stock'), {"text": "hi"}).status_code == 503 and fake.speak.closed
    fake.speak = Stream()
    for i in range(5):                                         # 3 slots: a leak would turn the fourth into a 429
        assert say(c, voice_pass('stock'), {"text": f"line {i}"}).status_code == 200
    no_studio = gate.create_app(KEY, "http://w", "http://t", clock=lambda: NOW, post=fake.post, get=fake.get).test_client()
    assert say(no_studio, voice_pass('stock'), {"text": "hi"}).status_code == 503


def test_health_says_which_voices_can_speak(studio):
    c, fake = studio
    assert c.get("/health").get_json()["voices"] == {"stock": True, "skippy": True, "elevenlabs": True}
    fake.health = Resp(200, {"voices": {"stock": True, "skippy": False}})
    fake.tts = Resp(503)
    assert c.get("/health").get_json()["voices"] == {"stock": True, "skippy": False, "elevenlabs": True}  # flask-api probe is /tts/health
    fake.health = Resp(500)
    assert c.get("/health").get_json()["voices"]["stock"] is False
