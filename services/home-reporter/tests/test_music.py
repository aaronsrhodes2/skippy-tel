"""The reporter's music job against a fake of ACE-Step's REST server: free the GPU first, one job
at a time, release -> poll -> download, hand the audio to the portal with the pairing token, and
report every kind of failure plainly."""
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import reporter


class Fake:
    """A tiny HTTP server on a free port; `handler(method, path, headers, body)` -> (status, headers, body)."""

    def __init__(self, handler):
        outer = self
        self.calls = []

        class H(BaseHTTPRequestHandler):
            disable_nagle_algorithm = True       # headers and body are two writes: without this each call waits ~40 ms for an ACK

            def _do(self, method):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.calls.append({"method": method, "path": self.path, "headers": dict(self.headers), "body": body})
                status, headers, payload = handler(method, self.path, self.headers, body)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = lambda self: self._do("GET")      # noqa: E731
            do_POST = lambda self: self._do("POST")    # noqa: E731
            do_PUT = lambda self: self._do("PUT")      # noqa: E731

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def _json(obj, status=200):
    return status, {"Content-Type": "application/json"}, json.dumps(obj).encode()


class FakeAceStep:
    """ACE-Step's server: /release_task -> task_id, /query_result runs `polls_until_done` times then
    succeeds (or fails), /v1/audio serves the mp3. Tracks how many songs are in flight at once."""

    def __init__(self, polls_until_done=2, fail_with=None, file="/v1/audio?path=%2Ftmp%2Fapi_audio%2Fabc.mp3",
                 audio=b"ID3fake-audio", refuse=False):
        self.polls, self.fail_with, self.file, self.audio, self.refuse = polls_until_done, fail_with, file, audio, refuse
        self.seen_polls, self.active, self.peak, self.lock = {}, 0, 0, threading.Lock()
        self.fake = Fake(self.handle)

    def handle(self, method, path, headers, body):
        if path == "/health":
            return _json({"data": {"status": "ok"}, "code": 200})
        if path == "/release_task":
            if self.refuse:
                return _json({"data": None, "code": 500, "error": "no"}, 500)
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
                task = f"task-{len(self.seen_polls)}"
                self.seen_polls[task] = 0
            return _json({"data": {"task_id": task, "status": "queued", "queue_position": 1}, "code": 200})
        if path == "/query_result":
            task = json.loads(body)["task_id_list"][0]
            self.seen_polls[task] += 1
            if self.seen_polls[task] <= self.polls:
                return _json({"data": [{"task_id": task, "result": json.dumps([{"status": 0}]), "status": 0}], "code": 200})
            with self.lock:
                self.active -= 1
            if self.fail_with:
                result = [{"file": "", "status": 2, "error": self.fail_with}]
                return _json({"data": [{"task_id": task, "result": json.dumps(result), "status": 2}], "code": 200})
            result = [{"file": self.file, "status": 1, "metas": {"duration": 30.4, "bpm": 80}}]
            return _json({"data": [{"task_id": task, "result": json.dumps(result), "status": 1}], "code": 200})
        if path.startswith("/v1/audio"):
            return 200, {"Content-Type": "audio/mpeg"}, self.audio
        return 404, {}, b"{}"


@pytest.fixture
def home(monkeypatch):
    """A fake ACE-Step + Ollama (one model loaded) + portal, wired into the reporter's settings."""
    monkeypatch.setattr(reporter, "MUSIC_POLL", 0.01)
    studio = FakeAceStep()
    ollama = Fake(lambda m, p, h, b: _json({"models": [{"name": "qwen3:8b"}]} if p == "/api/ps" else {"done": True}))
    portal = Fake(lambda m, p, h, b: _json({}))
    monkeypatch.setattr(reporter, "MUSIC", studio.fake.url)
    monkeypatch.setattr(reporter, "OLLAMA", ollama.url)
    monkeypatch.setattr(reporter, "PORTAL", portal.url)
    yield studio, ollama, portal
    for f in (studio.fake, ollama, portal):
        f.close()


JOB = {"id": "abc123", "kind": "make_music", "prompt": "dreamy lofi beat", "lyrics": "", "duration_s": 30}


def _release(studio):
    return json.loads(next(c for c in studio.fake.calls if c["path"] == "/release_task")["body"])


def test_a_song_is_made_polled_downloaded_uploaded_with_the_token_and_reported(home):
    studio, ollama, portal = home
    out = reporter.run_job(JOB, "secret-token")
    assert out == {"result": {"title": "dreamy lofi beat", "seconds": 30.4}}
    paths = [c["path"] for c in studio.fake.calls]
    assert paths[0] == "/release_task" and paths.count("/query_result") == 3 and paths[-1].startswith("/v1/audio?path=")
    (put,) = portal.calls
    assert put["method"] == "PUT" and put["path"] == "/home/media/abc123?title=dreamy%20lofi%20beat"
    assert put["headers"]["Authorization"] == "Bearer secret-token" and put["headers"]["Content-Type"] == "audio/mpeg"
    assert put["body"] == b"ID3fake-audio"


def test_the_task_asks_for_the_turbo_model_an_mp3_and_an_instrumental_when_there_are_no_lyrics(home):
    studio, *_ = home
    reporter.run_job(JOB, "t")
    ask = _release(studio)
    assert ask["prompt"] == "dreamy lofi beat" and ask["lyrics"] == "[Instrumental]"
    assert ask["audio_duration"] == 30 and ask["audio_format"] == "mp3" and ask["inference_steps"] == 8
    assert ask["model"] == "acestep-v15-turbo" and ask["batch_size"] == 1


def test_lyrics_are_passed_through(home):
    studio, *_ = home
    reporter.run_job({**JOB, "lyrics": "[verse]\nrain on the window"}, "t")
    assert _release(studio)["lyrics"] == "[verse]\nrain on the window"


def test_ollama_is_told_to_unload_its_models_before_the_song(home):
    studio, ollama, portal = home
    reporter.run_job(JOB, "t")
    unload = [c for c in ollama.calls if c["path"] == "/api/generate"]
    assert [json.loads(c["body"]) for c in unload] == [{"model": "qwen3:8b", "keep_alive": 0}]
    assert ollama.calls[0]["path"] == "/api/ps"                     # it looked first, then asked


def test_the_job_is_clamped_whatever_the_portal_sends(home):
    studio, *_ = home
    reporter.run_job({**JOB, "prompt": "p" * 900, "lyrics": "l" * 9000, "duration_s": 99999}, "t")
    ask = _release(studio)
    assert len(ask["prompt"]) == 500 and len(ask["lyrics"]) == 3000 and ask["audio_duration"] == 120
    studio.fake.calls.clear()
    reporter.run_job({**JOB, "duration_s": "banana"}, "t")
    assert _release(studio)["audio_duration"] == 45
    studio.fake.calls.clear()
    reporter.run_job({**JOB, "duration_s": 1}, "t")
    assert _release(studio)["audio_duration"] == 10


def test_a_bare_file_path_from_the_studio_is_url_encoded_for_the_download(home):
    studio, *_ = home
    studio.file = "/tmp/api audio/x&y.mp3"
    reporter.run_job(JOB, "t")
    assert studio.fake.calls[-1]["path"] == "/v1/audio?path=" + urllib.parse.quote("/tmp/api audio/x&y.mp3", safe="")


def test_only_one_gpu_job_runs_at_a_time(monkeypatch):
    monkeypatch.setattr(reporter, "MUSIC_POLL", 0.01)
    studio = FakeAceStep(polls_until_done=8)
    portal = Fake(lambda *a: _json({}))
    monkeypatch.setattr(reporter, "MUSIC", studio.fake.url)
    monkeypatch.setattr(reporter, "PORTAL", portal.url)
    monkeypatch.setattr(reporter, "OLLAMA", "http://127.0.0.1:9")    # nothing there: freeing the GPU must not matter
    threads = [threading.Thread(target=reporter.run_job, args=({**JOB, "id": f"j{i}"}, "t")) for i in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    studio.fake.close(), portal.close()
    assert studio.peak == 1 and len(studio.seen_polls) == 3


def test_failures_are_reported_plainly(monkeypatch, home):
    studio, ollama, portal = home
    monkeypatch.setattr(reporter, "MUSIC", "")
    assert reporter.run_job(JOB, "t") == {"error": "music isn't set up on skippy-pc"}
    monkeypatch.setattr(reporter, "MUSIC", "http://127.0.0.1:9")     # nothing listening
    assert reporter.run_job(JOB, "t")["error"] == "the music studio didn't take the job (no answer)"
    refuses = FakeAceStep(refuse=True)
    monkeypatch.setattr(reporter, "MUSIC", refuses.fake.url)
    assert reporter.run_job(JOB, "t")["error"].startswith("the music studio didn't take the job")
    refuses.fake.close()


def test_a_song_the_studio_fails_to_make_says_why(monkeypatch, home):
    failing = FakeAceStep(fail_with="CUDA out of memory")
    monkeypatch.setattr(reporter, "MUSIC", failing.fake.url)
    assert reporter.run_job(JOB, "t") == {"error": "CUDA out of memory"}
    failing.fake.close()


def test_a_song_that_never_finishes_times_out(monkeypatch, home):
    slow = FakeAceStep(polls_until_done=10_000)
    monkeypatch.setattr(reporter, "MUSIC", slow.fake.url)
    monkeypatch.setattr(reporter, "MUSIC_TIMEOUT", 0.2)
    assert reporter.run_job(JOB, "t")["error"].startswith("the song took longer than")
    slow.fake.close()


def test_a_portal_that_refuses_the_clip_is_an_error_not_a_lie(monkeypatch, home):
    studio, ollama, portal = home
    refuse = Fake(lambda *a: _json({}, 415))
    monkeypatch.setattr(reporter, "PORTAL", refuse.url)
    assert reporter.run_job(JOB, "t")["error"].startswith("couldn't hand the clip to the portal")
    refuse.close()


def test_an_oversized_clip_is_not_sent(monkeypatch, home):
    monkeypatch.setattr(reporter, "CLIP_MAX", 5)
    studio, ollama, portal = home
    assert reporter.run_job(JOB, "t") == {"error": "the clip came out too big to send"}
    assert portal.calls == []


def test_music_is_advertised_only_when_the_studio_answers(monkeypatch, home):
    music = lambda: reporter.capabilities()["make_music"]           # noqa: E731 (the workshop's entry has its own tests)
    assert music() == {"ready": True}
    monkeypatch.setattr(reporter, "MUSIC", "http://127.0.0.1:9")
    assert music() == {"ready": False}
    monkeypatch.setattr(reporter, "MUSIC", "")
    assert music() == {"ready": False}


def test_unknown_jobs_are_still_refused(monkeypatch):
    monkeypatch.setattr(reporter, "MENTAT", "")
    assert reporter.run_job({"kind": "format_c_drive"}, "t") == {"error": "unknown job 'format_c_drive'"}
