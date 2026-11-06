"""The voice gate adds no wait of its own to a line that had to be rendered ("streamline it so it becomes snappy like Amazon Echo", 2026-11-05).

Measured on the live gate: the cache directory is a Windows bind mount where one stat() costs ~0.85 ms, and the old store stat()ed all 300 cached files after
every render to find the oldest, before the first byte of the line went out: 319 ms median (258 to 444 ms). These tests pin that the bookkeeping is off the
reply's path and does not scale with the size of the cache, with the order and the limit of the cache unchanged."""
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gate  # noqa: E402

NOW = 1_800_000_000
KEY = gate.gate_key("test-home-token")


def settle(voice, seconds=10):
    """Until the background stores are on disk (the old store was synchronous: nothing to wait for, so these tests can also run against it)."""
    return voice.wait(seconds) if hasattr(voice, "wait") else True


def fill(folder, n, start=0):
    names = [f"{i:032x}.mp3" for i in range(start, start + n)]
    for i, name in enumerate(names):
        p = folder / name
        p.write_bytes(b"x")
        os.utime(p, (1_000_000 + i * 10, 1_000_000 + i * 10))                 # ages apart: the first is the oldest
    return names


def slow_stat(real, seconds, folder):
    """os.stat that costs `seconds` for anything inside `folder`: the live mount's price, made big enough to measure on any machine."""
    def stat(path, *a, **k):
        if str(path).startswith(str(folder)):
            time.sleep(seconds)
        return real(path, *a, **k)
    return stat


def test_speedgate_storing_a_line_does_not_wait_for_the_cache_to_be_bookkept(tmp_path, monkeypatch):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=40)
    folder = tmp_path / "tts-cache"
    fill(folder, 40)                                                          # a cache at its cap, as the live one always is
    monkeypatch.setattr(os, "stat", slow_stat(os.stat, 0.01, folder))        # 10 ms a stat: the old store (41 stats) held the reply 0.4 s
    started = time.perf_counter()
    voice.remember("skippy|Hello there.", b"mp3-audio")
    took = time.perf_counter() - started
    assert took < 0.1, f"the reply was held back {took:.2f}s for the cache"
    assert settle(voice)
    monkeypatch.undo()
    assert len(list(folder.glob("*.mp3"))) == 40                              # one in, the oldest out
    assert voice.cached("skippy|Hello there.") == b"mp3-audio"


def test_speedgate_the_cache_is_not_stat_ed_file_by_file_on_every_store(tmp_path, monkeypatch):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=30)
    folder = tmp_path / "tts-cache"
    fill(folder, 30)
    seen = []
    real = os.stat

    def counting(path, *a, **k):
        if str(path).startswith(str(folder)):
            seen.append(str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(os, "stat", counting)
    for n in range(5):
        voice.remember(f"skippy|line {n}", b"audio")
        assert settle(voice)
    assert len(seen) < 10, f"{len(seen)} stat() calls for 5 stores of a 30-file cache (the old store made {5 * 31})"


def test_speedgate_a_line_is_a_hit_the_moment_it_is_stored_even_while_the_disk_is_slow(tmp_path, monkeypatch):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=10)
    real = Path.write_bytes
    gate_open = threading.Event()

    def stalled(self, data):
        if str(self).startswith(str(tmp_path)):
            gate_open.wait(5)                                                  # the disk stalls (D: busy)
        return real(self, data)
    monkeypatch.setattr(Path, "write_bytes", stalled)
    started = time.perf_counter()
    voice.remember("skippy|Same words.", b"mp3-audio")
    assert time.perf_counter() - started < 0.5                                 # not held for the stalled write
    assert voice.cached("skippy|Same words.") == b"mp3-audio"                  # the repeat is served from memory at once
    assert not list((tmp_path / "tts-cache").glob("*.mp3"))                    # (and it really is not on disk yet)
    gate_open.set()
    assert settle(voice)
    monkeypatch.undo()
    assert voice.cached("skippy|Same words.") == b"mp3-audio" and len(list((tmp_path / "tts-cache").glob("*.mp3"))) == 1


def test_speedgate_the_least_recently_used_line_goes_first_and_a_hit_keeps_a_line(tmp_path):
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=3)
    for word in ("a", "b", "c"):
        voice.remember(f"skippy|{word}", word.encode())
        assert settle(voice)
    assert voice.cached("skippy|a") == b"a"                                    # a is used again: b is the oldest now
    voice.remember("skippy|d", b"d")
    assert settle(voice)
    assert voice.cached("skippy|b") is None
    assert [voice.cached(f"skippy|{w}") for w in "acd"] == [b"a", b"c", b"d"]
    assert len(list((tmp_path / "tts-cache").glob("*.mp3"))) == 3


def test_speedgate_a_restart_rebuilds_the_order_from_the_file_times(tmp_path):
    folder = tmp_path / "tts-cache"
    folder.mkdir()
    names = fill(folder, 5)
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=5)               # a fresh process: nothing known yet
    voice.remember("skippy|newcomer", b"audio")
    assert settle(voice)
    left = {p.name for p in folder.glob("*.mp3")}
    assert names[0] not in left and set(names[1:]) <= left and len(left) == 5


def test_speedgate_a_half_written_file_is_never_a_line_and_an_old_leftover_is_swept(tmp_path):
    folder = tmp_path / "tts-cache"
    folder.mkdir()
    stale, fresh = folder / ("0" * 32 + ".mp3.tmp"), folder / ("1" * 32 + ".mp3.tmp")
    stale.write_bytes(b"half")
    fresh.write_bytes(b"half")
    os.utime(stale, (1_000_000, 1_000_000))
    voice = gate.Voice(str(tmp_path), time.time, cache_keep=5)
    voice.remember("skippy|whole", b"audio")
    assert settle(voice)
    assert not stale.exists() and fresh.exists()                               # a crash's leftover goes; a store in flight is left alone
    assert voice.cached("skippy|whole") == b"audio" and voice.cached("skippy|" + "x") is None
    assert not list(folder.glob("*.mp3.tmp.mp3"))


class Piece:
    status_code = 200

    def __init__(self, parts):
        self.parts, self.closed = parts, False

    def iter_content(self, chunk_size=None):
        yield from self.parts

    def close(self):
        self.closed = True


def test_speedgate_a_rendered_line_goes_out_at_once_and_the_same_words_are_a_hit_next(tmp_path, monkeypatch):
    posts = []

    def post(url, **kw):
        posts.append(kw["json"])
        return Piece((b"mp3-one", b"mp3-two"))

    def get(url, **kw):
        raise AssertionError("no probe is needed")
    app = gate.create_app(KEY, "http://w", "http://studio", clock=lambda: NOW, post=post, get=get, state_dir=str(tmp_path))
    folder = tmp_path / "tts-cache"
    fill(folder, 30)
    voice_cache = app.extensions.get("voice_cache")                           # (the old gate kept it private: the same test then runs on its 300-file cap)
    if voice_cache is not None:
        voice_cache.keep = 30
    monkeypatch.setattr(os, "stat", slow_stat(os.stat, 0.01, folder))
    c = app.test_client()
    token = gate.sign(KEY, NOW + 3600, "skippy")
    started = time.perf_counter()
    first = c.post(f"/t/{token}/tts", json={"text": "I am Skippy."})
    body = first.get_data()
    first.close()
    took = time.perf_counter() - started
    assert first.status_code == 200 and body == b"mp3-onemp3-two"
    assert took < 0.15, f"the reply took {took:.2f}s (the cache bookkeeping used to add 0.3 s on the live mount)"
    again = c.post(f"/t/{token}/tts", json={"text": " I am Skippy. "})
    assert again.headers["X-Voice-Cache"] == "hit" and again.get_data() == body and len(posts) == 1
    assert settle(voice_cache)


def test_speedgate_whole_is_sent_on_only_when_asked_and_a_default_can_be_set():
    posts = []

    def post(url, **kw):
        posts.append(kw["json"])
        return Piece((b"mp3",))

    def make(**kw):
        return gate.create_app(KEY, "http://w", "http://studio", clock=lambda: NOW, post=post, get=lambda *a, **k: None, **kw).test_client()

    token = gate.sign(KEY, NOW + 3600, "skippy")

    def say(c, text, **body):
        r = c.post(f"/t/{token}/tts", json={"text": text, **body})
        r.get_data()
        r.close()
        return r

    c = make()
    say(c, "One.")
    assert posts[-1] == {"text": "One.", "voice": "skippy"}                       # nothing changes unless a caller asks
    say(c, "Two.", whole=True)
    assert posts[-1] == {"text": "Two.", "voice": "skippy", "whole": True}
    say(c, "Three.", whole="yes")
    assert posts[-1] == {"text": "Three.", "voice": "skippy"}                     # only a real true asks
    d = make(whole_default=True)                                                  # TTS_WHOLE=1 in the gate's environment
    say(d, "Four.")
    assert posts[-1] == {"text": "Four.", "voice": "skippy", "whole": True}
    say(d, "Five.", whole=False)
    assert posts[-1] == {"text": "Five.", "voice": "skippy"}                      # a caller can still say no
