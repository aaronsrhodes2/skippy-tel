"""Shared fixtures: the gate wired to a FAKE Jellyfin (tests/fake_jellyfin.py), with a controllable clock. No real Jellyfin, no
network beyond 127.0.0.1, no containers.

Run this service's tests in their own pytest session (`python -m pytest services/video-gate/tests -q`): the music-gate has a
`gate.py` too, and two of them can't share one interpreter's module cache.
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import fake_jellyfin as fj                      # noqa: E402
import gate                                     # noqa: E402
import hls                                      # noqa: E402
from catalog import Library                     # noqa: E402
from images import ImageCache                   # noqa: E402
from jellyfin import Client                     # noqa: E402

from helpers import HOME_TOKEN, KEY, NOW      # noqa: E402


class Clock:
    def __init__(self, t=NOW):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class Stack:
    """The gate and everything it talks to, for one test."""

    def __init__(self, fake, tmp_path, clock, **opts):
        self.fake, self.clock = fake, clock
        self.client = Client(fake.url, fj.KEY, **opts.pop("client_opts", {}))
        self.library = Library(self.client, opts.pop("user", ""), clock=clock)
        self.stopped: list[str] = []
        self.sessions = hls.Sessions(self._stop, clock=clock, cap=opts.pop("cap", 2), idle_s=opts.pop("idle_s", 120.0))
        self.images = ImageCache(tmp_path / "img", limit=opts.pop("img_limit", 50 * 1024 * 1024))
        self.app = gate.create_app(self.library, self.client, KEY, clock=clock, sessions=self.sessions, images=self.images,
                                   max_streams=opts.pop("max_streams", 8), forbidden=(fj.KEY,))
        self.web = self.app.test_client()
        self.tok = gate.sign(KEY, NOW + 3600)
        self.base = f"/t/{self.tok}"

    def _stop(self, sid):                           # what the sessions do to Jellyfin: through the real client, and recorded
        self.stopped.append(sid)
        return self.client.stop_encoding(sid)

    def get(self, path, **kw):
        return self.web.get(f"{self.base}{path}", **kw)

    def pass_for(self, n):
        """A different but valid pass (another expiry): another viewer, as far as the gate can tell."""
        return gate.sign(KEY, NOW + 3600 + n)


@pytest.fixture
def fake():
    f = fj.FakeJellyfin()
    f.url = f.start()
    yield f
    f.stop()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_stack(tmp_path, clock):
    def make(fake, refresh=True, **opts):
        s = Stack(fake, tmp_path, clock, **opts)
        if refresh:
            assert s.library.refresh()
        return s
    return make


@pytest.fixture
def stack(fake, make_stack):
    return make_stack(fake)


@pytest.fixture
def live(stack):
    """The gate on a real socket (threaded dev server), for the tests that need a real client hanging up mid-stream."""
    import threading

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, stack.app, threaded=True)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    stack.port = srv.server_address[1]
    yield stack
    srv.shutdown()
